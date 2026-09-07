import csv
import json
from datetime import datetime, timezone

from botocore.stub import Stubber

import aws_cli_tools.ec2_inventory as inventory_module
from aws_cli_tools.errors import AwsOperationError


def test_extract_instance_inventory_includes_ri_sp_attributes():
    launch_time = datetime(2026, 9, 7, 1, 2, 3, tzinfo=timezone.utc)

    records = inventory_module.extract_instance_inventory(
        [
            {
                "ReservationId": "r-0123456789abcdef0",
                "OwnerId": "123456789012",
                "Instances": [
                    {
                        "InstanceId": "i-0123456789abcdef0",
                        "ImageId": "ami-0123456789abcdef0",
                        "InstanceType": "m7g.large",
                        "State": {"Name": "running"},
                        "Placement": {"AvailabilityZone": "ap-northeast-2a", "Tenancy": "default"},
                        "PlatformDetails": "Linux/UNIX",
                        "Architecture": "arm64",
                        "InstanceLifecycle": "spot",
                        "LaunchTime": launch_time,
                        "CpuOptions": {"CoreCount": 1, "ThreadsPerCore": 2},
                        "UsageOperation": "RunInstances",
                        "EbsOptimized": True,
                        "Tags": [
                            {"Key": "Name", "Value": "web-a"},
                            {"Key": "Environment", "Value": "prod"},
                        ],
                    }
                ],
            }
        ],
        "ap-northeast-2",
    )

    assert records == [
        {
            "captured_at_utc": None,
            "region": "ap-northeast-2",
            "availability_zone": "ap-northeast-2a",
            "instance_id": "i-0123456789abcdef0",
            "name": "web-a",
            "image_id": "ami-0123456789abcdef0",
            "instance_type": "m7g.large",
            "state": "running",
            "platform": None,
            "platform_details": "Linux/UNIX",
            "architecture": "arm64",
            "tenancy": "default",
            "instance_lifecycle": "spot",
            "spot_instance": True,
            "launch_time": "2026-09-07T01:02:03Z",
            "usage_operation": "RunInstances",
            "usage_operation_update_time": None,
            "vcpu_core_count": 1,
            "threads_per_core": 2,
            "hypervisor": None,
            "virtualization_type": None,
            "ebs_optimized": True,
            "root_device_type": None,
            "vpc_id": None,
            "subnet_id": None,
            "private_ip": None,
            "public_ip": None,
            "placement_group_name": None,
            "reservation_id": "r-0123456789abcdef0",
            "owner_id": "123456789012",
            "tags_json": json.dumps({"Environment": "prod", "Name": "web-a"}, ensure_ascii=False, sort_keys=True),
        }
    ]


def test_list_instances_in_region_uses_describe_instances_paginator(
    aws_client_factory,
    stubbed_session_factory,
    monkeypatch,
):
    ec2_client = aws_client_factory("ec2", "ap-northeast-2")
    with Stubber(ec2_client) as stubber:
        stubber.add_response(
            "describe_instances",
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-0123456789abcdef0",
                                "InstanceType": "t3.micro",
                                "State": {"Name": "running"},
                            }
                        ]
                    }
                ]
            },
            {},
        )
        monkeypatch.setattr(
            inventory_module,
            "get_default_session",
            lambda: stubbed_session_factory({("ec2", "ap-northeast-2"): ec2_client}),
        )

        records = inventory_module.list_instances_in_region("ap-northeast-2")

    assert len(records) == 1
    assert records[0]["instance_id"] == "i-0123456789abcdef0"
    assert records[0]["instance_type"] == "t3.micro"


def test_collect_ec2_instance_inventory_sorts_rows_by_region_and_instance(monkeypatch):
    monkeypatch.setattr(inventory_module, "get_enabled_regions", lambda: ["us-west-2", "ap-northeast-2"])

    def fake_list(region, connect_timeout, read_timeout, max_attempts):
        return [
            {
                "captured_at_utc": None,
                "region": region,
                "instance_id": "i-2" if region == "us-west-2" else "i-1",
                "tags_json": "{}",
                "spot_instance": False,
            }
        ]

    monkeypatch.setattr(inventory_module, "list_instances_in_region", fake_list)

    records = inventory_module.collect_ec2_instance_inventory()

    assert [(record["region"], record["instance_id"]) for record in records] == [
        ("ap-northeast-2", "i-1"),
        ("us-west-2", "i-2"),
    ]


def test_collect_ec2_instance_inventory_raises_when_a_region_fails(monkeypatch):
    monkeypatch.setattr(inventory_module, "get_enabled_regions", lambda: ["ap-northeast-2"])
    expected = AwsOperationError(
        operation="ec2.describe_instances",
        error=RuntimeError("denied"),
        region="ap-northeast-2",
        profile="default",
    )
    monkeypatch.setattr(inventory_module, "list_instances_in_region", lambda *args: (_ for _ in ()).throw(expected))

    try:
        inventory_module.collect_ec2_instance_inventory()
    except AwsOperationError as error:
        assert error is expected
    else:
        raise AssertionError("Expected inventory collection to fail when a region fails")


def test_write_inventory_csv_creates_timestamped_file_and_serializes_values(tmp_path):
    records = inventory_module.extract_instance_inventory(
        [
            {
                "Instances": [
                    {
                        "InstanceId": "i-0123456789abcdef0",
                        "InstanceType": "t3.micro",
                        "State": {"Name": "running"},
                        "Tags": [{"Key": "Name", "Value": "web,a"}],
                    }
                ]
            }
        ],
        "ap-northeast-2",
    )
    captured_at = datetime(2026, 9, 7, 1, 2, 3, tzinfo=timezone.utc)

    output_path = inventory_module.write_inventory_csv(records, tmp_path, captured_at=captured_at)

    assert output_path == tmp_path / "ec2-instances-20260907T010203Z.csv"
    with output_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert rows[0]["captured_at_utc"] == "2026-09-07T01:02:03Z"
    assert rows[0]["instance_id"] == "i-0123456789abcdef0"
    assert rows[0]["spot_instance"] == "false"
    assert json.loads(rows[0]["tags_json"]) == {"Name": "web,a"}

    second_output_path = inventory_module.write_inventory_csv(records, tmp_path, captured_at=captured_at)
    assert second_output_path == tmp_path / "ec2-instances-20260907T010203Z-2.csv"
