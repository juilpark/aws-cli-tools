import configparser
from unittest.mock import Mock

from botocore.exceptions import ClientError

import aws_cli_tools.aws_common as aws_common_module
from aws_cli_tools.errors import AwsOperationError
from aws_cli_tools.constants import DEFAULT_STS_REGION


def test_build_boto_config_uses_explicit_timeouts_and_attempts():
    config = aws_common_module.build_boto_config(connect_timeout=7, read_timeout=9, max_attempts=3)

    assert config.connect_timeout == 7
    assert config.read_timeout == 9
    assert config.retries == {"total_max_attempts": 3, "mode": "standard"}


def test_parse_region_priority_env_deduplicates_and_strips(monkeypatch):
    monkeypatch.setenv(
        "AWS_REGION_PRIORITY",
        " ap-northeast-2 , us-west-2,ap-northeast-2 ,, eu-west-1 ",
    )

    assert aws_common_module.parse_region_priority_env() == [
        "ap-northeast-2",
        "us-west-2",
        "eu-west-1",
    ]


def test_order_regions_by_priority_prefers_env_regions(monkeypatch):
    monkeypatch.setenv("AWS_REGION_PRIORITY", "us-west-2,ap-northeast-2")

    assert aws_common_module.order_regions_by_priority(
        ["eu-west-1", "ap-northeast-2", "us-west-2", "us-east-1"]
    ) == [
        "us-west-2",
        "ap-northeast-2",
        "eu-west-1",
        "us-east-1",
    ]


def test_order_regions_by_priority_sorts_when_no_env(clear_region_priority_env):
    assert aws_common_module.order_regions_by_priority(["us-west-2", "ap-northeast-2"]) == [
        "ap-northeast-2",
        "us-west-2",
    ]


def test_find_config_section_handles_default_and_profile_prefix():
    config = configparser.ConfigParser()
    config.read_dict(
        {
            "default": {"region": "ap-northeast-2"},
            "profile sandbox": {"region": "us-west-2"},
            "legacy": {"region": "eu-west-1"},
        }
    )

    assert aws_common_module.find_config_section(config, "default") == "default"
    assert aws_common_module.find_config_section(config, "sandbox") == "profile sandbox"
    assert aws_common_module.find_config_section(config, "legacy") == "legacy"
    assert aws_common_module.find_config_section(config, "missing") is None


def test_get_profile_region_prefers_session_region():
    class Session:
        region_name = "us-east-1"

    assert aws_common_module.get_profile_region("default", session=Session()) == "us-east-1"


def test_get_profile_region_reads_region_from_config(isolated_aws_config_path):
    isolated_aws_config_path.write_text("[profile sandbox]\nregion = eu-central-1\n")

    assert aws_common_module.get_profile_region("sandbox") == "eu-central-1"


def test_get_profile_region_falls_back_to_default_region(
    isolated_aws_config_path,
    clear_region_priority_env,
):
    isolated_aws_config_path.write_text("")

    assert aws_common_module.get_profile_region("missing") == DEFAULT_STS_REGION


def test_get_default_session_uses_default_profile(monkeypatch):
    session_factory = Mock(return_value="session")
    monkeypatch.setattr(aws_common_module.boto3, "Session", session_factory)

    assert aws_common_module.get_default_session() == "session"
    session_factory.assert_called_once_with(profile_name="default")


def test_get_enabled_regions_wraps_client_errors(monkeypatch):
    class FakeClient:
        def describe_regions(self, AllRegions=False):
            raise ClientError(
                {
                    "Error": {"Code": "UnauthorizedOperation", "Message": "denied"},
                    "ResponseMetadata": {"RequestId": "req-123", "HTTPStatusCode": 403},
                },
                "DescribeRegions",
            )

    class FakeSession:
        def client(self, service_name, region_name=None, config=None):
            assert service_name == "ec2"
            assert region_name == "us-east-1"
            return FakeClient()

    monkeypatch.setattr(aws_common_module, "get_default_session", lambda: FakeSession())

    try:
        aws_common_module.get_enabled_regions()
    except AwsOperationError as error:
        assert error.operation == "ec2.describe_regions"
        assert error.region == "us-east-1"
        assert error.profile == "default"
    else:
        raise AssertionError("Expected get_enabled_regions to wrap the client error")
