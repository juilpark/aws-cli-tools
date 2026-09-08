import importlib
from pathlib import Path
from unittest.mock import Mock

from aws_cli_tools.app import app
from aws_cli_tools.errors import AwsOperationError


def test_commitment_inventory_writes_eligibility_and_resource_csvs(monkeypatch, cli_runner, tmp_path):
    command_module = importlib.import_module("aws_cli_tools.commands.commitment_inventory")
    eligibility = [{"service_code": "ec2"}]
    resources = [{"collection_status": "ok", "service_code": "ec2"}]
    eligibility_path = tmp_path / "commitment-eligibility.csv"
    resources_path = tmp_path / "commitment-resources.csv"
    build = Mock(return_value=eligibility)
    collect = Mock(return_value=resources)
    write_eligibility = Mock(return_value=eligibility_path)
    write_resources = Mock(return_value=resources_path)
    monkeypatch.setattr(command_module, "build_commitment_eligibility", build)
    monkeypatch.setattr(command_module, "collect_commitment_resource_inventory", collect)
    monkeypatch.setattr(command_module, "write_commitment_eligibility_csv", write_eligibility)
    monkeypatch.setattr(command_module, "write_commitment_resource_inventory_csv", write_resources)

    result = cli_runner.invoke(
        app,
        [
            "inventory",
            "commitment",
            "--output-dir",
            str(tmp_path),
            "--connect-timeout",
            "7",
            "--read-timeout",
            "8",
            "--max-attempts",
            "2",
        ],
    )

    assert result.exit_code == 0
    collect.assert_called_once_with(connect_timeout=7, read_timeout=8, max_attempts=2)
    write_eligibility.assert_called_once()
    write_resources.assert_called_once()
    assert write_eligibility.call_args.args[:2] == (eligibility, Path(tmp_path))
    assert write_resources.call_args.args[:2] == (resources, Path(tmp_path))
    assert "Collected 1 commitment-relevant resource rows." in result.stdout
    assert f"Eligibility CSV saved to: {eligibility_path}" in result.stdout
    assert f"Resource CSV saved to: {resources_path}" in result.stdout


def test_commitment_inventory_prints_aws_error_and_exits(monkeypatch, cli_runner):
    command_module = importlib.import_module("aws_cli_tools.commands.commitment_inventory")
    error = AwsOperationError(
        operation="ec2.describe_regions",
        error=RuntimeError("denied"),
        region="us-east-1",
        profile="default",
    )
    monkeypatch.setattr(command_module, "collect_commitment_resource_inventory", Mock(side_effect=error))
    monkeypatch.setattr(command_module, "write_commitment_eligibility_csv", Mock())
    print_aws_error = Mock()
    monkeypatch.setattr(command_module, "print_aws_error", print_aws_error)

    result = cli_runner.invoke(app, ["inventory", "commitment"])

    assert result.exit_code == 1
    print_aws_error.assert_called_once_with(error)
