from botocore.stub import Stubber

import aws_cli_tools.instances as instances_module
import aws_cli_tools.ssm_targets as ssm_targets_module


def test_list_ssm_candidates_in_region_filters_ids_deduplicates_and_sorts(
    aws_client_factory,
    stubbed_session_factory,
    monkeypatch,
):
    ssm_client = aws_client_factory("ssm", "ap-northeast-2")
    ec2_client = aws_client_factory("ec2", "ap-northeast-2")

    with Stubber(ssm_client) as ssm_stubber, Stubber(ec2_client) as ec2_stubber:
        ssm_stubber.add_response(
            "describe_instance_information",
            {
                "InstanceInformationList": [
                    {"InstanceId": "i-00000000000000002"},
                    {"InstanceId": "mi-00000000000000001"},
                    {"InstanceId": "i-00000000000000001"},
                    {"InstanceId": "i-00000000000000002"},
                ]
            },
            {
                "Filters": [
                    {"Key": "PingStatus", "Values": ["Online"]},
                    {"Key": "ResourceType", "Values": ["EC2Instance"]},
                ]
            },
        )
        ec2_stubber.add_response(
            "describe_instances",
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-00000000000000001",
                                "State": {"Code": 16, "Name": "running"},
                                "Tags": [{"Key": "Name", "Value": "z-last"}],
                            },
                            {
                                "InstanceId": "i-00000000000000002",
                                "State": {"Code": 16, "Name": "running"},
                                "Tags": [{"Key": "Name", "Value": "a-first"}],
                            },
                        ]
                    }
                ]
            },
            {"InstanceIds": ["i-00000000000000001", "i-00000000000000002"]},
        )
        monkeypatch.setattr(
            ssm_targets_module,
            "get_default_session",
            lambda: stubbed_session_factory({("ssm", "ap-northeast-2"): ssm_client}),
        )
        monkeypatch.setattr(
            instances_module,
            "get_default_session",
            lambda: stubbed_session_factory({("ec2", "ap-northeast-2"): ec2_client}),
        )

        matches = ssm_targets_module.list_ssm_candidates_in_region("ap-northeast-2")

        assert [match["instance_id"] for match in matches] == [
            "i-00000000000000002",
            "i-00000000000000001",
        ]
        assert [match["name"] for match in matches] == ["a-first", "z-last"]


def test_build_ssm_command_uses_default_profile():
    assert ssm_targets_module.build_ssm_command(
        {
            "region": "ap-northeast-2",
            "instance_id": "i-0123456789abcdef0",
            "private_ip": None,
            "public_ip": None,
            "state": "running",
            "name": "example",
        }
    ) == [
        "aws",
        "ssm",
        "start-session",
        "--target",
        "i-0123456789abcdef0",
        "--region",
        "ap-northeast-2",
        "--profile",
        "default",
    ]
