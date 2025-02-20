import os

import aws_cdk as cdk_core
import boto3
from aws_cdk import (
    RemovalPolicy, Stack,
    aws_iam as _iam,
    aws_lambda as _lambda,
    aws_lambda_event_sources as _evt_src,
    aws_events as _events,
    aws_events_targets as _event_targets,
)
from constructs import Construct

from common import COMMON_LAMBDA_EXCLUDE, Config
from worker import WORKER_LAMBDA_EXCLUDE


class WorkerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        config: Config = kwargs.pop("config")
        shared_stack = kwargs.pop("shared_stack", None)
        if shared_stack is None:
            raise ValueError("Shared stack not found. Please provide shared stack.")
        super().__init__(scope, construct_id, **kwargs)

        # Lambda Layer
        layer = _lambda.LayerVersion(
            self, f"{config.stage}-9c-iap-worker-lambda-layer",
            code=_lambda.AssetCode("worker/layer/"),
            description="Lambda layer for 9c IAP Worker",
            compatible_runtimes=[
                _lambda.Runtime.PYTHON_3_10,
            ],
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Lambda Role
        role = _iam.Role(
            self, f"{config.stage}-9c-iap-worker-role",
            assumed_by=_iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                _iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
            ]
        )
        # DB Password
        role.add_to_policy(
            _iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[shared_stack.rds.secret.secret_arn],
            )
        )
        # KMS
        ssm = boto3.client("ssm", region_name=config.region_name,
                           aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                           aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                           )
        resp = ssm.get_parameter(Name=f"{config.stage}_9c_IAP_KMS_KEY_ID", WithDecryption=True)
        kms_key_id = resp["Parameter"]["Value"]
        role.add_to_policy(
            _iam.PolicyStatement(
                actions=["kms:GetPublicKey", "kms:Sign"],
                resources=[f"arn:aws:kms:{config.region_name}:{config.account_id}:key/{kms_key_id}"]
            )
        )
        role.add_to_policy(
            _iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    shared_stack.google_credential_arn,
                    shared_stack.apple_credential_arn,
                    shared_stack.kms_key_id_arn,
                ]
            )
        )

        # Environment variables
        # ssm = boto3.client("ssm", region_name="us-east-1")
        # Get env.variables from SSM by stage
        env = {
            "REGION_NAME": config.region_name,
            "STAGE": config.stage,
            "SECRET_ARN": shared_stack.rds.secret.secret_arn,
            "DB_URI": f"postgresql://"
                      f"{shared_stack.credentials.username}:[DB_PASSWORD]"
                      f"@{shared_stack.rds.db_instance_endpoint_address}"
                      f"/iap",
            "GOOGLE_PACKAGE_NAME": config.google_package_name,
            "HEADLESS": config.headless,
            "PLANET_URL": config.planet_url,
            "BRIDGE_DATA": config.bridge_data,
        }

        # Worker Lambda Function
        exclude_list = [".idea", ".gitignore", ]
        exclude_list.extend(COMMON_LAMBDA_EXCLUDE)
        exclude_list.extend(WORKER_LAMBDA_EXCLUDE)

        worker = _lambda.Function(
            self, f"{config.stage}-9c-iap-worker-function",
            function_name=f"{config.stage}-9c-iap-worker",
            runtime=_lambda.Runtime.PYTHON_3_10,
            description="9c Action making worker of NineChronicles.IAP",
            code=_lambda.AssetCode("worker/worker/", exclude=exclude_list),
            handler="handler.handle",
            layers=[layer],
            role=role,
            vpc=shared_stack.vpc,
            timeout=cdk_core.Duration.seconds(120),
            environment=env,
            events=[
                _evt_src.SqsEventSource(shared_stack.q)
            ],
            memory_size=256,
            reserved_concurrent_executions=1,
        )

        # Tracker Lambda Function
        tracker = _lambda.Function(
            self, f"{config.stage}-9c-iap-tracker-function",
            function_name=f"{config.stage}-9c-iap-tx-tracker",
            runtime=_lambda.Runtime.PYTHON_3_10,
            description="9c transaction status tracker of NineChronicles.IAP",
            code=_lambda.AssetCode("worker/worker/", exclude=exclude_list),
            handler="tracker.track_tx",
            layers=[layer],
            role=role,
            vpc=shared_stack.vpc,
            timeout=cdk_core.Duration.seconds(50),
            environment=env,
        )

        # Every minute
        minute_event_rule = _events.Rule(
            self, f"{config.stage}-9c-iap-tracker-event",
            schedule=_events.Schedule.cron(minute="*")  # Every minute
        )
        minute_event_rule.add_target(_event_targets.LambdaFunction(tracker))

        # Price updater Lambda function
        # NOTE: Price is directly fetched between client and google play.
        #  Not need to update price in IAP service.
        # updater = _lambda.Function(
        #     self, f"{config.stage}-9c-iap-price-updater-function",
        #     function_name=f"{config.stage}-9c-iap-price-updater",
        #     runtime=_lambda.Runtime.PYTHON_3_10,
        #     description="9c IAP price updater from google/apple store",
        #     code=_lambda.AssetCode("worker/worker", exclude=exclude_list),
        #     handler="updater.update_prices",
        #     layers=[layer],
        #     role=role,
        #     vpc=shared_stack.vpc,
        #     timeout=cdk_core.Duration.seconds(120),
        #     environment=env,
        #     memory_size=192,
        # )

        # Every hour
        # hourly_event_rule = _events.Rule(
        #     self, f"{config.stage}-9c-iap-price-updater-event",
        #     schedule=_events.Schedule.cron(minute="0")  # Every hour
        # )
        #
        # hourly_event_rule.add_target(_event_targets.LambdaFunction(updater))

        # IAP garage daily report
        env["IAP_GARAGE_WEBHOOK_URL"] = os.environ.get("IAP_GARAGE_WEBHOOK_URL")
        garage_report = _lambda.Function(
            self, f"{config.stage}-9c-iap-garage-report",
            function_name=f"{config.stage}_9c-iap-garage-reporter",
            runtime=_lambda.Runtime.PYTHON_3_10,
            description="Daily report of 9c IAP Garage item count",
            code=_lambda.AssetCode("worker/worker", exclude=exclude_list),
            handler="garage_noti.noti",
            layers=[layer],
            role=role,
            vpc=shared_stack.vpc,
            timeout=cdk_core.Duration.seconds(10),
            environment=env,
            memory_size=192,
        )

        # EveryDay 03:00 UTC == 12:00 KST
        if config.stage != "internal":
            everyday_event_rule = _events.Rule(
                self, f"{config.stage}-9c-iap-everyday-event",
                schedule=_events.Schedule.cron(hour="3", minute="0")  # Every day 00:00 ETC
            )
            everyday_event_rule.add_target(_event_targets.LambdaFunction(garage_report))

        # Golden dust by NCG handler
        env["GOLDEN_DUST_REQUEST_SHEET_ID"] = config.golden_dust_request_sheet_id
        env["GOLDEN_DUST_WORK_SHEET_ID"] = config.golden_dust_work_sheet_id
        env["FORM_SHEET"] = config.form_sheet
        gd_handler = _lambda.Function(
            self, f"{config.stage}-9c-iap-goldendust-handler-function",
            function_name=f"{config.stage}-9c-iap-goldendust-handler",
            runtime=_lambda.Runtime.PYTHON_3_10,
            description="Request handler for Golden dust by NCG for PC users",
            code=_lambda.AssetCode("worker/worker", exclude=exclude_list),
            handler="golden_dust_by_ncg.handle_request",
            layers=[layer],
            role=role,
            vpc=shared_stack.vpc,
            timeout=cdk_core.Duration.minutes(8),
            environment=env,
            memory_size=512,
            reserved_concurrent_executions=1,
        )

        # Every ten minute
        ten_minute_event_rule = _events.Rule(
            self, f"{config.stage}-9c-iap-gd-handler-event",
            schedule=_events.Schedule.cron(minute="*/10")  # Every ten minute
        )
        ten_minute_event_rule.add_target(_event_targets.LambdaFunction(gd_handler))

        # Golden dust unload Tx. tracker
        gd_tracker = _lambda.Function(
            self, f"{config.stage}-9c-iap-goldendust-tracker-function",
            function_name=f"{config.stage}-9c-iap-goldendust-tracker",
            runtime=_lambda.Runtime.PYTHON_3_10,
            description=f"Tx. status tracker for golden dust unload for PC users",
            code=_lambda.AssetCode("worker/worker", exclude=exclude_list),
            handler="golden_dust_by_ncg.track_tx",
            layers=[layer],
            role=role,
            vpc=shared_stack.vpc,
            timeout=cdk_core.Duration.seconds(50),
            environment=env,
            memory_size=256,
        )

        minute_event_rule.add_target(_event_targets.LambdaFunction(gd_tracker))

        # Manual unload function
        # This function does not have trigger. Go to AWS console and run manually.
        if config.stage != "mainnet":
            manual_unload = _lambda.Function(
                self, f"{config.stage}-9c-iap-manual-unload-function",
                function_name=f"{config.stage}-9c-iap-manual-unload",
                runtime=_lambda.Runtime.PYTHON_3_10,
                description=f"Manual unload Tx. executor from NineChronicles.IAP",
                code=_lambda.AssetCode("worker/worker", exclude=exclude_list),
                handler="manual.handle",
                layers=[layer],
                role=role,
                vpc=shared_stack.vpc,
                timeout=cdk_core.Duration.seconds(300),  # 5min
                environment=env,
                memory_size=512,
            )
