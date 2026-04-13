from botocore.stub import Stubber

import aws_cli_tools.aws_common as aws_common_module
from aws_cli_tools.errors import AwsOperationError


def test_get_all_regions_uses_stubbed_ec2_response_and_priority(
    aws_client_factory,
    stubbed_session_factory,
    monkeypatch,
):
    ec2_client = aws_client_factory("ec2", "us-east-1")
    with Stubber(ec2_client) as stubber:
        stubber.add_response(
            "describe_regions",
            {
                "Regions": [
                    {"RegionName": "ap-northeast-2", "Endpoint": "ec2.ap-northeast-2.amazonaws.com"},
                    {"RegionName": "eu-west-1", "Endpoint": "ec2.eu-west-1.amazonaws.com"},
                    {"RegionName": "us-west-2", "Endpoint": "ec2.us-west-2.amazonaws.com"},
                ]
            },
            {},
        )
        monkeypatch.setattr(
            aws_common_module,
            "get_default_session",
            lambda: stubbed_session_factory({("ec2", "us-east-1"): ec2_client}),
        )
        monkeypatch.setenv("AWS_REGION_PRIORITY", "us-west-2")

        assert aws_common_module.get_all_regions() == [
            "us-west-2",
            "ap-northeast-2",
            "eu-west-1",
        ]


def test_get_enabled_regions_filters_opt_in_statuses(
    aws_client_factory,
    stubbed_session_factory,
    monkeypatch,
):
    ec2_client = aws_client_factory("ec2", "us-east-1")
    with Stubber(ec2_client) as stubber:
        stubber.add_response(
            "describe_regions",
            {
                "Regions": [
                    {
                        "RegionName": "ap-northeast-2",
                        "Endpoint": "ec2.ap-northeast-2.amazonaws.com",
                        "OptInStatus": "opt-in-not-required",
                    },
                    {
                        "RegionName": "eu-west-1",
                        "Endpoint": "ec2.eu-west-1.amazonaws.com",
                        "OptInStatus": "not-opted-in",
                    },
                    {
                        "RegionName": "us-west-2",
                        "Endpoint": "ec2.us-west-2.amazonaws.com",
                        "OptInStatus": "opted-in",
                    },
                ]
            },
            {"AllRegions": True},
        )
        monkeypatch.setattr(
            aws_common_module,
            "get_default_session",
            lambda: stubbed_session_factory({("ec2", "us-east-1"): ec2_client}),
        )
        monkeypatch.delenv("AWS_REGION_PRIORITY", raising=False)

        assert aws_common_module.get_enabled_regions() == [
            "ap-northeast-2",
            "us-west-2",
        ]


def test_get_all_regions_wraps_client_errors(
    aws_client_factory,
    stubbed_session_factory,
    monkeypatch,
):
    ec2_client = aws_client_factory("ec2", "us-east-1")
    with Stubber(ec2_client) as stubber:
        stubber.add_client_error("describe_regions", service_error_code="UnauthorizedOperation")
        monkeypatch.setattr(
            aws_common_module,
            "get_default_session",
            lambda: stubbed_session_factory({("ec2", "us-east-1"): ec2_client}),
        )

        try:
            aws_common_module.get_all_regions()
        except AwsOperationError as error:
            assert error.operation == "ec2.describe_regions"
            assert error.region == "us-east-1"
            assert error.profile == "default"
            assert error.error.response["Error"]["Code"] == "UnauthorizedOperation"
        else:
            raise AssertionError("Expected get_all_regions to wrap the client error")
