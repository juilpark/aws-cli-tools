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


def test_region_loop_aborts_when_user_cancels(monkeypatch, cli_runner):
    region_loop_module = importlib.import_module("aws_cli_tools.commands.region_loop")

    monkeypatch.setattr(region_loop_module.boto3, "Session", lambda profile_name: FakeRegionLoopSession(["ap-northeast-2"]))
    monkeypatch.setattr(region_loop_module, "order_regions_by_priority", lambda regions: regions)
    monkeypatch.setattr(region_loop_module, "print_regions_list", lambda regions: None)
    run_mock = []
    monkeypatch.setattr(region_loop_module.subprocess, "run", lambda command, check=False: run_mock.append(command))

    result = cli_runner.invoke(app, ["region-loop"], input="aws ec2 describe-vpcs\nn\n")

    assert result.exit_code == 1
    assert run_mock == []
    assert "Operation cancelled." in result.stdout


def test_region_loop_continues_when_one_region_command_fails(monkeypatch, cli_runner):
    region_loop_module = importlib.import_module("aws_cli_tools.commands.region_loop")

    monkeypatch.setattr(
        region_loop_module.boto3,
        "Session",
        lambda profile_name: FakeRegionLoopSession(["ap-northeast-2", "us-west-2"]),
    )
    monkeypatch.setattr(region_loop_module, "order_regions_by_priority", lambda regions: regions)
    monkeypatch.setattr(region_loop_module, "print_regions_list", lambda regions: None)
    executed = []

    def fake_run(command, check=False):
        executed.append(command)
        if command[2] == "ap-northeast-2":
            raise RuntimeError("bad region")

    monkeypatch.setattr(region_loop_module.subprocess, "run", fake_run)

    result = cli_runner.invoke(app, ["region-loop"], input="aws ec2 describe-vpcs\ny\n")

    assert result.exit_code == 0
    assert len(executed) == 2
    assert "Failed to execute command in ap-northeast-2: bad region" in result.stdout


def test_region_loop_stops_on_keyboard_interrupt(monkeypatch, cli_runner):
    region_loop_module = importlib.import_module("aws_cli_tools.commands.region_loop")

    monkeypatch.setattr(region_loop_module.boto3, "Session", lambda profile_name: FakeRegionLoopSession(["ap-northeast-2"]))
    monkeypatch.setattr(region_loop_module, "order_regions_by_priority", lambda regions: regions)
    monkeypatch.setattr(region_loop_module, "print_regions_list", lambda regions: None)
    monkeypatch.setattr(
        region_loop_module.subprocess,
        "run",
        lambda command, check=False: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = cli_runner.invoke(app, ["region-loop"], input="aws ec2 describe-vpcs\ny\n")

    assert result.exit_code == 1
    assert "Loop interrupted by user." in result.stdout


def test_region_loop_does_not_duplicate_existing_profile_argument(monkeypatch, cli_runner):
    region_loop_module = importlib.import_module("aws_cli_tools.commands.region_loop")

    monkeypatch.setattr(region_loop_module.boto3, "Session", lambda profile_name: FakeRegionLoopSession(["ap-northeast-2"]))
    monkeypatch.setattr(region_loop_module, "order_regions_by_priority", lambda regions: regions)
    monkeypatch.setattr(region_loop_module, "print_regions_list", lambda regions: None)
    executed_commands = []
    monkeypatch.setattr(
        region_loop_module.subprocess,
        "run",
        lambda command, check=False: executed_commands.append(command),
    )

    result = cli_runner.invoke(
        app,
        ["region-loop", "--profile", "sandbox"],
        input="aws ec2 describe-vpcs --profile explicit\ny\n",
    )

    assert result.exit_code == 0
    assert executed_commands == [["aws", "--region", "ap-northeast-2", "ec2", "describe-vpcs", "--profile", "explicit"]]
    assert "--profile sandbox" not in result.stdout
