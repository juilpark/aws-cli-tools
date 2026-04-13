import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import typer
from botocore.exceptions import BotoCoreError, ClientError

from .aws_common import build_boto_config, get_default_session, get_enabled_regions
from .cache import cache_region_failure, get_region_failure_entry
from .constants import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_MAX_ATTEMPTS, DEFAULT_PROFILE, DEFAULT_READ_TIMEOUT_SECONDS
from .errors import AwsOperationError, is_skippable_region_error
from .models import InstanceMatch
from .targets import TargetKind, classify_target


def extract_instance_matches(reservations: Sequence[Dict[str, object]], region: str) -> List[InstanceMatch]:
    """Normalize EC2 instance results into a simple list of matches."""
    matches: List[InstanceMatch] = []
    for reservation in reservations:
        instances = reservation.get("Instances", []) if isinstance(reservation, dict) else []
        for instance in instances:
            if not isinstance(instance, dict):
                continue
            tags = {
                tag["Key"]: tag["Value"]
                for tag in instance.get("Tags", [])
                if isinstance(tag, dict) and "Key" in tag and "Value" in tag
            }
            matches.append(
                {
                    "region": region,
                    "instance_id": instance["InstanceId"],
                    "private_ip": instance.get("PrivateIpAddress"),
                    "public_ip": instance.get("PublicIpAddress"),
                    "state": instance.get("State", {}).get("Name"),
                    "name": tags.get("Name"),
                }
            )
    return matches


def _paginate_matches_for_target(
    region: str,
    filter_name: str,
    filter_values: List[str],
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[InstanceMatch]:
    session = get_default_session()
    ec2 = session.client(
        "ec2",
        region_name=region,
        config=build_boto_config(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_attempts=max_attempts,
        ),
    )
    matches: List[InstanceMatch] = []

    paginator = ec2.get_paginator("describe_instances")
    if filter_name == "instance-id":
        page_iterator = paginator.paginate(InstanceIds=filter_values)
    else:
        page_iterator = paginator.paginate(Filters=[{"Name": filter_name, "Values": filter_values}])

    for page in page_iterator:
        matches.extend(extract_instance_matches(page.get("Reservations", []), region))

    return matches


def resolve_instance_matches_in_region(
    region: str,
    target: str,
    target_kind: TargetKind,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[InstanceMatch]:
    """Resolve an instance target within a single region."""
    try:
        matches = _paginate_matches_for_target(
            region,
            "instance-id" if target_kind == "instance_id" else ("private-ip-address" if target_kind == "ip" else "tag:Name"),
            [target],
            connect_timeout,
            read_timeout,
            max_attempts,
        )
    except ClientError as error:
        if target_kind == "instance_id" and error.response.get("Error", {}).get("Code") == "InvalidInstanceID.NotFound":
            return []
        raise AwsOperationError(
            operation="ec2.describe_instances",
            error=error,
            region=region,
            profile=DEFAULT_PROFILE,
        ) from error
    except BotoCoreError as error:
        raise AwsOperationError(
            operation="ec2.describe_instances",
            error=error,
            region=region,
            profile=DEFAULT_PROFILE,
        ) from error

    if target_kind != "ip":
        return matches

    try:
        matches.extend(
            _paginate_matches_for_target(
                region,
                "ip-address",
                [target],
                connect_timeout,
                read_timeout,
                max_attempts,
            )
        )
    except ClientError as error:
        raise AwsOperationError(
            operation="ec2.describe_instances",
            error=error,
            region=region,
            profile=DEFAULT_PROFILE,
        ) from error
    except BotoCoreError as error:
        raise AwsOperationError(
            operation="ec2.describe_instances",
            error=error,
            region=region,
            profile=DEFAULT_PROFILE,
        ) from error

    return matches


def resolve_instance_matches(
    target: str,
    on_first_match: Optional[Callable[[InstanceMatch], None]] = None,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[InstanceMatch]:
    """Resolve an EC2 instance by instance id or IP across enabled regions."""
    regions = get_enabled_regions()
    target_kind = classify_target(target)
    matches: List[InstanceMatch] = []
    seen: set[Tuple[str, str]] = set()
    first_match_reported = False

    active_regions: List[str] = []
    for region in regions:
        failure_entry = get_region_failure_entry(region)
        if failure_entry is not None:
            expires_at = int(failure_entry["expires_at"])
            remaining_seconds = max(0, expires_at - int(time.time()))
            typer.secho(
                f"Skipping region [{region}] due to cached failure for {remaining_seconds}s more: {failure_entry.get('error', 'unknown error')}",
                fg=typer.colors.YELLOW,
                err=True,
            )
            continue
        active_regions.append(region)

    max_workers = min(12, len(active_regions)) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_region = {
            executor.submit(
                resolve_instance_matches_in_region,
                region,
                target,
                target_kind,
                connect_timeout,
                read_timeout,
                max_attempts,
            ): region
            for region in active_regions
        }

        for future in as_completed(future_to_region):
            region = future_to_region[future]
            try:
                region_matches = future.result()
            except AwsOperationError as error:
                if is_skippable_region_error(error):
                    cache_region_failure(region, error.error)
                    typer.secho(
                        f"Warning: skipping region [{region}] due to network timeout/error: {error.error}",
                        fg=typer.colors.YELLOW,
                        err=True,
                    )
                    continue
                raise

            for match in region_matches:
                key = (match["region"], match["instance_id"])
                if key in seen:
                    continue

                seen.add(key)
                matches.append(match)
                if not first_match_reported and on_first_match is not None:
                    on_first_match(match)
                    first_match_reported = True

    return matches


def describe_instances_by_ids_in_region(
    region: str,
    instance_ids: List[str],
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[InstanceMatch]:
    """Fetch normalized EC2 instance metadata for specific instance ids in a region."""
    if not instance_ids:
        return []

    session = get_default_session()
    ec2 = session.client(
        "ec2",
        region_name=region,
        config=build_boto_config(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_attempts=max_attempts,
        ),
    )
    matches: List[InstanceMatch] = []

    try:
        for index in range(0, len(instance_ids), 100):
            chunk = instance_ids[index:index + 100]
            response = ec2.describe_instances(InstanceIds=chunk)
            matches.extend(extract_instance_matches(response.get("Reservations", []), region))
    except ClientError as error:
        raise AwsOperationError(
            operation="ec2.describe_instances",
            error=error,
            region=region,
            profile=DEFAULT_PROFILE,
        ) from error
    except BotoCoreError as error:
        raise AwsOperationError(
            operation="ec2.describe_instances",
            error=error,
            region=region,
            profile=DEFAULT_PROFILE,
        ) from error

    return matches
