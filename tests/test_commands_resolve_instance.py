import importlib
from unittest.mock import Mock

from aws_cli_tools.app import app


def test_resolve_instance_uses_cached_match_without_calling_resolver(monkeypatch, cli_runner, sample_match):
    resolve_instance_module = importlib.import_module("aws_cli_tools.commands.resolve_instance")

    monkeypatch.setattr(resolve_instance_module, "get_cached_resolve_result", lambda target: sample_match)
    monkeypatch.setattr(
        resolve_instance_module,
        "resolve_instance_matches",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resolver should not be called")),
    )
    print_instance_matches = Mock()
    monkeypatch.setattr(resolve_instance_module, "print_instance_matches", print_instance_matches)

    result = cli_runner.invoke(app, ["resolve-instance", "example-instance"])

    assert result.exit_code == 0
    print_instance_matches.assert_called_once_with(sample_match)
    assert "Cache hit: returning cached resolver result." in result.stdout


def test_resolve_instance_caches_single_match_when_lookup_succeeds(monkeypatch, cli_runner, sample_match):
    resolve_instance_module = importlib.import_module("aws_cli_tools.commands.resolve_instance")

    monkeypatch.setattr(resolve_instance_module, "get_cached_resolve_result", lambda target: None)
    monkeypatch.setattr(resolve_instance_module, "resolve_instance_matches", lambda target, **kwargs: sample_match)
    cache_resolve_result = Mock()
    print_instance_matches = Mock()
    monkeypatch.setattr(resolve_instance_module, "cache_resolve_result", cache_resolve_result)
    monkeypatch.setattr(resolve_instance_module, "print_instance_matches", print_instance_matches)

    result = cli_runner.invoke(app, ["resolve-instance", "example-instance"])

    assert result.exit_code == 0
    cache_resolve_result.assert_called_once_with("example-instance", sample_match)
    print_instance_matches.assert_called_once_with(sample_match)


def test_resolve_instance_exits_for_ambiguous_matches(monkeypatch, cli_runner):
    resolve_instance_module = importlib.import_module("aws_cli_tools.commands.resolve_instance")

    matches = [
        {"region": "ap-northeast-2", "instance_id": "i-1", "private_ip": None, "public_ip": None, "state": "running", "name": "web"},
        {"region": "us-west-2", "instance_id": "i-2", "private_ip": None, "public_ip": None, "state": "running", "name": "web"},
    ]
    monkeypatch.setattr(resolve_instance_module, "get_cached_resolve_result", lambda target: None)
    monkeypatch.setattr(resolve_instance_module, "resolve_instance_matches", lambda target, **kwargs: matches)
    print_instance_matches = Mock()
    monkeypatch.setattr(resolve_instance_module, "print_instance_matches", print_instance_matches)

    result = cli_runner.invoke(app, ["resolve-instance", "web"])

    assert result.exit_code == 1
    print_instance_matches.assert_called_once_with(matches)
    assert "Multiple instances matched [web]." in result.stderr


def test_resolve_instance_exits_when_no_match_is_found(monkeypatch, cli_runner):
    resolve_instance_module = importlib.import_module("aws_cli_tools.commands.resolve_instance")

    monkeypatch.setattr(resolve_instance_module, "get_cached_resolve_result", lambda target: None)
    monkeypatch.setattr(resolve_instance_module, "resolve_instance_matches", lambda target, **kwargs: [])
    print_instance_matches = Mock()
    monkeypatch.setattr(resolve_instance_module, "print_instance_matches", print_instance_matches)

    result = cli_runner.invoke(app, ["resolve-instance", "missing-instance"])

    assert result.exit_code == 1
    print_instance_matches.assert_not_called()
    assert "No instance found for [missing-instance]." in result.stderr
