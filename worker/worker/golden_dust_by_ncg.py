import concurrent.futures
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Union, List, Optional

import requests
from gql.dsl import dsl_gql, DSLQuery

from common._crypto import Account
from common._graphql import GQL
from common.utils.aws import fetch_kms_key_id, fetch_parameter
from common.utils.google import Spreadsheet

GOOGLE_CREDENTIAL = fetch_parameter(
    os.environ.get("REGION_NAME"),
    f"{os.environ.get('STAGE')}_9c_IAP_GOOGLE_CREDENTIAL",
    True
)["Value"]

AUTHORIZED_RECIPIENT = "0xE8D6c4b15269754fE7b26DA243052ECD2a88db07"
NCG_TRANSFER_UNIT = 35
GOLDEN_DUST_SET = 20
GOLDEN_DUST_FUNGIBLE_ID = "f8faf92c9c0d0e8e06694361ea87bfc8b29a8ae8de93044b98470a57636ed0e0"

FORM_SHEET = os.environ.get("FORM_SHEET")
WORK_SHEET = f"Worksheet_{os.environ.get('STAGE')}"

NCG_COL = "E"
TOKEN_COL = "I"
TX_HASH_COL = "K"
TX_STATUS_COL = "L"
BLOCK_INDEX_COL = "M"
COMMENT_COL = "O"
NONCE_COL = "P"
PLAIN_VALUE_COL = "Q"

TX_QUERY = """{{
  stateQuery {{
    agent(address: "{agent_addr}") {{
      avatarStates {{ address }}
    }}
  }}
  transaction {{
    getTx(txId: "{tx_hash}")  {{
      signer
      actions {{
        json
      }}
    }}
    transactionResult(txId: "{tx_hash}") {{
      txStatus
      exceptionNames
    }}
  }}
}}"""

UNLOAD_QUERY = """{{
  actionTxQuery (
    publicKey: "{public_key}",
    nonce: {nonce}
  ) {{
    unloadFromMyGarages(
      recipientAvatarAddr: "{avatar_addr}",
      fungibleAssetValues: [],
      fungibleIdAndCounts: [
        {
          fungibleId: "{golden_dust_fungible_id}",
          count: {amount}
        }
      ]
    )
  }}
}}"""


class WorkStatus(Enum):
    REQ = "Requested"
    ACK = "Acknowledged"
    VALID = "Valid"
    INVALID = "Invalid"
    INVALID_CAN_REFUND = "Invalid - Can Refund"
    INVALID_CANNOT_REFUND = "Invalid - Cannot Refund"


class TxStatus(Enum):
    NOT_CREATED = "Not Created"
    NOT_FOUND = "Not Found"
    STAGING = "Staging"
    SUCCESS = "Success"
    INVALID = "Invalid"
    FAILURE = "Failure"


@dataclass
class TxData:
    tx_hash: str
    avatar_list: List[str] = field(default_factory=list)
    signer: str = ""
    amount: Optional[float] = None
    tx_status: TxStatus = TxStatus.NOT_FOUND
    comment: List[str] = field(default_factory=list)


@dataclass
class WorkData:
    agent_addr: str
    avatar_addr: str
    request_tx_hash: str
    request_dust_set: Union[str, int]
    email: str
    token: str
    request_duplicated: str = ""
    request_tx_status: Union[str, TxStatus] = "Not Found"
    sent_ncg: Optional[float] = None
    status: WorkStatus = WorkStatus.REQ
    tx_hash: str = ""
    tx_status: TxStatus = TxStatus.NOT_CREATED
    block_index: int = 0
    timestamp: datetime = None
    comment: List[str] = field(default_factory=list)
    nonce: Optional[int] = None
    plain_text: Optional[str] = ""

    def __post_init__(self):
        self.request_tx_status = TxStatus(self.request_tx_status)
        self.request_dust_set = int(self.request_dust_set)

    @classmethod
    def from_request(cls, req: List):
        return cls(
            agent_addr=req[0], avatar_addr=req[1],
            request_tx_hash=req[3], request_dust_set=req[5],
            email=req[6], token=req[11],
        )

    @property
    def values(self):
        return [
            self.agent_addr, self.avatar_addr, self.request_tx_hash, self.request_tx_status.value,
            self.request_duplicated, self.sent_ncg, self.request_dust_set,
            self.email, self.token, self.status.value, self.tx_hash, self.tx_status.value, self.block_index or "",
            (self.timestamp or datetime.now()).isoformat(), "\n".join(self.comment),
            self.nonce or "", self.plain_text,
        ]


def handle_avatar(data: dict, result: TxData) -> TxData:
    if "agent" not in data:
        result.comment.append("No agent data found from GQL")
        return result

    agent_data = data["agent"]
    if agent_data is None or "avatarStates" not in agent_data:
        result.comment.append("No avatar data found in agent")
        return result

    result.avatar_list = [x["address"].lower() for x in agent_data["avatarStates"]]
    return result


