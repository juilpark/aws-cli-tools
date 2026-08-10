import importlib
from unittest.mock import Mock

from botocore.exceptions import ClientError

from aws_cli_tools.app import app


def test_ssm_targets_prints_csv_output(monkeypatch, cli_runner, sample_match):
    ssm_targets_module = importlib.import_module("aws_cli_tools.commands.ssm_targets")

    load_ssm_target_candidates = Mock(return_value=sample_match)
    monkeypatch.setattr(ssm_targets_module, "load_ssm_target_candidates", load_ssm_target_candidates)

    result = cli_runner.invoke(app, ["ssm-targets"])

    assert result.exit_code == 0
    load_ssm_target_candidates.assert_called_once_with(
        use_cached_results=True,
        connect_timeout=3,
        read_timeout=5,
        max_attempts=1,
    )
    assert (
        result.stdout
        == "region,name,instance_id,private_ip,public_ip,state\n"
        "ap-northeast-2,example-instance,i-0123456789abcdef0,10.0.0.12,-,running\n"
    )


def test_ssm_targets_can_bypass_cache(monkeypatch, cli_runner):
    ssm_targets_module = importlib.import_module("aws_cli_tools.commands.ssm_targets")

    load_ssm_target_candidates = Mock(return_value=[])
    monkeypatch.setattr(ssm_targets_module, "load_ssm_target_candidates", load_ssm_target_candidates)

    result = cli_runner.invoke(app, ["ssm-targets", "--no-cache"])

    assert result.exit_code == 0
    load_ssm_target_candidates.assert_called_once_with(
        use_cached_results=False,
        connect_timeout=3,
        read_timeout=5,
        max_attempts=1,
    )
    assert result.stdout == "region,name,instance_id,private_ip,public_ip,state\n"


def test_ssm_targets_reauthenticates_once_when_request_is_expired(monkeypatch, cli_runner, sample_match):
    ssm_targets_module = importlib.import_module("aws_cli_tools.commands.ssm_targets")

    request_expired = ClientError(
        {
            "Error": {"Code": "RequestExpired", "Message": "Request has expired."},
            "ResponseMetadata": {"RequestId": "req-123", "HTTPStatusCode": 400},
        },
        "DescribeRegions",
    )
    load_ssm_target_candidates = Mock(side_effect=[request_expired, sample_match])
    run_login = Mock()

    monkeypatch.setattr(ssm_targets_module, "load_ssm_target_candidates", load_ssm_target_candidates)
    monkeypatch.setattr(ssm_targets_module, "run_login", run_login)

    result = cli_runner.invoke(app, ["ssm-targets"])

    assert result.exit_code == 0
    assert load_ssm_target_candidates.call_count == 2
    run_login.assert_called_once()
    assert "Running login and retrying once" in result.stdout


def test_ssm_targets_prints_aws_error_after_retry_is_exhausted(monkeypatch, cli_runner):
    ssm_targets_module = importlib.import_module("aws_cli_tools.commands.ssm_targets")

    request_expired = ClientError(
        {
            "Error": {"Code": "RequestExpired", "Message": "Request has expired."},
            "ResponseMetadata": {"RequestId": "req-123", "HTTPStatusCode": 400},
        },
        "DescribeRegions",
    )
    print_aws_error = Mock()

    monkeypatch.setattr(
        ssm_targets_module,
        "load_ssm_target_candidates",
        Mock(side_effect=[request_expired, request_expired]),
    )
    monkeypatch.setattr(ssm_targets_module, "run_login", Mock())
    monkeypatch.setattr(ssm_targets_module, "print_aws_error", print_aws_error)

    result = cli_runner.invoke(app, ["ssm-targets"])

    assert result.exit_code == 1
    print_aws_error.assert_called_once()
