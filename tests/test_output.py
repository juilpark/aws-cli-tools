from unittest.mock import Mock

import typer
from botocore.exceptions import ClientError

import aws_cli_tools.output as output_module
from aws_cli_tools.errors import AwsOperationError


def test_prompt_mfa_token_prints_notice_and_prompts(monkeypatch):
    printed = []
    monkeypatch.setattr(output_module.console, "print", lambda value: printed.append(value))
    monkeypatch.setattr(output_module.typer, "prompt", lambda label: "654321")

    token = output_module.prompt_mfa_token("arn:aws:iam::123456789012:mfa/test-user")

    assert token == "654321"
    assert len(printed) == 1


def test_print_instance_matches_renders_fallback_values(monkeypatch):
    printed = []
    monkeypatch.setattr(output_module.console, "print", lambda value: printed.append(value))

    output_module.print_instance_matches(
        [
            {
                "region": "ap-northeast-2",
                "instance_id": "i-0123456789abcdef0",
                "private_ip": None,
                "public_ip": None,
                "state": None,
                "name": None,
            }
        ]
    )

    table = printed[0]
    assert table.columns[0]._cells == ["ap-northeast-2"]
    assert table.columns[1]._cells == ["-"]
    assert table.columns[2]._cells == ["i-0123456789abcdef0"]
    assert table.columns[3]._cells == ["-"]
    assert table.columns[4]._cells == ["-"]
    assert table.columns[5]._cells == ["unknown"]


def test_print_regions_list_outputs_grid_rows(monkeypatch):
    rows = []
    monkeypatch.setattr(output_module.typer, "echo", lambda value, err=False: rows.append((value, err)))

    output_module.print_regions_list(
        ["ap-northeast-2", "us-west-2", "eu-west-1"],
        cols=2,
    )

    assert rows == [
        ("  ap-northeast-2        us-west-2           ", False),
        ("  eu-west-1           ", False),
    ]


def test_print_aws_error_includes_context_and_auth_hint(monkeypatch):
    secho = Mock()
    echo = Mock()
    monkeypatch.setattr(output_module.typer, "secho", secho)
    monkeypatch.setattr(output_module.typer, "echo", echo)

    error = ClientError(
        {
            "Error": {"Code": "ExpiredToken", "Message": "expired"},
            "ResponseMetadata": {"RequestId": "req-123", "HTTPStatusCode": 403},
        },
        "DescribeInstances",
    )

    output_module.print_aws_error(
        AwsOperationError(
            operation="ec2.describe_instances",
            error=error,
            region="ap-northeast-2",
            profile="default",
        )
    )

    echo_calls = [call.args[0] for call in echo.call_args_list]
    assert "  operation: ec2.describe_instances" in echo_calls
    assert "  region: ap-northeast-2" in echo_calls
    assert "  profile: default" in echo_calls
    assert "  code: ExpiredToken" in echo_calls
    assert "  message: expired" in echo_calls
    assert "  http_status: 403" in echo_calls
    assert "  request_id: req-123" in echo_calls
    assert any("Hint: check whether the default profile" in call.args[0] for call in secho.call_args_list)


def test_print_aws_error_falls_back_for_non_client_errors(monkeypatch):
    secho = Mock()
    monkeypatch.setattr(output_module.typer, "secho", secho)

    output_module.print_aws_error(RuntimeError("boom"))

    secho.assert_called_once_with("AWS Error: boom", fg=typer.colors.RED, err=True)