def handle_tx_detail(data: Optional[dict], result: TxData) -> TxData:
    if data is None:
        result.comment.append("No Tx. detail found from GQL")
        return result

    result.signer = data["signer"]
    action = json.loads(data["actions"][0]["json"].replace("\\uFEFF", ""))

    if not action["type_id"].startswith("transfer_asset"):
        result.comment.append(f"{action['type_id']} is not valid action")
        return result

    values = action["values"]

    fav_data, amount = values["amount"]
    if not (fav_data["ticker"] == "NCG" and fav_data["decimalPlaces"] == "0x02" and
            fav_data["minters"] == ["0x47d082a115c63e7b58b1532d20e631538eafadde"]):
        result.comment.append(f"Transferred non-real NCG")
        return result

    if values["recipient"].lower() != AUTHORIZED_RECIPIENT.lower():
        result.comment.append(f"{values['recipient']} is not the right recipient address.")

    if values["sender"].lower() != data["signer"].lower():
        result.comment.append(f"Sender {values['sender']} is not matched to signer {data['signer']}")

    result.amount = float(amount) / 100

    return result


def handle_tx_result(data: dict, result: TxData) -> TxData:
    result.tx_status = TxStatus[data["txStatus"]]
    if data.get("exceptionNames") is not None:
        result.comment.extend(data["exceptionNames"])

    return result


def get_tx_result(agent_addr: str, tx_hash: str) -> TxData:
    result = TxData(tx_hash)

    resp = requests.post(f"{os.environ.get('HEADLESS')}/graphql",
                         json={"query": TX_QUERY.format(agent_addr=agent_addr, tx_hash=tx_hash)})

    if resp.status_code != 200:
        result.comment.append("Failed to get Tx. status through GQL")
        return result

    data = resp.json()
    if "data" not in data:
        result.comment.append("No right data returned from GQL")
        return result

    data = data["data"]
    if data is None:
        result.comment.append("No data found from GQL")
        return result

    # avatar list in agent
    result = handle_avatar(data.get("stateQuery", {}), result)

    # Transaction
    if "transaction" not in data:
        result.comment.append("No right Tx. data found from GQL")
        return result

    data = data["transaction"]
    result = handle_tx_detail(data.get("getTx"), result)
    result = handle_tx_result(data.get("transactionResult"), result)

    return result


