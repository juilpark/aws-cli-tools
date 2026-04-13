from botocore.stub import Stubber

import aws_cli_tools.instances as instances_module
from aws_cli_tools.errors import AwsOperationError


def test_resolve_instance_matches_in_region_normalizes_matches_for_name_target(
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
                                "State": {"Code": 16, "Name": "running"},
                                "PrivateIpAddress": "10.0.0.12",
                                "PublicIpAddress": "3.39.10.20",
                                "Tags": [{"Key": "Name", "Value": "web-a"}],
                            }
                        ]
                    }
                ]
            },
            {"Filters": [{"Name": "tag:Name", "Values": ["web-a"]}]},
        )
        monkeypatch.setattr(
            instances_module,
            "get_default_session",
            lambda: stubbed_session_factory({("ec2", "ap-northeast-2"): ec2_client}),
        )

        assert instances_module.resolve_instance_matches_in_region("ap-northeast-2", "web-a", "name") == [
            {
                "region": "ap-northeast-2",
                "instance_id": "i-0123456789abcdef0",
                "private_ip": "10.0.0.12",
                "public_ip": "3.39.10.20",
                "state": "running",
                "name": "web-a",
            }
        ]


def test_resolve_instance_matches_in_region_queries_private_and_public_ip(
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
                                "InstanceId": "i-private0000000001",
                                "State": {"Code": 16, "Name": "running"},
                                "PrivateIpAddress": "10.0.0.15",
                                "Tags": [{"Key": "Name", "Value": "private-match"}],
                            }
                        ]
                    }
                ]
            },
            {"Filters": [{"Name": "private-ip-address", "Values": ["10.0.0.15"]}]},
        )
        stubber.add_response(
            "describe_instances",
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-public00000000002",
                                "State": {"Code": 16, "Name": "running"},
                                "PublicIpAddress": "10.0.0.15",
                                "Tags": [{"Key": "Name", "Value": "public-match"}],
                            }
                        ]
                    }
                ]
            },
            {"Filters": [{"Name": "ip-address", "Values": ["10.0.0.15"]}]},
        )
        monkeypatch.setattr(
            instances_module,
            "get_default_session",
            lambda: stubbed_session_factory({("ec2", "ap-northeast-2"): ec2_client}),
        )

        matches = instances_module.resolve_instance_matches_in_region("ap-northeast-2", "10.0.0.15", "ip")

        assert [match["instance_id"] for match in matches] == [
            "i-private0000000001",
            "i-public00000000002",
        ]


def test_resolve_instance_matches_in_region_returns_empty_for_missing_instance_id(
    aws_client_factory,
    stubbed_session_factory,
    monkeypatch,
):
    ec2_client = aws_client_factory("ec2", "ap-northeast-2")
    with Stubber(ec2_client) as stubber:
        stubber.add_client_error(
            "describe_instances",
            service_error_code="InvalidInstanceID.NotFound",
            service_message="The instance ID does not exist",
            expected_params={"InstanceIds": ["i-0123456789abcdef0"]},
        )
        monkeypatch.setattr(
            instances_module,
            "get_default_session",
            lambda: stubbed_session_factory({("ec2", "ap-northeast-2"): ec2_client}),
        )

        assert (
            instances_module.resolve_instance_matches_in_region(
                "ap-northeast-2",
                "i-0123456789abcdef0",
                "instance_id",
            )
            == []
        )


def test_describe_instances_by_ids_in_region_chunks_requests(
    aws_client_factory,
    stubbed_session_factory,
    monkeypatch,
):
    ec2_client = aws_client_factory("ec2", "ap-northeast-2")
    instance_ids = [f"i-{index:017x}" for index in range(101)]
    with Stubber(ec2_client) as stubber:
        stubber.add_response(
            "describe_instances",
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": instance_ids[0],
                                "State": {"Code": 16, "Name": "running"},
                                "Tags": [{"Key": "Name", "Value": "first-chunk"}],
                            }
                        ]
                    }
                ]
            },
            {"InstanceIds": instance_ids[:100]},
        )
        stubber.add_response(
            "describe_instances",
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": instance_ids[100],
                                "State": {"Code": 80, "Name": "stopped"},
                                "Tags": [{"Key": "Name", "Value": "second-chunk"}],
                            }
                        ]
                    }
                ]
            },
            {"InstanceIds": instance_ids[100:]},
        )
        monkeypatch.setattr(
            instances_module,
            "get_default_session",
            lambda: stubbed_session_factory({("ec2", "ap-northeast-2"): ec2_client}),
        )

        matches = instances_module.describe_instances_by_ids_in_region("ap-northeast-2", instance_ids)

        assert [match["instance_id"] for match in matches] == [instance_ids[0], instance_ids[100]]
        assert [match["state"] for match in matches] == ["running", "stopped"]


def test_describe_instances_by_ids_in_region_wraps_client_errors(
    aws_client_factory,
    stubbed_session_factory,
    monkeypatch,
):
    ec2_client = aws_client_factory("ec2", "ap-northeast-2")
    with Stubber(ec2_client) as stubber:
        stubber.add_client_error(
            "describe_instances",
            service_error_code="UnauthorizedOperation",
            expected_params={"InstanceIds": ["i-0123456789abcdef0"]},
        )
        monkeypatch.setattr(
            instances_module,
            "get_default_session",
            lambda: stubbed_session_factory({("ec2", "ap-northeast-2"): ec2_client}),
        )

        try:
            instances_module.describe_instances_by_ids_in_region("ap-northeast-2", ["i-0123456789abcdef0"])
        except AwsOperationError as error:
            assert error.operation == "ec2.describe_instances"
            assert error.region == "ap-northeast-2"
            assert error.profile == "default"
        else:
            raise AssertionError("Expected describe_instances_by_ids_in_region to wrap the client error")
