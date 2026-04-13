from typer.testing import CliRunner

from aws_cli_tools.app import app
from aws_cli_tools.constants import VERSION

runner = CliRunner()


def test_version_command_prints_current_version():
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"aws-cli-tools version {VERSION}"


def test_root_help_lists_available_commands():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "login" in result.stdout
    assert "region-loop" in result.stdout
    assert "resolve-instance" in result.stdout
    assert "ssm" in result.stdout
    assert "version" in result.stdout
