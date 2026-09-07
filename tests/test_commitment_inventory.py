import csv
from datetime import datetime, timezone

from botocore.exceptions import ClientError
from botocore.stub import Stubber

import aws_cli_tools.commitment_inventory as inventory_module
from aws_cli_tools.errors import AwsOperationError


def test_build_commitment_eligibility_covers_all_supported_service_types():
    records = inventory_module.build_commitment_eligibility()

    assert {record["service_code"] for record in records} == {
        "ec2",
        "ecs",
        "eks",
        "lambda",
        "sagemaker",
        "rds",
        "dsql",
        "dynamodb",
        "elasticache",
        "opensearch",
        "opensearchserverless",
        "redshift",
        "memorydb",
        "docdb",
        "neptune",
        "neptune-graph",
        "timestream-write",
        "keyspaces",
        "dms",
    }
    rds = next(record for record in records if record["service_code"] == "rds")
    assert rds["commitment_types"] == "Reserved Instance;Database Savings Plan"
    assert all(record["inventory_status"] == "supported" for record in records)


def test_collect_ec2_marks_spot_usage_as_not_eligible(monkeypatch):
    spec = next(spec for spec in inventory_module.COMMITMENT_SERVICE_SPECS if spec.service_code == "ec2")
    monkeypatch.setattr(
        inventory_module,
        "list_instances_in_region",
        lambda *args: [
            {
                "instance_id": "i-spot",
                "name": "spot-worker",
                "state": "running",
                "platform_details": "Linux/UNIX",
                "instance_type": "m7g.large",
                "spot_instance": True,
                "vcpu_core_count": 2,
                "threads_per_core": 1,
                "availability_zone": "ap-northeast-2a",
                "instance_lifecycle": "spot",
                "tenancy": "default",
                "tags_json": "{}",
            }
        ],
    )

    rows = inventory_module._collect_ec2(spec, "ap-northeast-2", 3, 5, 1)

    assert len(rows) == 1
    assert rows[0]["commitment_types"] == ""
    assert "Spot usage" in (rows[0]["eligibility_note"] or "")


def test_collect_lambda_uses_list_functions_paginator(
    aws_client_factory,
    stubbed_session_factory,
    monkeypatch,
):
    lambda_client = aws_client_factory("lambda", "ap-northeast-2")
    with Stubber(lambda_client) as stubber:
        stubber.add_response(
            "list_functions",
            {
                "Functions": [
                    {
                        "FunctionName": "billing-worker",
                        "FunctionArn": "arn:aws:lambda:ap-northeast-2:123456789012:function:billing-worker",
                        "Runtime": "python3.14",
                        "MemorySize": 1024,
                        "Timeout": 30,
                        "State": "Active",
                    }
                ]
            },
            {},
        )
        monkeypatch.setattr(
            inventory_module,
            "get_default_session",
            lambda: stubbed_session_factory({("lambda", "ap-northeast-2"): lambda_client}),
        )
        spec = next(spec for spec in inventory_module.COMMITMENT_SERVICE_SPECS if spec.service_code == "lambda")

        rows = inventory_module._collect_lambda(spec, "ap-northeast-2", 3, 5, 1)

    assert rows[0]["resource_name"] == "billing-worker"
    assert rows[0]["capacity_summary"] == "memory_mb=1024;timeout_seconds=30"
    assert rows[0]["commitment_types"] == "Compute Savings Plan"


def test_collect_resources_records_per_service_errors_and_sorts(monkeypatch):
    specs = inventory_module.COMMITMENT_SERVICE_SPECS[:2]
    monkeypatch.setattr(inventory_module, "COMMITMENT_SERVICE_SPECS", specs)
    monkeypatch.setattr(inventory_module, "get_enabled_regions", lambda: ["ap-northeast-2"])

    expected_error = ClientError(
        {
            "Error": {"Code": "AccessDeniedException", "Message": "denied"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        "ListTasks",
    )

    def fake_collect(spec, region, connect_timeout, read_timeout, max_attempts):
        if spec.service_code == "ecs":
            raise expected_error
        return [
            inventory_module._resource_row(
                spec,
                region,
                resource_id="i-1",
                resource_name="web",
            )
        ]

    monkeypatch.setattr(inventory_module, "_collect_service_region", fake_collect)

    rows = inventory_module.collect_commitment_resource_inventory()

    assert [(row["service_code"], row["collection_status"]) for row in rows] == [
        ("ec2", "ok"),
        ("ecs", "error"),
    ]
    assert rows[1]["error_code"] == "AccessDeniedException"


def test_collect_resources_converts_wrapped_ec2_errors_to_csv_rows(monkeypatch):
    spec = inventory_module.COMMITMENT_SERVICE_SPECS[:1]
    monkeypatch.setattr(inventory_module, "COMMITMENT_SERVICE_SPECS", spec)
    monkeypatch.setattr(inventory_module, "get_enabled_regions", lambda: ["ap-northeast-2"])
    expected = AwsOperationError(
        operation="ec2.describe_instances",
        error=RuntimeError("denied"),
        region="ap-northeast-2",
        profile="default",
    )
    monkeypatch.setattr(
        inventory_module,
        "_collect_service_region",
        lambda *args: (_ for _ in ()).throw(expected),
    )

    rows = inventory_module.collect_commitment_resource_inventory()

    assert rows[0]["collection_status"] == "error"
    assert rows[0]["error_code"] == "RuntimeError"
    assert rows[0]["error_message"] == "denied"


def test_write_commitment_csvs_create_timestamped_files(tmp_path):
    captured_at = datetime(2026, 9, 7, 1, 2, 3, tzinfo=timezone.utc)
    eligibility = inventory_module.build_commitment_eligibility()
    resources = [
        inventory_module._resource_row(
            inventory_module.COMMITMENT_SERVICE_SPECS[0],
            "ap-northeast-2",
            resource_id="i-0123456789abcdef0",
            resource_name="web",
        )
    ]

    eligibility_path = inventory_module.write_commitment_eligibility_csv(
        eligibility,
        tmp_path,
        captured_at=captured_at,
    )
    resources_path = inventory_module.write_commitment_resource_inventory_csv(
        resources,
        tmp_path,
        captured_at=captured_at,
    )

    assert eligibility_path.name == "commitment-eligibility-20260907T010203Z.csv"
    assert resources_path.name == "commitment-resources-20260907T010203Z.csv"
    with resources_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert rows[0]["service_code"] == "ec2"
    assert rows[0]["resource_id"] == "i-0123456789abcdef0"
    assert rows[0]["collection_status"] == "ok"
