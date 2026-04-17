from botocore.exceptions import ConnectTimeoutError, EndpointConnectionError

from aws_cli_tools.errors import AwsOperationError, is_skippable_region_error


def test_is_skippable_region_error_accepts_timeout_errors():
    assert is_skippable_region_error(ConnectTimeoutError(endpoint_url="https://ec2.amazonaws.com")) is True
    assert is_skippable_region_error(EndpointConnectionError(endpoint_url="https://ec2.amazonaws.com")) is True


def test_is_skippable_region_error_unwraps_wrapped_errors_and_rejects_other_errors():
    wrapped = AwsOperationError(
        operation="ec2.describe_instances",
        error=ConnectTimeoutError(endpoint_url="https://ec2.amazonaws.com"),
        region="us-east-1",
        profile="default",
    )

    assert is_skippable_region_error(wrapped) is True
    assert is_skippable_region_error(RuntimeError("boom")) is False
