import json

import aws_cli_tools.cache as cache_module


def test_get_cache_ttl_seconds_uses_shorter_ttl_for_ip_targets():
    assert cache_module.get_cache_ttl_seconds("i-0123456789abcdef0") == cache_module.INSTANCE_ID_CACHE_TTL_SECONDS
    assert cache_module.get_cache_ttl_seconds("10.0.0.15") == cache_module.IP_CACHE_TTL_SECONDS


def test_cache_resolve_result_round_trips_single_match(
    isolated_cache_paths,
    monkeypatch,
    sample_match,
):
    monkeypatch.setattr(cache_module.time, "time", lambda: 1_700_000_000)

    cache_module.cache_resolve_result("i-0123456789abcdef0", sample_match)

    assert cache_module.get_cached_resolve_result("i-0123456789abcdef0") == sample_match
    assert json.loads(cache_module.RESOLVE_CACHE_FILE.read_text())["i-0123456789abcdef0"]["matches"] == sample_match


def test_get_cached_resolve_result_drops_expired_entries(
    isolated_cache_paths,
    monkeypatch,
    sample_match,
):
    cache_module.save_resolve_cache(
        {
            "i-0123456789abcdef0": {
                "cached_at": 10,
                "expires_at": 11,
                "matches": sample_match,
            }
        }
    )
    monkeypatch.setattr(cache_module.time, "time", lambda: 12)

    assert cache_module.get_cached_resolve_result("i-0123456789abcdef0") is None
    assert json.loads(cache_module.RESOLVE_CACHE_FILE.read_text()) == {}


def test_cache_region_failure_round_trips_active_entry(
    isolated_cache_paths,
    monkeypatch,
):
    monkeypatch.setattr(cache_module.time, "time", lambda: 2_000)

    cache_module.cache_region_failure("ap-northeast-2", RuntimeError("timeout"), ttl_seconds=60)

    assert cache_module.get_region_failure_entry("ap-northeast-2") == {
        "cached_at": 2000,
        "expires_at": 2060,
        "error": "timeout",
    }


def test_get_region_failure_entry_drops_expired_entries(
    isolated_cache_paths,
    monkeypatch,
):
    cache_module.save_region_failure_cache(
        {
            "ap-northeast-2": {
                "cached_at": 1,
                "expires_at": 2,
                "error": "timeout",
            }
        }
    )
    monkeypatch.setattr(cache_module.time, "time", lambda: 3)

    assert cache_module.get_region_failure_entry("ap-northeast-2") is None
    assert json.loads(cache_module.REGION_FAILURE_CACHE_FILE.read_text()) == {}

