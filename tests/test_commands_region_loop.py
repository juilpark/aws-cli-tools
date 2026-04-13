import importlib
from types import SimpleNamespace

from aws_cli_tools.app import app


class FakeRegionLoopSession:
    def __init__(self, regions):
        self.regions = regions

    def client(self, service_name, region_name=None):
        assert service_name == "ec2"
        return SimpleNamespace(describe_regions=lambda: {"Regions": [{"RegionName": region} for region in self.regions]})


def test_region_loop_runs_command_for_each_region_with_profile(monkeypatch, cli_runner):
    region_loop_module = importlib.import_module("aws_cli_tools.commands.region_loop")

    monkeypatch.setattr(region_loop_module.boto3, "Session", lambda profile_name: FakeRegionLoopSession(["ap-northeast-2", "us-west-2"]))
    monkeypatch.setattr(region_loop_module, "order_regions_by_priority", lambda regions: regions)
    monkeypatch.setattr(region_loop_module, "print_regions_list", lambda regions: None)

    executed_commands = []
    monkeypatch.setattr(
        region_loop_module.subprocess,
        "run",
        lambda command, check=False: executed_commands.append((command, check)),
    )

    result = cli_runner.invoke(
        app,
        ["region-loop", "--profile", "sandbox"],
        input="aws ec2 describe-vpcs\ny\n",
    )

    assert result.exit_code == 0
    assert executed_commands == [
        (["aws", "--region", "ap-northeast-2", "ec2", "describe-vpcs", "--profile", "sandbox"], False),
        (["aws", "--region", "us-west-2", "ec2", "describe-vpcs", "--profile", "sandbox"], False),
    ]
    assert "Example Command: aws --region ap-northeast-2 ec2 describe-vpcs --profile sandbox" in result.stdout


def test_region_loop_rejects_non_aws_commands(monkeypatch, cli_runner):
    region_loop_module = importlib.import_module("aws_cli_tools.commands.region_loop")

    monkeypatch.setattr(region_loop_module.boto3, "Session", lambda profile_name: FakeRegionLoopSession(["ap-northeast-2"]))
    monkeypatch.setattr(region_loop_module, "order_regions_by_priority", lambda regions: regions)
    monkeypatch.setattr(region_loop_module, "print_regions_list", lambda regions: None)

    executed_commands = []
    monkeypatch.setattr(
        region_loop_module.subprocess,
        "run",
        lambda command, check=False: executed_commands.append((command, check)),
    )

    result = cli_runner.invoke(app, ["region-loop"], input="echo hello\n")

    assert result.exit_code == 1
    assert executed_commands == []
    assert "Error: Command must start with 'aws'" in result.stderr
