import csv
import json
from datetime import datetime, timezone

from botocore.stub import Stubber

import aws_cli_tools.ri_inventory as inventory_module
from aws_cli_tools.errors import AwsOperationError


def test_extract_reserved_instance_inventory_includes_commitment_details():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc)

    records = inventory_module.extract_reserved_instance_inventory(
        [
            {
                "ReservedInstancesId": "e5a2ff3b-7d14-494f-90af-0b5d0EXAMPLE",
                "InstanceType": "m7g.large",
                "InstanceCount": 2,
                "State": "active",
                "Scope": "Region",
                "AvailabilityZone": "",
                "AvailabilityZoneId": "",
                "InstanceTenancy": "default",
                "ProductDescription": "Linux/UNIX",
                "OfferingClass": "standard",
                "OfferingType": "Partial Upfront",
                "Duration": 31536000,
                "Start": start,
                "End": end,
                "CurrencyCode": "USD",
                "FixedPrice": 100.0,
                "UsagePrice": 0.034,
                "RecurringCharges": [{"Amount": 0.05, "Frequency": "Hourly"}],
                "Tags": [{"Key": "Environment", "Value": "prod"}],
            }
        ],
        "ap-northeast-2",
    )

    assert records[0] == {
        "captured_at_utc": None,
        "region": "ap-northeast-2",
        "reserved_instances_id": "e5a2ff3b-7d14-494f-90af-0b5d0EXAMPLE",
        "instance_type": "m7g.large",
        "instance_count": 2,
        "state": "active",
        "scope": "Region",
        "availability_zone": "",
        "availability_zone_id": "",
        "instance_tenancy": "default",
        "product_description": "Linux/UNIX",
        "offering_class": "standard",
        "offering_type": "Partial Upfront",
        "duration_seconds": 31536000,
        "start": "2026-01-01T00:00:00Z",
        "end": "2027-01-01T00:00:00Z",
        "currency_code": "USD",
        "fixed_price": 100.0,
        "usage_price": 0.034,
        "recurring_charge_amount": 0.05,
        "recurring_charge_frequency": "Hourly",
        "recurring_charges_json": json.dumps(
            [{"Amount": 0.05, "Frequency": "Hourly"}],
            ensure_ascii=False,
            sort_keys=True,
        ),
        "tags_json": json.dumps({"Environment": "prod"}, ensure_ascii=False, sort_keys=True),
    }


def test_list_reserved_instances_in_region_calls_describe_reserved_instances(
    aws_client_factory,
    stubbed_session_factory,
    monkeypatch,
):
    ec2_client = aws_client_factory("ec2", "ap-northeast-2")
    with Stubber(ec2_client) as stubber:
        stubber.add_response(
            "describe_reserved_instances",
            {
                "ReservedInstances": [
                    {
                        "ReservedInstancesId": "e5a2ff3b-7d14-494f-90af-0b5d0EXAMPLE",
                        "InstanceType": "t3.micro",
                        "InstanceCount": 1,
                        "State": "active",
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

        records = inventory_module.list_reserved_instances_in_region("ap-northeast-2")

    assert len(records) == 1
    assert records[0]["reserved_instances_id"] == "e5a2ff3b-7d14-494f-90af-0b5d0EXAMPLE"
    assert records[0]["instance_count"] == 1


def test_collect_reserved_instance_inventory_sorts_rows_by_region_and_id(monkeypatch):
    monkeypatch.setattr(inventory_module, "get_enabled_regions", lambda: ["us-west-2", "ap-northeast-2"])

    def fake_list(region, connect_timeout, read_timeout, max_attempts):
        return [
            {
                "region": region,
                "reserved_instances_id": "ri-2" if region == "us-west-2" else "ri-1",
            }
        ]

    monkeypatch.setattr(inventory_module, "list_reserved_instances_in_region", fake_list)

    records = inventory_module.collect_reserved_instance_inventory()

    assert [(record["region"], record["reserved_instances_id"]) for record in records] == [
        ("ap-northeast-2", "ri-1"),
        ("us-west-2", "ri-2"),
    ]


def test_collect_reserved_instance_inventory_raises_when_a_region_fails(monkeypatch):
    monkeypatch.setattr(inventory_module, "get_enabled_regions", lambda: ["ap-northeast-2"])
    expected = AwsOperationError(
        operation="ec2.describe_reserved_instances",
        error=RuntimeError("denied"),
        region="ap-northeast-2",
        profile="default",
    )
    monkeypatch.setattr(
        inventory_module,
        "list_reserved_instances_in_region",
        lambda *args: (_ for _ in ()).throw(expected),
    )

    try:
        inventory_module.collect_reserved_instance_inventory()
    except AwsOperationError as error:
        assert error is expected
    else:
        raise AssertionError("Expected RI collection to fail when a region fails")


def test_write_ri_inventory_csv_creates_timestamped_file(tmp_path):
    records = inventory_module.extract_reserved_instance_inventory(
        [
            {
                "ReservedInstancesId": "e5a2ff3b-7d14-494f-90af-0b5d0EXAMPLE",
                "InstanceType": "t3.micro",
                "InstanceCount": 1,
                "State": "active",
            }
        ],
        "ap-northeast-2",
    )
    captured_at = datetime(2026, 9, 7, 1, 2, 3, tzinfo=timezone.utc)

    output_path = inventory_module.write_ri_inventory_csv(records, tmp_path, captured_at=captured_at)

    assert output_path == tmp_path / "ri-commitments-20260907T010203Z.csv"
    with output_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert rows[0]["captured_at_utc"] == "2026-09-07T01:02:03Z"
    assert rows[0]["region"] == "ap-northeast-2"
    assert rows[0]["reserved_instances_id"] == "e5a2ff3b-7d14-494f-90af-0b5d0EXAMPLE"
    assert rows[0]["instance_count"] == "1"
