import boto3
import pytest
from typer.testing import CliRunner


@pytest.fixture
def isolated_cache_paths(tmp_path, monkeypatch):
    import aws_cli_tools.cache as cache_module

    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(cache_module, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(cache_module, "RESOLVE_CACHE_FILE", cache_dir / "resolve-instance.json")
    monkeypatch.setattr(cache_module, "REGION_FAILURE_CACHE_FILE", cache_dir / "region-failures.json")
    return cache_dir


@pytest.fixture
def isolated_aws_config_path(tmp_path, monkeypatch):
    import aws_cli_tools.aws_common as aws_common_module

    aws_dir = tmp_path / ".aws"
    aws_dir.mkdir(parents=True, exist_ok=True)
    config_path = aws_dir / "config"
    monkeypatch.setattr(aws_common_module, "AWS_CONFIG_FILE", config_path)
    return config_path


@pytest.fixture
def clear_region_priority_env(monkeypatch):
    monkeypatch.delenv("AWS_REGION_PRIORITY", raising=False)


@pytest.fixture
def sample_match():
    return [
        {
            "region": "ap-northeast-2",
            "instance_id": "i-0123456789abcdef0",
            "private_ip": "10.0.0.12",
            "public_ip": None,
            "state": "running",
            "name": "example-instance",
        }
    ]


class StubbedSession:
    def __init__(self, clients):
        self._clients = clients

    def client(self, service_name, region_name=None, config=None):
        key = (service_name, region_name)
        if key in self._clients:
            return self._clients[key]

        fallback_key = (service_name, None)
        if fallback_key in self._clients:
            return self._clients[fallback_key]

        raise AssertionError(f"No stubbed client registered for service={service_name!r}, region={region_name!r}")


@pytest.fixture
def aws_client_factory():
    def factory(service_name, region_name):
        return boto3.client(
            service_name,
            region_name=region_name,
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
            aws_session_token="testing",
        )

    return factory


@pytest.fixture
def stubbed_session_factory():
    def factory(clients):
        return StubbedSession(clients)

    return factory


@pytest.fixture
def cli_runner():
    return CliRunner()
