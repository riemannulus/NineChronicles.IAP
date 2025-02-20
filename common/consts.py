import os

HOST_LIST = {
    "development": [
        os.environ.get("HEADLESS", "http://localhost")
    ],
    "internal": [
        "https://9c-internal-rpc-1.nine-chronicles.com",
    ],
    "mainnet": [
        "https://9c-main-full-state.nine-chronicles.com",
        "https://9c-main-rpc-1.nine-chronicles.com",
        "https://9c-main-rpc-2.nine-chronicles.com",
        "https://9c-main-rpc-3.nine-chronicles.com",
        # "https://9c-main-rpc-4.nine-chronicles.com",
        # "https://9c-main-rpc-5.nine-chronicles.com",
    ],
}

CURRENCY_LIST = ("NCG", "CRYSTAL", "GARAGE")

AVATAR_BOUND_TICKER = tuple()
