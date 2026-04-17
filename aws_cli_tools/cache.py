import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .constants import (
    CACHE_DIR,
    INSTANCE_ID_CACHE_TTL_SECONDS,
    IP_CACHE_TTL_SECONDS,
    REGION_FAILURE_CACHE_FILE,
    REGION_FAILURE_CACHE_TTL_SECONDS,
    RESOLVE_CACHE_FILE,
    SSM_TARGETS_CACHE_FILE,
    SSM_TARGETS_CACHE_TTL_SECONDS,
)
from .models import InstanceMatch
from .targets import is_instance_id


def _load_json_cache(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}

    try:
        with open(path, "r") as cache_file:
            data = json.load(cache_file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_json_cache(path: Path, cache_data: Dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as cache_file:
        json.dump(cache_data, cache_file, indent=2, sort_keys=True)


def get_cache_ttl_seconds(target: str) -> int:
    """Return cache TTL based on target type."""
    return INSTANCE_ID_CACHE_TTL_SECONDS if is_instance_id(target) else IP_CACHE_TTL_SECONDS


def load_resolve_cache() -> Dict[str, Any]:
    """Load the resolver cache from disk."""
    return _load_json_cache(RESOLVE_CACHE_FILE)


def save_resolve_cache(cache_data: Dict[str, Any]) -> None:
    """Persist the resolver cache to disk."""
    _save_json_cache(RESOLVE_CACHE_FILE, cache_data)


def load_region_failure_cache() -> Dict[str, Any]:
    """Load the per-region failure cache from disk."""
    return _load_json_cache(REGION_FAILURE_CACHE_FILE)


def save_region_failure_cache(cache_data: Dict[str, Any]) -> None:
    """Persist the per-region failure cache to disk."""
    _save_json_cache(REGION_FAILURE_CACHE_FILE, cache_data)


def load_ssm_targets_cache() -> Dict[str, Any]:
    """Load the per-region SSM target cache from disk."""
    return _load_json_cache(SSM_TARGETS_CACHE_FILE)


def save_ssm_targets_cache(cache_data: Dict[str, Any]) -> None:
    """Persist the per-region SSM target cache to disk."""
    _save_json_cache(SSM_TARGETS_CACHE_FILE, cache_data)


def get_region_failure_entry(region: str) -> Optional[Dict[str, Any]]:
    """Return a valid cached region failure entry when it is still fresh."""
    cache_data = load_region_failure_cache()
    entry = cache_data.get(region)
    if not isinstance(entry, dict):
        return None

    expires_at = entry.get("expires_at")
    if not isinstance(expires_at, (int, float)):
        return None

    if expires_at <= time.time():
        cache_data.pop(region, None)
        save_region_failure_cache(cache_data)
        return None

    return entry


def cache_region_failure(
    region: str,
    error: Exception,
    ttl_seconds: int = REGION_FAILURE_CACHE_TTL_SECONDS,
) -> None:
    """Store a temporary region failure entry so repeated timeouts can be skipped."""
    current_time = int(time.time())
    cache_data = load_region_failure_cache()
    cache_data[region] = {
        "cached_at": current_time,
        "expires_at": current_time + ttl_seconds,
        "error": str(error),
    }
    save_region_failure_cache(cache_data)


def get_cached_ssm_targets(region: str) -> Optional[List[InstanceMatch]]:
    """Return cached SSM browser targets for a region when they are still fresh."""
    cache_data = load_ssm_targets_cache()
    entry = cache_data.get(region)
    if not isinstance(entry, dict):
        return None

    expires_at = entry.get("expires_at")
    matches = entry.get("matches")
    if not isinstance(expires_at, (int, float)) or not isinstance(matches, list):
        return None

    if expires_at <= time.time():
        cache_data.pop(region, None)
        save_ssm_targets_cache(cache_data)
        return None

    return matches


def cache_ssm_targets(
    region: str,
    matches: List[InstanceMatch],
    ttl_seconds: int = SSM_TARGETS_CACHE_TTL_SECONDS,
) -> None:
    """Store SSM browser targets for a region."""
    current_time = int(time.time())
    cache_data = load_ssm_targets_cache()
    cache_data[region] = {
        "cached_at": current_time,
        "expires_at": current_time + ttl_seconds,
        "matches": matches,
    }
    save_ssm_targets_cache(cache_data)


def get_cached_resolve_result(target: str) -> Optional[List[InstanceMatch]]:
    """Return a cached resolver result when it is still fresh."""
    cache_data = load_resolve_cache()
    entry = cache_data.get(target)
    if not isinstance(entry, dict):
        return None

    expires_at = entry.get("expires_at")
    matches = entry.get("matches")
    if not isinstance(expires_at, (int, float)) or not isinstance(matches, list):
        return None

    if expires_at <= time.time():
        cache_data.pop(target, None)
        save_resolve_cache(cache_data)
        return None

    return matches


def cache_resolve_result(target: str, matches: List[InstanceMatch]) -> None:
    """Store a single-match resolver result in the cache."""
    current_time = int(time.time())
    cache_data = load_resolve_cache()
    cache_data[target] = {
        "cached_at": current_time,
        "expires_at": current_time + get_cache_ttl_seconds(target),
        "matches": matches,
    }
    save_resolve_cache(cache_data)
