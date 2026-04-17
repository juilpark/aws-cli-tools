import importlib
import typer
from unittest.mock import Mock

from botocore.exceptions import ClientError

from aws_cli_tools.app import app


def test_ssm_exits_when_aws_cli_is_not_available(monkeypatch, cli_runner):
    ssm_module = importlib.import_module("aws_cli_tools.commands.ssm")

    monkeypatch.setattr(ssm_module.shutil, "which", lambda command: None)

    result = cli_runner.invoke(app, ["ssm", "example-instance"])

    assert result.exit_code == 1
    assert "AWS CLI not found in PATH." in result.stderr


def test_ssm_uses_cached_match_and_execs_command(monkeypatch, cli_runner, sample_match):
    ssm_module = importlib.import_module("aws_cli_tools.commands.ssm")

    command = ["aws", "ssm", "start-session", "--target", "i-0123456789abcdef0", "--region", "ap-northeast-2", "--profile", "default"]
    monkeypatch.setattr(ssm_module.shutil, "which", lambda binary: "/usr/local/bin/aws")
    monkeypatch.setattr(ssm_module, "get_cached_resolve_result", lambda target: sample_match)
    monkeypatch.setattr(
        ssm_module,
        "resolve_instance_matches",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resolver should not be called")),
    )
    monkeypatch.setattr(ssm_module, "build_ssm_command", lambda match: command)
    monkeypatch.setattr(ssm_module.console, "print", lambda *args, **kwargs: None)
    print_instance_matches = Mock()
    monkeypatch.setattr(ssm_module, "print_instance_matches", print_instance_matches)

    exec_calls = []

    def fake_execv(path, argv):
        exec_calls.append((path, argv))
        raise typer.Exit(code=0)

    monkeypatch.setattr(ssm_module.os, "execv", fake_execv)

    result = cli_runner.invoke(app, ["ssm", "example-instance"])

    assert result.exit_code == 0
    print_instance_matches.assert_called_once_with(sample_match)
    assert exec_calls == [
        ("/usr/local/bin/aws", ["/usr/local/bin/aws", "ssm", "start-session", "--target", "i-0123456789abcdef0", "--region", "ap-northeast-2", "--profile", "default"])
    ]
    assert "Cache hit: using cached resolver result." in result.stdout


