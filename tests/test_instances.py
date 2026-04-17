from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError

import aws_cli_tools.instances as instances_module
from aws_cli_tools.errors import AwsOperationError


class FakeFuture:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def result(self):
        if self._error is not None:
            raise self._error
        return self._result


class FakeExecutor:
    def __init__(self, max_workers):
        self.max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, func, *args):
        try:
            return FakeFuture(result=func(*args))
        except Exception as error:
            return FakeFuture(error=error)


def test_extract_instance_matches_skips_non_dict_instances():
    matches = instances_module.extract_instance_matches(
        [
            "skip-me",
            {
                "Instances": [
                    "not-a-dict",
                    {
                        "InstanceId": "i-0123456789abcdef0",
                        "State": {"Name": "running"},
                        "PrivateIpAddress": "10.0.0.12",
                        "Tags": [{"Key": "Name", "Value": "web-a"}],
                    },
                ]
            },
        ],
        "ap-northeast-2",
    )

    assert matches == [
        {
            "region": "ap-northeast-2",
            "instance_id": "i-0123456789abcdef0",
            "private_ip": "10.0.0.12",
            "public_ip": None,
            "state": "running",
            "name": "web-a",
        }
    ]


def test_resolve_instance_matches_in_region_wraps_initial_boto_errors(monkeypatch):
    monkeypatch.setattr(
        instances_module,
        "_paginate_matches_for_target",
        lambda *args, **kwargs: (_ for _ in ()).throw(BotoCoreError()),
    )

    try:
        instances_module.resolve_instance_matches_in_region("ap-northeast-2", "web-a", "name")
    except AwsOperationError as error:
        assert error.operation == "ec2.describe_instances"
        assert error.region == "ap-northeast-2"
        assert error.profile == "default"
    else:
        raise AssertionError("Expected resolve_instance_matches_in_region to wrap BotoCoreError")


def test_resolve_instance_matches_in_region_wraps_public_ip_lookup_errors(monkeypatch):
    calls = []

    def fake_paginate(region, filter_name, filter_values, connect_timeout, read_timeout, max_attempts):
        calls.append(filter_name)
        if filter_name == "private-ip-address":
            return [
                {
                    "region": region,
                    "instance_id": "i-private",
                    "private_ip": "10.0.0.15",
                    "public_ip": None,
                    "state": "running",
                    "name": "private",
                }
            ]
        raise ClientError(
            {
                "Error": {"Code": "UnauthorizedOperation", "Message": "denied"},
                "ResponseMetadata": {"RequestId": "req-123", "HTTPStatusCode": 403},
            },
            "DescribeInstances",
        )

    monkeypatch.setattr(instances_module, "_paginate_matches_for_target", fake_paginate)

    try:
        instances_module.resolve_instance_matches_in_region("ap-northeast-2", "10.0.0.15", "ip")
    except AwsOperationError as error:
        assert calls == ["private-ip-address", "ip-address"]
        assert error.operation == "ec2.describe_instances"
        assert error.region == "ap-northeast-2"
    else:
        raise AssertionError("Expected resolve_instance_matches_in_region to wrap second lookup errors")


def test_resolve_instance_matches_skips_cached_regions_deduplicates_and_reports_first_match(monkeypatch):
    monkeypatch.setattr(instances_module, "get_enabled_regions", lambda: ["ap-northeast-2", "us-west-2", "eu-west-1"])
    monkeypatch.setattr(
        instances_module,
        "get_region_failure_entry",
        lambda region: {"expires_at": 2_000, "error": "cached timeout"} if region == "us-west-2" else None,
    )
    monkeypatch.setattr(instances_module.time, "time", lambda: 1_000)
    monkeypatch.setattr(instances_module, "classify_target", lambda target: "name")
    monkeypatch.setattr(instances_module, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(instances_module, "as_completed", lambda futures: list(futures))
    secho_calls = []
    monkeypatch.setattr(instances_module.typer, "secho", lambda message, **kwargs: secho_calls.append(message))

    def fake_resolve(region, target, target_kind, connect_timeout, read_timeout, max_attempts):
        if region == "ap-northeast-2":
            return [
                {
                    "region": region,
                    "instance_id": "i-1",
                    "private_ip": None,
                    "public_ip": None,
                    "state": "running",
                    "name": "web",
                }
            ]
        return [
            {
                "region": "ap-northeast-2",
                "instance_id": "i-1",
                "private_ip": None,
                "public_ip": None,
                "state": "running",
                "name": "web",
            },
            {
                "region": region,
                "instance_id": "i-2",
                "private_ip": None,
                "public_ip": None,
                "state": "running",
                "name": "web-2",
            },
        ]

    monkeypatch.setattr(instances_module, "resolve_instance_matches_in_region", fake_resolve)

    first_matches = []
    matches = instances_module.resolve_instance_matches("web", on_first_match=first_matches.append)

    assert [match["instance_id"] for match in matches] == ["i-1", "i-2"]
    assert first_matches == [matches[0]]
    assert any("Skipping region [us-west-2]" in message for message in secho_calls)


def test_resolve_instance_matches_caches_skippable_region_failures(monkeypatch):
    monkeypatch.setattr(instances_module, "get_enabled_regions", lambda: ["ap-northeast-2", "us-west-2"])
    monkeypatch.setattr(instances_module, "get_region_failure_entry", lambda region: None)
    monkeypatch.setattr(instances_module, "classify_target", lambda target: "name")
    monkeypatch.setattr(instances_module, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(instances_module, "as_completed", lambda futures: list(futures))
    monkeypatch.setattr(instances_module, "is_skippable_region_error", lambda error: True)
    cached = []
    secho_calls = []
    monkeypatch.setattr(instances_module, "cache_region_failure", lambda region, error: cached.append((region, str(error))))
    monkeypatch.setattr(instances_module.typer, "secho", lambda message, **kwargs: secho_calls.append(message))

    def fake_resolve(region, target, target_kind, connect_timeout, read_timeout, max_attempts):
        if region == "ap-northeast-2":
            return []
        raise AwsOperationError(
            operation="ec2.describe_instances",
            error=ConnectTimeoutError(endpoint_url="https://ec2.amazonaws.com"),
            region=region,
            profile="default",
        )

    monkeypatch.setattr(instances_module, "resolve_instance_matches_in_region", fake_resolve)

    matches = instances_module.resolve_instance_matches("web")

    assert matches == []
    assert cached == [("us-west-2", "Connect timeout on endpoint URL: \"https://ec2.amazonaws.com\"")]
    assert any("Warning: skipping region [us-west-2]" in message for message in secho_calls)


def test_describe_instances_by_ids_in_region_returns_empty_for_empty_input():
    assert instances_module.describe_instances_by_ids_in_region("ap-northeast-2", []) == []
