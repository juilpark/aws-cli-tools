import configparser
import os
from typing import List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .constants import (
    AWS_CONFIG_FILE,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_PROFILE,
    DEFAULT_READ_TIMEOUT_SECONDS,
    DEFAULT_STS_REGION,
    REGION_PRIORITY_ENV_VAR,
)
from .errors import AwsOperationError


def get_default_session() -> boto3.Session:
    """Create a boto3 session bound to the default AWS profile."""
    return boto3.Session(profile_name=DEFAULT_PROFILE)


def build_boto_config(
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Config:
    """Build a botocore config with explicit network timeouts."""
    return Config(
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries={"total_max_attempts": max_attempts, "mode": "standard"},
    )


def parse_region_priority_env() -> List[str]:
    """Parse the optional region-priority environment variable."""
    raw_value = os.getenv(REGION_PRIORITY_ENV_VAR, "")
    if not raw_value.strip():
        return []

    ordered_regions: List[str] = []
    seen = set()
    for region in raw_value.split(","):
        normalized = region.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered_regions.append(normalized)
    return ordered_regions


def order_regions_by_priority(regions: List[str]) -> List[str]:
    """Move prioritized regions to the front while preserving relative order."""
    priority_regions = parse_region_priority_env()
    if not priority_regions:
        return sorted(regions)

    available_regions = set(regions)
    prioritized = [region for region in priority_regions if region in available_regions]
    prioritized_set = set(prioritized)
    remaining = sorted(region for region in regions if region not in prioritized_set)
    return prioritized + remaining


def get_all_regions() -> List[str]:
    """Fetch all EC2 regions using the default profile."""
    session = get_default_session()
    ec2 = session.client("ec2", region_name="us-east-1", config=build_boto_config())
    try:
        response = ec2.describe_regions()
        return order_regions_by_priority([region["RegionName"] for region in response["Regions"]])
    except (BotoCoreError, ClientError) as error:
        raise AwsOperationError(
            operation="ec2.describe_regions",
            error=error,
            region="us-east-1",
            profile=DEFAULT_PROFILE,
        ) from error


def get_enabled_regions() -> List[str]:
    """Fetch regions that are available to the account."""
    session = get_default_session()
    ec2 = session.client("ec2", region_name="us-east-1", config=build_boto_config())
    try:
        response = ec2.describe_regions(AllRegions=True)
        return order_regions_by_priority(
            [
                region["RegionName"]
                for region in response["Regions"]
                if region.get("OptInStatus") in {"opt-in-not-required", "opted-in"}
            ]
        )
    except (BotoCoreError, ClientError) as error:
        raise AwsOperationError(
            operation="ec2.describe_regions",
            error=error,
            region="us-east-1",
            profile=DEFAULT_PROFILE,
        ) from error


def find_config_section(config: configparser.ConfigParser, profile_name: str) -> Optional[str]:
    """Helper to find the correct section name in config file."""
    if profile_name == DEFAULT_PROFILE:
        return DEFAULT_PROFILE if DEFAULT_PROFILE in config.sections() else None

    standard_name = f"profile {profile_name}"
    if standard_name in config.sections():
        return standard_name
    if profile_name in config.sections():
        return profile_name
    return None


def get_profile_region(profile_name: str, session: Optional[boto3.Session] = None) -> str:
    """Return the configured AWS region for a profile, with a safe fallback."""
    if session and session.region_name:
        return session.region_name

    aws_config = configparser.ConfigParser()
    if AWS_CONFIG_FILE.exists():
        aws_config.read(AWS_CONFIG_FILE)
        section_name = find_config_section(aws_config, profile_name)
        if section_name and aws_config.has_option(section_name, "region"):
            return aws_config.get(section_name, "region")

    return DEFAULT_STS_REGION

