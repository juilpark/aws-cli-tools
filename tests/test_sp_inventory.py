import csv
import json
from datetime import datetime, timezone

from botocore.stub import Stubber

import aws_cli_tools.sp_inventory as inventory_module
from aws_cli_tools.errors import AwsOperationError


def test_extract_savings_plan_inventory_includes_contract_details():
    records = inventory_module.extract_savings_plan_inventory(
        [
            {
                "offeringId": "offering-1",
                "savingsPlanId": "sp-0123456789abcdef0",
                "savingsPlanArn": "arn:aws:savingsplans::123456789012:savingsplan/sp-0123456789abcdef0",
                "description": "production compute",
                "start": "2026-01-01T00:00:00Z",
                "end": "2027-01-01T00:00:00Z",
                "returnableUntil": "2026-01-31T00:00:00Z",
                "state": "active",
                "region": "ap-northeast-2",
                "ec2InstanceFamily": "m7g",
                "savingsPlanType": "EC2Instance",
                "paymentOption": "Partial Upfront",
                "productTypes": ["EC2"],
                "currency": "USD",
                "commitment": "0.10000000",
                "upfrontPaymentAmount": "100.00",
                "recurringPaymentAmount": "0.00",
                "termDurationInSeconds": 31536000,
                "tags": {"Environment": "prod"},
            }
        ]
    )

    assert records == [
        {
            "captured_at_utc": None,
            "savings_plan_id": "sp-0123456789abcdef0",
            "savings_plan_arn": "arn:aws:savingsplans::123456789012:savingsplan/sp-0123456789abcdef0",
            "state": "active",
            "savings_plan_type": "EC2Instance",
            "region": "ap-northeast-2",
            "ec2_instance_family": "m7g",
            "product_types": "EC2",
            "payment_option": "Partial Upfront",
            "currency": "USD",
            "commitment": "0.10000000",
            "upfront_payment_amount": "100.00",
            "recurring_payment_amount": "0.00",
            "term_duration_seconds": 31536000,
            "start": "2026-01-01T00:00:00Z",
            "end": "2027-01-01T00:00:00Z",
            "returnable_until": "2026-01-31T00:00:00Z",
            "offering_id": "offering-1",
            "description": "production compute",
            "tags_json": json.dumps({"Environment": "prod"}, ensure_ascii=False, sort_keys=True),
        }
    ]


def test_list_savings_plans_follows_next_token(
    aws_client_factory,
    stubbed_session_factory,
    monkeypatch,
):
    savingsplans_client = aws_client_factory("savingsplans", "us-east-1")
    with Stubber(savingsplans_client) as stubber:
        stubber.add_response(
            "describe_savings_plans",
            {
                "savingsPlans": [
                    {
                        "savingsPlanId": "sp-1",
                        "state": "active",
                        "region": "ap-northeast-2",
                    }
                ],
                "nextToken": "token-1",
            },
            {"maxResults": 1000},
        )
        stubber.add_response(
            "describe_savings_plans",
            {
                "savingsPlans": [
                    {
                        "savingsPlanId": "sp-2",
                        "state": "retired",
                        "region": "us-west-2",
                    }
                ]
            },
            {"maxResults": 1000, "nextToken": "token-1"},
        )
        monkeypatch.setattr(
            inventory_module,
            "get_default_session",
            lambda: stubbed_session_factory({("savingsplans", "us-east-1"): savingsplans_client}),
        )

        records = inventory_module.list_savings_plans()

    assert [record["savings_plan_id"] for record in records] == ["sp-1", "sp-2"]


def test_collect_savings_plan_inventory_sorts_rows_by_region_and_id(monkeypatch):
    monkeypatch.setattr(
        inventory_module,
        "list_savings_plans",
        lambda **_: [
            {
                "captured_at_utc": None,
                "savings_plan_id": "sp-2",
                "region": "us-west-2",
                "tags_json": "{}",
            },
            {
                "captured_at_utc": None,
                "savings_plan_id": "sp-1",
                "region": "ap-northeast-2",
                "tags_json": "{}",
            },
        ],
    )

    records = inventory_module.collect_savings_plan_inventory()

    assert [(record["region"], record["savings_plan_id"]) for record in records] == [
        ("ap-northeast-2", "sp-1"),
        ("us-west-2", "sp-2"),
    ]


def test_list_savings_plans_wraps_aws_errors(
    aws_client_factory,
    stubbed_session_factory,
    monkeypatch,
):
    savingsplans_client = aws_client_factory("savingsplans", "us-east-1")
    with Stubber(savingsplans_client) as stubber:
        stubber.add_client_error(
            "describe_savings_plans",
            service_error_code="AccessDeniedException",
            service_message="denied",
            http_status_code=403,
            expected_params={"maxResults": 1000},
        )
        monkeypatch.setattr(
            inventory_module,
            "get_default_session",
            lambda: stubbed_session_factory({("savingsplans", "us-east-1"): savingsplans_client}),
        )

        try:
            inventory_module.list_savings_plans()
        except AwsOperationError as error:
            assert error.operation == "savingsplans.describe_savings_plans"
            assert error.region == "us-east-1"
            assert error.profile == "default"
        else:
            raise AssertionError("Expected Savings Plans API errors to be wrapped")


def test_write_savings_plan_inventory_csv_creates_timestamped_file(tmp_path):
    records = inventory_module.extract_savings_plan_inventory(
        [
            {
                "savingsPlanId": "sp-0123456789abcdef0",
                "state": "active",
                "region": "ap-northeast-2",
            }
        ]
    )
    captured_at = datetime(2026, 9, 7, 1, 2, 3, tzinfo=timezone.utc)

    output_path = inventory_module.write_savings_plan_inventory_csv(
        records,
        tmp_path,
        captured_at=captured_at,
    )

    assert output_path == tmp_path / "sp-commitments-20260907T010203Z.csv"
    with output_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert rows[0]["captured_at_utc"] == "2026-09-07T01:02:03Z"
    assert rows[0]["savings_plan_id"] == "sp-0123456789abcdef0"
    assert rows[0]["state"] == "active"
    assert rows[0]["tags_json"] == "{}"
