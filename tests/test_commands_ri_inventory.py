import importlib
from pathlib import Path
from unittest.mock import Mock

from aws_cli_tools.app import app
from aws_cli_tools.errors import AwsOperationError


def test_ri_inventory_writes_csv_to_requested_directory(monkeypatch, cli_runner, tmp_path):
    command_module = importlib.import_module("aws_cli_tools.commands.ri_inventory")
    output_path = tmp_path / "ri-commitments-20260907T010203Z.csv"
    records = [{"region": "ap-northeast-2", "reserved_instances_id": "ri-1"}]
    collect = Mock(return_value=records)
    write = Mock(return_value=output_path)
    monkeypatch.setattr(command_module, "collect_reserved_instance_inventory", collect)
    monkeypatch.setattr(command_module, "write_ri_inventory_csv", write)

    result = cli_runner.invoke(
        app,
        [
            "ri-inventory",
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
    write.assert_called_once_with(records, Path(tmp_path))
    assert "Collected 1 Reserved Instance commitments." in result.stdout
    assert f"CSV saved to: {output_path}" in result.stdout


def test_ri_inventory_prints_aws_error_and_does_not_write_file(monkeypatch, cli_runner):
    command_module = importlib.import_module("aws_cli_tools.commands.ri_inventory")
    error = AwsOperationError(
        operation="ec2.describe_reserved_instances",
        error=RuntimeError("denied"),
        region="ap-northeast-2",
        profile="default",
    )
    monkeypatch.setattr(command_module, "collect_reserved_instance_inventory", Mock(side_effect=error))
    print_aws_error = Mock()
    monkeypatch.setattr(command_module, "print_aws_error", print_aws_error)

    result = cli_runner.invoke(app, ["ri-inventory"])

    assert result.exit_code == 1
    print_aws_error.assert_called_once_with(error)
