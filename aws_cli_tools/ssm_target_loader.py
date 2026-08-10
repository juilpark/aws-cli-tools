from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from typing import Callable, List, Optional

from .aws_common import get_enabled_regions
from .cache import cache_region_failure, cache_ssm_targets, get_cached_ssm_targets, get_region_failure_entry
from .constants import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_MAX_ATTEMPTS, DEFAULT_READ_TIMEOUT_SECONDS
from .errors import AwsOperationError, is_skippable_region_error
from .models import InstanceMatch
from .ssm_targets import list_ssm_candidates_in_region

RegionMatchesCallback = Callable[[str, List[InstanceMatch]], None]
RegionSkippedCallback = Callable[[str, str], None]
LoadingStartedCallback = Callable[[int], None]
LoadingFinishedCallback = Callable[[], None]


def load_ssm_target_candidates(
    *,
    use_cached_results: bool = True,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    on_loading_started: Optional[LoadingStartedCallback] = None,
    on_region_cached: Optional[RegionMatchesCallback] = None,
    on_region_loaded: Optional[RegionMatchesCallback] = None,
    on_region_skipped: Optional[RegionSkippedCallback] = None,
    on_loading_finished: Optional[LoadingFinishedCallback] = None,
) -> List[InstanceMatch]:
    """Load online SSM-managed EC2 targets across enabled regions."""
    regions = get_enabled_regions()
    if on_loading_started is not None:
        on_loading_started(len(regions))

    matches_by_region: dict[str, List[InstanceMatch]] = {}
    if use_cached_results:
        for region in regions:
            matches = get_cached_ssm_targets(region)
            if matches is None:
                continue

            matches_by_region[region] = matches
            if on_region_cached is not None:
                on_region_cached(region, matches)

    active_regions: List[str] = []
    for region in regions:
        failure_entry = get_region_failure_entry(region)
        if failure_entry is not None:
            expires_at = int(failure_entry["expires_at"])
            remaining_seconds = max(0, expires_at - int(time.time()))
            if on_region_skipped is not None:
                on_region_skipped(
                    region,
                    f"cached failure for {remaining_seconds}s more: {failure_entry.get('error', 'unknown error')}",
                )
            continue

        active_regions.append(region)

    max_workers = min(12, len(active_regions)) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_region = {
            executor.submit(
                list_ssm_candidates_in_region,
                region,
                connect_timeout,
                read_timeout,
                max_attempts,
            ): region
            for region in active_regions
        }

        for future in as_completed(future_to_region):
            region = future_to_region[future]
            try:
                matches = future.result()
            except AwsOperationError as error:
                if is_skippable_region_error(error):
                    cache_region_failure(region, error.error)
                    if on_region_skipped is not None:
                        on_region_skipped(region, f"network timeout/error: {error.error}")
                    continue
                raise

            cache_ssm_targets(region, matches)
            matches_by_region[region] = matches
            if on_region_loaded is not None:
                on_region_loaded(region, matches)

    if on_loading_finished is not None:
        on_loading_finished()

    ordered_matches: List[InstanceMatch] = []
    for region in regions:
        ordered_matches.extend(matches_by_region.get(region, []))
    return ordered_matches
