import importlib
import configparser
from unittest.mock import Mock

from botocore.exceptions import ClientError

from aws_cli_tools.app import app


class FakeStsClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get_session_token(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeSession:
    def __init__(self, sts_client):
        self.region_name = None
        self._sts_client = sts_client

    def client(self, service_name, region_name=None):
        assert service_name == "sts"
        self.last_region_name = region_name
        return self._sts_client


def test_login_updates_credentials_and_syncs_config(tmp_path, monkeypatch, cli_runner):
    login_module = importlib.import_module("aws_cli_tools.commands.login")

    aws_dir = tmp_path / ".aws"
    credentials_path = aws_dir / "credentials"
    config_path = aws_dir / "config"
    aws_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[profile source]\nregion = us-west-2\noutput = json\n")

    monkeypatch.setattr(login_module, "AWS_DOT_AWS_DIR", aws_dir)
    monkeypatch.setattr(login_module, "AWS_CREDENTIALS_FILE", credentials_path)
    monkeypatch.setattr(login_module, "AWS_CONFIG_FILE", config_path)
    monkeypatch.setattr(login_module, "get_profile_region", lambda profile_name, session=None: "us-west-2")

    sts_client = FakeStsClient(
        response={
            "Credentials": {
                "AccessKeyId": "ASIAEXAMPLE",
                "SecretAccessKey": "secret-key",
                "SessionToken": "session-token",
                "Expiration": "2030-01-01T00:00:00+00:00",
            }
        }
    )
    monkeypatch.setattr(login_module.boto3, "Session", lambda profile_name: FakeSession(sts_client))

    result = cli_runner.invoke(
        app,
        [
            "login",
            "--source-profile",
            "source",
            "--target-profile",
            "sandbox",
            "--duration",
            "3600",
            "--mfa-serial",
            "arn:aws:iam::123456789012:mfa/test-user",
            "--token-code",
            "123456",
        ],
    )

    assert result.exit_code == 0
    assert sts_client.calls == [
        {
            "DurationSeconds": 3600,
            "SerialNumber": "arn:aws:iam::123456789012:mfa/test-user",
            "TokenCode": "123456",
        }
    ]

    credentials = configparser.ConfigParser()
    credentials.read(credentials_path)
    assert credentials.get("sandbox", "aws_access_key_id") == "ASIAEXAMPLE"
    assert credentials.get("sandbox", "aws_secret_access_key") == "secret-key"
    assert credentials.get("sandbox", "aws_session_token") == "session-token"

    config = configparser.ConfigParser()
    config.read(config_path)
    assert config.get("profile sandbox", "region") == "us-west-2"
    assert config.get("profile sandbox", "output") == "json"
    assert "Successfully updated [sandbox]" in result.stdout
    assert "Synced config for [profile sandbox] from [profile source]" in result.stdout


def test_login_prompts_for_mfa_token_when_serial_is_given(tmp_path, monkeypatch, cli_runner):
    login_module = importlib.import_module("aws_cli_tools.commands.login")

    aws_dir = tmp_path / ".aws"
    credentials_path = aws_dir / "credentials"
    config_path = aws_dir / "config"
    aws_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(login_module, "AWS_DOT_AWS_DIR", aws_dir)
    monkeypatch.setattr(login_module, "AWS_CREDENTIALS_FILE", credentials_path)
    monkeypatch.setattr(login_module, "AWS_CONFIG_FILE", config_path)
    monkeypatch.setattr(login_module, "get_profile_region", lambda profile_name, session=None: "ap-northeast-2")

    sts_client = FakeStsClient(
        response={
            "Credentials": {
                "AccessKeyId": "ASIAMFA",
                "SecretAccessKey": "secret-key",
                "SessionToken": "session-token",
                "Expiration": "2030-01-01T00:00:00+00:00",
            }
        }
    )
    monkeypatch.setattr(login_module.boto3, "Session", lambda profile_name: FakeSession(sts_client))

    result = cli_runner.invoke(
        app,
        [
            "login",
            "--source-profile",
            "source",
            "--mfa-serial",
            "arn:aws:iam::123456789012:mfa/test-user",
        ],
        input="123456\n",
    )

    assert result.exit_code == 0
    assert sts_client.calls == [
        {
            "DurationSeconds": 28800,
            "SerialNumber": "arn:aws:iam::123456789012:mfa/test-user",
            "TokenCode": "123456",
        }
    ]
    assert "Enter MFA Token Code" in result.stdout


def test_login_prints_aws_error_and_exits_nonzero(tmp_path, monkeypatch, cli_runner):
    login_module = importlib.import_module("aws_cli_tools.commands.login")

    aws_dir = tmp_path / ".aws"
    aws_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(login_module, "AWS_DOT_AWS_DIR", aws_dir)
    monkeypatch.setattr(login_module, "AWS_CREDENTIALS_FILE", aws_dir / "credentials")
    monkeypatch.setattr(login_module, "AWS_CONFIG_FILE", aws_dir / "config")
    monkeypatch.setattr(login_module, "get_profile_region", lambda profile_name, session=None: "ap-northeast-2")

    error = ClientError(
        {
            "Error": {"Code": "AccessDenied", "Message": "denied"},
            "ResponseMetadata": {"RequestId": "req-123", "HTTPStatusCode": 403},
        },
        "GetSessionToken",
    )
    monkeypatch.setattr(
        login_module.boto3,
        "Session",
        lambda profile_name: FakeSession(FakeStsClient(error=error)),
    )
    print_aws_error = Mock()
    monkeypatch.setattr(login_module, "print_aws_error", print_aws_error)

    result = cli_runner.invoke(
        app,
        [
            "login",
            "--source-profile",
            "source",
            "--mfa-serial",
            "arn:aws:iam::123456789012:mfa/test-user",
            "--token-code",
            "123456",
        ],
    )

    assert result.exit_code == 1
    print_aws_error.assert_called_once()