def test_ssm_without_target_uses_interactive_selector(monkeypatch, cli_runner, sample_match):
    ssm_module = importlib.import_module("aws_cli_tools.commands.ssm")

    monkeypatch.setattr(ssm_module.shutil, "which", lambda binary: "/usr/local/bin/aws")
    monkeypatch.setattr(ssm_module.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(ssm_module, "print_instance_matches", lambda matches: None)
    monkeypatch.setattr(
        ssm_module,
        "build_ssm_command",
        lambda match: ["aws", "ssm", "start-session", "--target", match["instance_id"], "--region", match["region"], "--profile", "default"],
    )

    run_ssm_browser = Mock(return_value=(sample_match[0], None))
    monkeypatch.setattr(ssm_module, "run_ssm_browser", run_ssm_browser)

    exec_calls = []

    def fake_execv(path, argv):
        exec_calls.append((path, argv))
        raise typer.Exit(code=0)

    monkeypatch.setattr(ssm_module.os, "execv", fake_execv)

    result = cli_runner.invoke(app, ["ssm"])

    assert result.exit_code == 0
    run_ssm_browser.assert_called_once_with(
        use_cached_results=True,
        connect_timeout=3,
        read_timeout=5,
        max_attempts=1,
    )
    assert exec_calls[0][0] == "/usr/local/bin/aws"


def test_ssm_without_target_can_bypass_browser_cache(monkeypatch, cli_runner, sample_match):
    ssm_module = importlib.import_module("aws_cli_tools.commands.ssm")

    monkeypatch.setattr(ssm_module.shutil, "which", lambda binary: "/usr/local/bin/aws")
    monkeypatch.setattr(ssm_module.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(ssm_module, "print_instance_matches", lambda matches: None)
    monkeypatch.setattr(
        ssm_module,
        "build_ssm_command",
        lambda match: ["aws", "ssm", "start-session", "--target", match["instance_id"], "--region", match["region"], "--profile", "default"],
    )

    run_ssm_browser = Mock(return_value=(sample_match[0], None))
    monkeypatch.setattr(ssm_module, "run_ssm_browser", run_ssm_browser)

    def fake_execv(path, argv):
        raise typer.Exit(code=0)

    monkeypatch.setattr(ssm_module.os, "execv", fake_execv)

    result = cli_runner.invoke(app, ["ssm", "--no-cache"])

    assert result.exit_code == 0
    run_ssm_browser.assert_called_once_with(
        use_cached_results=False,
        connect_timeout=3,
        read_timeout=5,
        max_attempts=1,
    )


def test_ssm_uses_selector_for_ambiguous_matches(monkeypatch, cli_runner):
    ssm_module = importlib.import_module("aws_cli_tools.commands.ssm")

    matches = [
        {"region": "us-west-2", "instance_id": "i-b", "private_ip": None, "public_ip": None, "state": "running", "name": "z-last"},
        {"region": "ap-northeast-2", "instance_id": "i-a", "private_ip": None, "public_ip": None, "state": "running", "name": "a-first"},
    ]
    monkeypatch.setattr(ssm_module.shutil, "which", lambda binary: "/usr/local/bin/aws")
    monkeypatch.setattr(ssm_module, "get_cached_resolve_result", lambda target: None)
    monkeypatch.setattr(ssm_module, "resolve_instance_matches", lambda target, **kwargs: matches)
    monkeypatch.setattr(ssm_module.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(ssm_module, "print_instance_matches", lambda matches: None)
    monkeypatch.setattr(
        ssm_module,
        "build_ssm_command",
        lambda match: ["aws", "ssm", "start-session", "--target", match["instance_id"], "--region", match["region"], "--profile", "default"],
    )

    selector_kwargs = {}

    class FakeSelectorApp:
        def __init__(self, **kwargs):
            selector_kwargs.update(kwargs)

        def run(self):
            return matches[0]

    monkeypatch.setattr(ssm_module, "SsmSelectionApp", FakeSelectorApp)

    exec_calls = []

    def fake_execv(path, argv):
        exec_calls.append((path, argv))
        raise typer.Exit(code=0)

    monkeypatch.setattr(ssm_module.os, "execv", fake_execv)

    result = cli_runner.invoke(app, ["ssm", "web"])

    assert result.exit_code == 0
    assert [match["instance_id"] for match in selector_kwargs["initial_matches"]] == ["i-a", "i-b"]
    assert selector_kwargs["live_load"] is False
    assert exec_calls[0][1][-6:] == ["--target", "i-b", "--region", "us-west-2", "--profile", "default"]


def test_ssm_caches_single_resolved_match_before_exec(monkeypatch, cli_runner, sample_match):
    ssm_module = importlib.import_module("aws_cli_tools.commands.ssm")

    monkeypatch.setattr(ssm_module.shutil, "which", lambda binary: "/usr/local/bin/aws")
    monkeypatch.setattr(ssm_module, "get_cached_resolve_result", lambda target: None)

    def fake_resolve_instance_matches(target, on_first_match=None, **kwargs):
        on_first_match(sample_match[0])
        return sample_match

    monkeypatch.setattr(ssm_module, "resolve_instance_matches", fake_resolve_instance_matches)
    cache_resolve_result = Mock()
    monkeypatch.setattr(ssm_module, "cache_resolve_result", cache_resolve_result)
    monkeypatch.setattr(ssm_module.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(ssm_module, "print_instance_matches", lambda matches: None)
    monkeypatch.setattr(
        ssm_module,
        "build_ssm_command",
        lambda match: ["aws", "ssm", "start-session", "--target", match["instance_id"], "--region", match["region"], "--profile", "default"],
    )

    exec_calls = []

    def fake_execv(path, argv):
        exec_calls.append((path, argv))
        raise typer.Exit(code=0)

    monkeypatch.setattr(ssm_module.os, "execv", fake_execv)

    result = cli_runner.invoke(app, ["ssm", "example-instance"])

    assert result.exit_code == 0
    cache_resolve_result.assert_called_once_with("example-instance", sample_match)
    assert exec_calls[0][0] == "/usr/local/bin/aws"


def test_ssm_reauthenticates_once_when_browser_load_hits_request_expired(monkeypatch, cli_runner, sample_match):
    ssm_module = importlib.import_module("aws_cli_tools.commands.ssm")

    monkeypatch.setattr(ssm_module.shutil, "which", lambda binary: "/usr/local/bin/aws")
    monkeypatch.setattr(ssm_module.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(ssm_module, "print_instance_matches", lambda matches: None)
    monkeypatch.setattr(
        ssm_module,
        "build_ssm_command",
        lambda match: ["aws", "ssm", "start-session", "--target", match["instance_id"], "--region", match["region"], "--profile", "default"],
    )

    request_expired = ClientError(
        {
            "Error": {"Code": "RequestExpired", "Message": "Request has expired."},
            "ResponseMetadata": {"RequestId": "req-123", "HTTPStatusCode": 400},
        },
        "DescribeRegions",
    )
    run_ssm_browser = Mock(side_effect=[(None, request_expired), (sample_match[0], None)])
    monkeypatch.setattr(ssm_module, "run_ssm_browser", run_ssm_browser)

    run_login = Mock()
    monkeypatch.setattr(ssm_module, "run_login", run_login)

    exec_calls = []

    def fake_execv(path, argv):
        exec_calls.append((path, argv))
        raise typer.Exit(code=0)

    monkeypatch.setattr(ssm_module.os, "execv", fake_execv)

    result = cli_runner.invoke(app, ["ssm"])

    assert result.exit_code == 0
    run_login.assert_called_once()
    assert run_ssm_browser.call_count == 2
    assert exec_calls[0][0] == "/usr/local/bin/aws"
    assert "Running login and retrying once" in result.stdout


def test_is_request_expired_error_handles_wrapped_and_plain_errors():
    ssm_module = importlib.import_module("aws_cli_tools.commands.ssm")

    wrapped = ssm_module.AwsOperationError(
        operation="ec2.describe_instances",
        error=ClientError(
            {
                "Error": {"Code": "ExpiredTokenException", "Message": "expired"},
                "ResponseMetadata": {"RequestId": "req-123", "HTTPStatusCode": 403},
            },
            "DescribeInstances",
        ),
        region="ap-northeast-2",
        profile="default",
    )

    assert ssm_module.is_request_expired_error(wrapped) is True
    assert ssm_module.is_request_expired_error(RuntimeError("Request has expired")) is True
    assert ssm_module.is_request_expired_error(RuntimeError("different")) is False


def test_run_ssm_browser_returns_selection_and_loading_error(monkeypatch, sample_match):
    ssm_module = importlib.import_module("aws_cli_tools.commands.ssm")

    class FakeBrowser:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.loading_error = RuntimeError("load failed")

        def run(self):
            return sample_match[0]

    monkeypatch.setattr(ssm_module, "SsmSelectionApp", FakeBrowser)

    match, error = ssm_module.run_ssm_browser(
        use_cached_results=False,
        connect_timeout=3,
        read_timeout=4,
        max_attempts=5,
    )

    assert match == sample_match[0]
    assert str(error) == "load failed"


def test_ssm_browser_exit_without_selection_returns_nonzero(monkeypatch, cli_runner):
    ssm_module = importlib.import_module("aws_cli_tools.commands.ssm")

    monkeypatch.setattr(ssm_module.shutil, "which", lambda binary: "/usr/local/bin/aws")
    monkeypatch.setattr(ssm_module, "run_ssm_browser", lambda **kwargs: (None, None))

    result = cli_runner.invoke(app, ["ssm"])

    assert result.exit_code == 1


def test_ssm_prints_aws_error_after_retry_is_exhausted(monkeypatch, cli_runner):
    ssm_module = importlib.import_module("aws_cli_tools.commands.ssm")

    request_expired = ClientError(
        {
            "Error": {"Code": "RequestExpired", "Message": "Request has expired."},
            "ResponseMetadata": {"RequestId": "req-123", "HTTPStatusCode": 400},
        },
        "DescribeRegions",
    )
    print_aws_error = Mock()

    monkeypatch.setattr(ssm_module.shutil, "which", lambda binary: "/usr/local/bin/aws")
    monkeypatch.setattr(ssm_module, "run_ssm_browser", lambda **kwargs: (None, request_expired))
    monkeypatch.setattr(ssm_module, "run_login", Mock())
    monkeypatch.setattr(ssm_module, "print_aws_error", print_aws_error)

    result = cli_runner.invoke(app, ["ssm"])

    assert result.exit_code == 1
    print_aws_error.assert_called_once()