def handle_request(event, context):
    account = Account(fetch_kms_key_id(os.environ.get("STAGE"), os.environ.get("REGION_NAME")))
    form_sheet = Spreadsheet(GOOGLE_CREDENTIAL, os.environ.get("GOLDEN_DUST_REQUEST_SHEET_ID"))
    work_sheet = Spreadsheet(GOOGLE_CREDENTIAL, os.environ.get("GOLDEN_DUST_WORK_SHEET_ID"))
    gql = GQL()
    # Get prev. data
    prev_tokens = set()
    prev_treated = set()
    prev_data = work_sheet.get_values(f"{WORK_SHEET}!C2:{TX_STATUS_COL}").get("values", [])
    for prev in prev_data:
        prev_tokens.add(prev[6])
        prev_treated.add(prev[0].lower())

    # Get form data and filter new
    form_data = [x for x in form_sheet.get_values(f"{FORM_SHEET}!A2:L").get("values", []) if x[-1] not in prev_tokens]
    request_data = [WorkData.from_request(x) for x in form_data]

    print(f"{len(request_data)} requests to treat.")
    # Get Tx. data and validate
    valid_request = set()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {}
        for req in request_data:
            if req.request_tx_hash.lower() in prev_treated:
                req.comment.append(f"Tx {req.request_tx_hash} is already treated.")
                req.request_duplicated = True
                req.status = WorkStatus.INVALID_CANNOT_REFUND
            else:
                futures[executor.submit(get_tx_result, req.agent_addr, req.request_tx_hash)] = req

        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            req = futures[future]
            tx_data = future.result()
            req.sent_ncg = tx_data.amount
            req.request_tx_status = tx_data.tx_status
            req.comment.extend(tx_data.comment)

            # Validate
            if req.agent_addr.lower() != tx_data.signer.lower():
                req.comment.append(f"{req.agent_addr} is not matched with Tx. Signer {tx_data.signer}")
                req.status = WorkStatus.INVALID_CAN_REFUND

            if req.avatar_addr.lower() not in tx_data.avatar_list:
                req.comment.append(f"{req.avatar_addr} is not an avatar of agent {req.agent_addr}")
                req.status = WorkStatus.INVALID_CAN_REFUND

            if tx_data.amount is None:
                req.comment.append("No transferred NCG")
                req.status = WorkStatus.INVALID_CANNOT_REFUND
            elif tx_data.amount <= 0:
                req.comment.append(f"{tx_data.amount} is not valid amount")
                req.status = WorkStatus.INVALID_CANNOT_REFUND
            elif tx_data.amount % NCG_TRANSFER_UNIT != 0:
                req.comment.append(f"{tx_data.amount} is not divided by {NCG_TRANSFER_UNIT}")
                req.status = WorkStatus.INVALID_CAN_REFUND
            elif tx_data.amount and tx_data.amount // NCG_TRANSFER_UNIT != req.request_dust_set:
                req.comment.append(
                    f"Requested {req.request_dust_set} is not match to sent NCG {tx_data.amount} for {tx_data.amount // NCG_TRANSFER_UNIT} set")
                req.status = WorkStatus.INVALID_CAN_REFUND

            if not req.comment:
                req.status = WorkStatus.VALID
                if req.request_tx_hash in valid_request:
                    req.status = WorkStatus.INVALID_CANNOT_REFUND
                    req.comment.append(f"Tx {req.request_tx_hash} is duplicated")
                    req.request_duplicated = "Duplicated"
                else:
                    valid_request.add(req.request_tx_hash)
            elif req.status not in (
                    WorkStatus.INVALID, WorkStatus.INVALID_CANNOT_REFUND, WorkStatus.INVALID_CAN_REFUND
            ):
                # This should be re-checked
                req.status = WorkStatus.INVALID

            print(f"{i + 1} / {len(futures)} checked")

    # Send Golden Dust
    nonce = gql.get_next_nonce(account.address)
    for i, req in enumerate(request_data):
        if req.status != WorkStatus.VALID:
            work_sheet.set_values(f"{WORK_SHEET}!A{len(prev_data) + 2 + i}:{PLAIN_VALUE_COL}", [req.values])
            print(f"{i + 1} / {len(request_data)} is invalid. Skip.")
            continue

        unsigned_tx = gql.create_action("unload_from_garage", pubkey=account.pubkey, nonce=nonce,
                                        fav_data=[], avatar_addr=req.avatar_addr,
                                        item_data=[{"fungibleId": GOLDEN_DUST_FUNGIBLE_ID,
                                                    "count": req.request_dust_set * GOLDEN_DUST_SET}]
                                        )
        signature = account.sign_tx(unsigned_tx)
        signed_tx = gql.sign(unsigned_tx, signature)
        success, msg, tx_id = gql.stage(signed_tx)
        req.nonce = nonce
        req.plain_text = unsigned_tx.hex()
        if success:
            nonce += 1
            req.tx_status = TxStatus.STAGING
            req.tx_hash = tx_id
        else:
            req.tx_status = TxStatus.NOT_CREATED
            req.comment.append(msg)

        print(f"{i + 1} / {len(request_data)} treated with nonce {nonce - 1}")
        work_sheet.set_values(f"{WORK_SHEET}!A{len(prev_data) + 2 + i}:{PLAIN_VALUE_COL}", [req.values])

    # Write result
    # work_sheet.set_values(f"{WORK_SHEET}!A{len(prev_data) + 2}:{PLAIN_VALUE_COL}", [req.values for req in request_data])
    print("Work result recorded to worksheet.")


def track_tx(event, context):
    sheet = Spreadsheet(GOOGLE_CREDENTIAL, os.environ.get("GOLDEN_DUST_WORK_SHEET_ID"))
    tx_data = sheet.get_values(f"{WORK_SHEET}!{TX_HASH_COL}2:{COMMENT_COL}").get("values", [])
    client = GQL()
    for i, tx in enumerate(tx_data):
        if tx[1] != "Staging":
            print(f"{i + 1} / {len(tx_data)} : Invalid tx. status: {tx[1]}")
            continue
        query = dsl_gql(
            DSLQuery(
                client.ds.StandaloneQuery.transaction.select(
                    client.ds.TransactionHeadlessQuery.transactionResult.args(
                        txId=tx[0]
                    ).select(
                        client.ds.TxResultType.txStatus,
                        client.ds.TxResultType.blockIndex,
                        client.ds.TxResultType.blockHash,
                        client.ds.TxResultType.exceptionNames,
                    )
                )
            )
        )
        resp = client.execute(query)
        logging.debug(resp)

        if "errors" in resp:
            logging.error(f"GQL Failed to get tx result: {resp['errors']}")
            continue

        data = resp["transaction"]["transactionResult"]
        tx[1] = TxStatus[data["txStatus"]].value
        tx[2] = data["blockIndex"]
        if data.get("exceptionNames"):
            tx[-1] = json.dumps(data["exceptionNames"])
        sheet.set_values(f"{WORK_SHEET}!{TX_HASH_COL}{i + 2}:{COMMENT_COL}", [tx])
        print(f"{i + 1} / {len(tx_data)} updated.")


if __name__ == "__main__":
    # handle_request(None, None)
    # track_tx(None, None)
    pass
