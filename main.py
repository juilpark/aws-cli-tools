import os
import configparser
import subprocess
import shlex
import shutil
import re
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, List, Dict, Any

import boto3
import typer
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError
from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import DataTable, Footer, Header, Input, Static

# Load environment variables from .env
load_dotenv()

app = typer.Typer(help="AWS CLI Tools")
console = Console()
AWS_DOT_AWS_DIR = Path.home() / ".aws"
AWS_CREDENTIALS_FILE = AWS_DOT_AWS_DIR / "credentials"
AWS_CONFIG_FILE = AWS_DOT_AWS_DIR / "config"
DEFAULT_PROFILE = "default"
DEFAULT_STS_REGION = "ap-northeast-2"
CACHE_DIR = Path.home() / ".cache" / "aws-cli-tools"
RESOLVE_CACHE_FILE = CACHE_DIR / "resolve-instance.json"
REGION_FAILURE_CACHE_FILE = CACHE_DIR / "region-failures.json"
INSTANCE_ID_CACHE_TTL_SECONDS = 300
IP_CACHE_TTL_SECONDS = 60
DEFAULT_CONNECT_TIMEOUT_SECONDS = 3
DEFAULT_READ_TIMEOUT_SECONDS = 5
DEFAULT_MAX_ATTEMPTS = 1
REGION_FAILURE_CACHE_TTL_SECONDS = 300
REGION_PRIORITY_ENV_VAR = "AWS_REGION_PRIORITY"


class AwsOperationError(Exception):
    """Wrap AWS SDK errors with operation context."""

    def __init__(self, operation: str, error: Exception, region: Optional[str] = None, profile: Optional[str] = None):
        self.operation = operation
        self.error = error
        self.region = region
        self.profile = profile
        super().__init__(str(error))


def is_skippable_region_error(error: Exception) -> bool:
    """Return True when a per-region error should not fail the entire lookup."""
    if isinstance(error, AwsOperationError):
        error = error.error

    return isinstance(error, (ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError))


def is_instance_id(value: str) -> bool:
    """Return True when the input looks like an EC2 instance id."""
    return re.fullmatch(r"i-[0-9a-f]+", value) is not None


def is_ipv4_address(value: str) -> bool:
    """Return True when the input looks like an IPv4 address."""
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", value) is None:
        return False

    octets = value.split(".")
    return all(0 <= int(octet) <= 255 for octet in octets)


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


def get_cache_ttl_seconds(target: str) -> int:
    """Return cache TTL based on target type."""
    return INSTANCE_ID_CACHE_TTL_SECONDS if is_instance_id(target) else IP_CACHE_TTL_SECONDS


def parse_region_priority_env() -> List[str]:
    """Parse the optional region-priority environment variable."""
    raw_value = os.getenv(REGION_PRIORITY_ENV_VAR, "")
    if not raw_value.strip():
        return []

    ordered_regions: List[str] = []
    seen: set[str] = set()
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
    remaining = sorted(region for region in regions if region not in set(prioritized))
    return prioritized + remaining


def load_resolve_cache() -> Dict[str, Any]:
    """Load the resolver cache from disk."""
    if not RESOLVE_CACHE_FILE.exists():
        return {}

    try:
        with open(RESOLVE_CACHE_FILE, "r") as cache_file:
            data = json.load(cache_file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_resolve_cache(cache_data: Dict[str, Any]):
    """Persist the resolver cache to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESOLVE_CACHE_FILE, "w") as cache_file:
        json.dump(cache_data, cache_file, indent=2, sort_keys=True)


def load_region_failure_cache() -> Dict[str, Any]:
    """Load the per-region failure cache from disk."""
    if not REGION_FAILURE_CACHE_FILE.exists():
        return {}

    try:
        with open(REGION_FAILURE_CACHE_FILE, "r") as cache_file:
            data = json.load(cache_file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_region_failure_cache(cache_data: Dict[str, Any]):
    """Persist the per-region failure cache to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(REGION_FAILURE_CACHE_FILE, "w") as cache_file:
        json.dump(cache_data, cache_file, indent=2, sort_keys=True)


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


def cache_region_failure(region: str, error: Exception, ttl_seconds: int = REGION_FAILURE_CACHE_TTL_SECONDS):
    """Store a temporary region failure entry so repeated timeouts can be skipped."""
    cache_data = load_region_failure_cache()
    cache_data[region] = {
        "cached_at": int(time.time()),
        "expires_at": int(time.time()) + ttl_seconds,
        "error": str(error),
    }
    save_region_failure_cache(cache_data)


def get_cached_resolve_result(target: str) -> Optional[List[Dict[str, Any]]]:
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


def cache_resolve_result(target: str, matches: List[Dict[str, Any]]):
    """Store a single-match resolver result in the cache."""
    cache_data = load_resolve_cache()
    cache_data[target] = {
        "cached_at": int(time.time()),
        "expires_at": int(time.time()) + get_cache_ttl_seconds(target),
        "matches": matches,
    }
    save_resolve_cache(cache_data)


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


def extract_instance_matches(reservations: List[Dict[str, Any]], region: str) -> List[Dict[str, Any]]:
    """Normalize EC2 instance results into a simple list of matches."""
    matches: List[Dict[str, Any]] = []
    for reservation in reservations:
        for instance in reservation.get("Instances", []):
            tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", []) if "Key" in tag and "Value" in tag}
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


def resolve_instance_matches_in_region(
    region: str,
    target: str,
    target_kind: str,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[Dict[str, Any]]:
    """Resolve an instance target within a single region."""
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
    matches: List[Dict[str, Any]] = []

    try:
        paginator = ec2.get_paginator("describe_instances")
        if target_kind == "instance_id":
            page_iterator = paginator.paginate(InstanceIds=[target])
        elif target_kind == "ip":
            page_iterator = paginator.paginate(
                Filters=[
                    {
                        "Name": "private-ip-address",
                        "Values": [target],
                    }
                ]
            )
        else:
            page_iterator = paginator.paginate(
                Filters=[
                    {
                        "Name": "tag:Name",
                        "Values": [target],
                    }
                ]
            )

        for page in page_iterator:
            matches.extend(extract_instance_matches(page.get("Reservations", []), region))
    except ClientError as error:
        if target_kind == "instance_id" and error.response.get("Error", {}).get("Code") == "InvalidInstanceID.NotFound":
            return matches
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
        paginator = ec2.get_paginator("describe_instances")
        page_iterator = paginator.paginate(
            Filters=[
                {
                    "Name": "ip-address",
                    "Values": [target],
                }
            ]
        )
        for page in page_iterator:
            matches.extend(extract_instance_matches(page.get("Reservations", []), region))
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
    on_first_match: Optional[Any] = None,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[Dict[str, Any]]:
    """Resolve an EC2 instance by instance id or IP across enabled regions."""
    regions = get_enabled_regions()
    matches: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    first_match_reported = False
    if is_instance_id(target):
        target_kind = "instance_id"
    elif is_ipv4_address(target):
        target_kind = "ip"
    else:
        target_kind = "name"

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
                if key not in seen:
                    seen.add(key)
                    matches.append(match)
                    if not first_match_reported and on_first_match is not None:
                        on_first_match(match)
                        first_match_reported = True

    return matches


def build_ssm_command(match: Dict[str, Any]) -> List[str]:
    """Build the AWS CLI command used to start an SSM session."""
    return [
        "aws",
        "ssm",
        "start-session",
        "--target",
        match["instance_id"],
        "--region",
        match["region"],
        "--profile",
        DEFAULT_PROFILE,
    ]


def describe_instances_by_ids_in_region(
    region: str,
    instance_ids: List[str],
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[Dict[str, Any]]:
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
    matches: List[Dict[str, Any]] = []

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


def list_ssm_candidates_in_region(
    region: str,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[Dict[str, Any]]:
    """List online SSM-managed EC2 instances in a single region."""
    session = get_default_session()
    ssm_client = session.client(
        "ssm",
        region_name=region,
        config=build_boto_config(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_attempts=max_attempts,
        ),
    )

    instance_ids: List[str] = []
    try:
        paginator = ssm_client.get_paginator("describe_instance_information")
        for page in paginator.paginate(
            Filters=[
                {"Key": "PingStatus", "Values": ["Online"]},
                {"Key": "ResourceType", "Values": ["EC2Instance"]},
            ]
        ):
            for info in page.get("InstanceInformationList", []):
                instance_id = info.get("InstanceId")
                if instance_id and instance_id.startswith("i-"):
                    instance_ids.append(instance_id)
    except ClientError as error:
        raise AwsOperationError(
            operation="ssm.describe_instance_information",
            error=error,
            region=region,
            profile=DEFAULT_PROFILE,
        ) from error
    except BotoCoreError as error:
        raise AwsOperationError(
            operation="ssm.describe_instance_information",
            error=error,
            region=region,
            profile=DEFAULT_PROFILE,
        ) from error

    matches = describe_instances_by_ids_in_region(
        region,
        sorted(set(instance_ids)),
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        max_attempts=max_attempts,
    )
    return sorted(
        matches,
        key=lambda match: (
            (match.get("name") or "").lower(),
            match["region"],
            match["instance_id"],
        ),
    )


def print_instance_matches(matches: List[Dict[str, Any]]):
    """Print resolved instance matches in a readable table."""
    table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("Region", style="green")
    table.add_column("Name", style="bold")
    table.add_column("Instance ID", style="magenta")
    table.add_column("Private IP")
    table.add_column("Public IP")
    table.add_column("State")

    for match in matches:
        table.add_row(
            match["region"],
            match.get("name") or "-",
            match["instance_id"],
            match.get("private_ip") or "-",
            match.get("public_ip") or "-",
            match.get("state") or "unknown",
        )

    console.print(table)


class SsmSelectionApp(App[Optional[Dict[str, Any]]]):
    """Interactive Textual app for browsing and selecting SSM targets."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        height: 1fr;
    }

    #status {
        height: 2;
        padding: 0 1;
        content-align: left middle;
    }

    #search {
        margin: 0 1;
    }

    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("enter", "connect", "Connect"),
        ("/", "focus_search", "Search"),
        ("ctrl+l", "clear_search", "Clear Search"),
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
    ]

    class RegionLoaded(Message):
        def __init__(self, region: str, matches: List[Dict[str, Any]]) -> None:
            self.region = region
            self.matches = matches
            super().__init__()

    class RegionSkipped(Message):
        def __init__(self, region: str, detail: str) -> None:
            self.region = region
            self.detail = detail
            super().__init__()

    class LoadingStarted(Message):
        def __init__(self, total_regions: int) -> None:
            self.total_regions = total_regions
            super().__init__()

    class LoadingFinished(Message):
        def __init__(self) -> None:
            super().__init__()

    class LoadingFailed(Message):
        def __init__(self, error: Exception) -> None:
            self.error = error
            super().__init__()

    def __init__(
        self,
        *,
        initial_matches: Optional[List[Dict[str, Any]]] = None,
        title_text: str = "AWS SSM Targets",
        status_text: str = "Use arrow keys to move, Enter to connect, Q to quit.",
        live_load: bool = False,
        connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        super().__init__()
        self.title = title_text
        self.sub_title = "Arrow keys to move, Enter to connect"
        self.initial_matches = initial_matches or []
        self.initial_status_text = status_text
        self.live_load = live_load
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_attempts = max_attempts
        self.total_regions = 0
        self.completed_regions = 0
        self.total_matches = 0
        self.search_query = ""
        self.all_row_keys: List[str] = []
        self.row_order: List[str] = []
        self.matches_by_key: Dict[str, Dict[str, Any]] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            yield Static(self.initial_status_text, id="status")
            yield Input(placeholder="Press / to search...", id="search")
            yield DataTable(id="instances")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#instances", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("Region", "Name", "Instance ID", "Private IP", "Public IP", "State")
        table.focus()

        for match in self.initial_matches:
            self.add_match_row(match)

        if self.live_load:
            self.run_worker(self.load_candidates, thread=True, exclusive=True)
        elif self.initial_matches:
            self.update_status()

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_clear_search(self) -> None:
        search_input = self.query_one("#search", Input)
        search_input.value = ""
        self.search_query = ""
        self.refresh_table()
        self.query_one("#instances", DataTable).focus()

    def load_candidates(self) -> None:
        try:
            regions = get_enabled_regions()
        except Exception as error:
            self.post_message(self.LoadingFailed(error))
            return

        self.post_message(self.LoadingStarted(len(regions)))

        active_regions: List[str] = []
        for region in regions:
            failure_entry = get_region_failure_entry(region)
            if failure_entry is not None:
                expires_at = int(failure_entry["expires_at"])
                remaining_seconds = max(0, expires_at - int(time.time()))
                self.post_message(
                    self.RegionSkipped(
                        region,
                        f"cached failure for {remaining_seconds}s more: {failure_entry.get('error', 'unknown error')}",
                    )
                )
                continue
            active_regions.append(region)

        max_workers = min(12, len(active_regions)) or 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_region = {
                executor.submit(
                    list_ssm_candidates_in_region,
                    region,
                    self.connect_timeout,
                    self.read_timeout,
                    self.max_attempts,
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
                        self.post_message(self.RegionSkipped(region, f"network timeout/error: {error.error}"))
                        continue
                    self.post_message(self.LoadingFailed(error))
                    return
                except Exception as error:
                    self.post_message(self.LoadingFailed(error))
                    return

                self.post_message(self.RegionLoaded(region, matches))

        self.post_message(self.LoadingFinished())

    def add_match_row(self, match: Dict[str, Any]) -> None:
        row_key = f"{match['region']}::{match['instance_id']}"
        if row_key in self.matches_by_key:
            return

        self.matches_by_key[row_key] = match
        self.all_row_keys.append(row_key)
        self.total_matches += 1
        self.refresh_table()

    def get_match_search_text(self, match: Dict[str, Any]) -> str:
        return " ".join(
            [
                match["region"],
                match.get("name") or "-",
                match["instance_id"],
                match.get("private_ip") or "-",
                match.get("public_ip") or "-",
                match.get("state") or "unknown",
            ]
        ).lower()

    def highlight_text(self, value: str) -> Text:
        text = Text(value)
        if not self.search_query:
            return text

        pattern = re.escape(self.search_query)
        for found in re.finditer(pattern, value, re.IGNORECASE):
            text.stylize("bold", found.start(), found.end())
        return text

    def row_matches_filter(self, match: Dict[str, Any]) -> bool:
        if not self.search_query:
            return True
        return self.search_query.lower() in self.get_match_search_text(match)

    def refresh_table(self) -> None:
        table = self.query_one("#instances", DataTable)
        table.clear(columns=False)
        self.row_order = []

        for row_key in self.all_row_keys:
            match = self.matches_by_key[row_key]
            if not self.row_matches_filter(match):
                continue

            self.row_order.append(row_key)
            table.add_row(
                self.highlight_text(match["region"]),
                self.highlight_text(match.get("name") or "-"),
                self.highlight_text(match["instance_id"]),
                self.highlight_text(match.get("private_ip") or "-"),
                self.highlight_text(match.get("public_ip") or "-"),
                self.highlight_text(match.get("state") or "unknown"),
                key=row_key,
            )

        if self.row_order:
            table.move_cursor(row=0, column=0)

    def update_status(self, extra: Optional[str] = None) -> None:
        status = self.query_one("#status", Static)
        visible_matches = len(self.row_order)
        base_text = (
            f"Loaded {self.total_matches} instance(s)"
            f" from {self.completed_regions}/{self.total_regions} region(s). "
            f"Showing {visible_matches}. Use arrow keys to move, / to search, Enter to connect, Q to quit."
        )
        if not self.live_load:
            base_text = (
                f"{self.total_matches} instance(s) ready. Showing {visible_matches}. "
                "Use arrow keys to move, / to search, Enter to connect, Q to quit."
            )
        if self.search_query:
            base_text = f"{base_text} [search: {self.search_query}]"
        if extra:
            base_text = f"{base_text} [{extra}]"
        status.update(base_text)

    def action_connect(self) -> None:
        if not self.row_order:
            self.bell()
            return

        table = self.query_one("#instances", DataTable)
        cursor_row = table.cursor_row
        if cursor_row < 0 or cursor_row >= len(self.row_order):
            self.bell()
            return

        row_key = self.row_order[cursor_row]
        self.exit(self.matches_by_key[row_key])

    @on(DataTable.RowSelected)
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table = event.control
        cursor_row = table.cursor_row
        if 0 <= cursor_row < len(self.row_order):
            row_key = self.row_order[cursor_row]
            self.exit(self.matches_by_key[row_key])

    @on(Input.Changed, "#search")
    def handle_search_changed(self, event: Input.Changed) -> None:
        self.search_query = event.value.strip()
        self.refresh_table()
        self.update_status()

    @on(Input.Submitted, "#search")
    def handle_search_submitted(self, event: Input.Submitted) -> None:
        if self.row_order:
            self.query_one("#instances", DataTable).focus()
        else:
            event.input.focus()

    @on(LoadingStarted)
    def handle_loading_started(self, message: LoadingStarted) -> None:
        self.total_regions = message.total_regions
        self.completed_regions = 0
        self.total_matches = 0
        self.update_status("loading")

    @on(RegionLoaded)
    def handle_region_loaded(self, message: RegionLoaded) -> None:
        self.completed_regions += 1
        for match in sorted(
            message.matches,
            key=lambda match: ((match.get("name") or "").lower(), match["instance_id"]),
        ):
            self.add_match_row(match)
        self.update_status(f"{message.region} done")

    @on(RegionSkipped)
    def handle_region_skipped(self, message: RegionSkipped) -> None:
        self.completed_regions += 1
        self.update_status(f"{message.region} skipped")

    @on(LoadingFinished)
    def handle_loading_finished(self, message: LoadingFinished) -> None:
        if self.total_matches == 0:
            self.query_one("#status", Static).update(
                "No online SSM-manageable EC2 instances were found. Press Q to quit."
            )
            return
        self.update_status("complete")

    @on(LoadingFailed)
    def handle_loading_failed(self, message: LoadingFailed) -> None:
        self.exit(None, return_code=1, message=f"Failed to load SSM targets: {message.error}")


def print_aws_error(error: Exception):
    """Print AWS errors with extra diagnostic context."""
    operation = None
    region = None
    profile = None
    original_error = error

    if isinstance(error, AwsOperationError):
        operation = error.operation
        region = error.region
        profile = error.profile
        original_error = error.error

    if isinstance(original_error, ClientError):
        error_info = original_error.response.get("Error", {})
        metadata = original_error.response.get("ResponseMetadata", {})
        code = error_info.get("Code", "Unknown")
        message = error_info.get("Message", str(original_error))
        request_id = metadata.get("RequestId", "-")
        status_code = metadata.get("HTTPStatusCode", "-")

        typer.secho("AWS Error", fg=typer.colors.RED, err=True, bold=True)
        if operation:
            typer.echo(f"  operation: {operation}", err=True)
        if region:
            typer.echo(f"  region: {region}", err=True)
        if profile:
            typer.echo(f"  profile: {profile}", err=True)
        typer.echo(f"  code: {code}", err=True)
        typer.echo(f"  message: {message}", err=True)
        typer.echo(f"  http_status: {status_code}", err=True)
        typer.echo(f"  request_id: {request_id}", err=True)
        typer.echo(f"  raw: {original_error}", err=True)

        if code in {"AuthFailure", "UnauthorizedOperation", "InvalidClientTokenId", "ExpiredToken"}:
            typer.secho(
                "Hint: check whether the default profile has valid, non-expired credentials and permission to call EC2.",
                fg=typer.colors.YELLOW,
                err=True,
            )
        return

    typer.secho(f"AWS Error: {original_error}", fg=typer.colors.RED, err=True)

def find_config_section(config: configparser.ConfigParser, profile_name: str) -> Optional[str]:
    """Helper to find the correct section name in config file."""
    if profile_name == "default":
        return "default" if "default" in config.sections() else None
    
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

def print_regions_list(regions: List[str], cols: int = 4):
    """Helper to print regions in a formatted grid."""
    for i in range(0, len(regions), cols):
        row = regions[i:i+cols]
        typer.echo("  " + "  ".join(f"{r:<20}" for r in row))

@app.command()
def login(
    source_profile: str = typer.Option(
        os.getenv("AWS_SOURCE_PROFILE", "example_source_profile"),
        help="The source AWS profile to use for STS authentication. Can also be set via AWS_SOURCE_PROFILE in .env",
    ),
    target_profile: str = typer.Option("default", help="The profile to update in credentials file"),
    duration: int = typer.Option(28800, help="Duration in seconds (default 28800s / 8h)"),
    mfa_serial: Optional[str] = typer.Option(
        os.getenv("AWS_MFA_SERIAL"), 
        help="MFA Serial Number (ARN). Can also be set via AWS_MFA_SERIAL in .env"
    ),
    token_code: Optional[str] = typer.Option(None, help="MFA Token Code if required"),
):
    """
    Get temporary session token from AWS STS and update ~/.aws/credentials & config.
    """
    try:
        # 1. Check for existing credentials and warn if they look like permanent keys
        cred_config = configparser.ConfigParser()
        if AWS_CREDENTIALS_FILE.exists():
            cred_config.read(AWS_CREDENTIALS_FILE)
            if target_profile in cred_config.sections():
                if "aws_session_token" not in cred_config[target_profile]:
                    typer.secho(
                        f"WARNING: Profile [{target_profile}] exists but does not have a session token.",
                        fg=typer.colors.YELLOW, bold=True
                    )
                    typer.secho(
                        "It looks like a permanent IAM User credential. Overwriting it with temporary STS tokens...",
                        fg=typer.colors.YELLOW
                    )

        # 2. Initialize boto3 session and STS client
        session = boto3.Session(profile_name=source_profile)
        sts_region = get_profile_region(source_profile, session=session)
        sts = session.client("sts", region_name=sts_region)

        kwargs = {"DurationSeconds": duration}
        if mfa_serial and token_code:
            kwargs["SerialNumber"] = mfa_serial
            kwargs["TokenCode"] = token_code
        elif mfa_serial and not token_code:
            token_code = typer.prompt(f"Enter MFA Token Code for {mfa_serial}")
            kwargs["SerialNumber"] = mfa_serial
            kwargs["TokenCode"] = token_code

        typer.echo(
            f"Requesting session token using profile [{source_profile}] "
            f"via regional STS endpoint [{sts_region}] for {duration} seconds..."
        )
        response = sts.get_session_token(**kwargs)
        credentials = response["Credentials"]
        
        # 3. Update ~/.aws/credentials
        if not AWS_DOT_AWS_DIR.exists():
            AWS_DOT_AWS_DIR.mkdir(parents=True, exist_ok=True)

        if target_profile not in cred_config.sections():
            cred_config.add_section(target_profile)

        cred_config.set(target_profile, "aws_access_key_id", credentials["AccessKeyId"])
        cred_config.set(target_profile, "aws_secret_access_key", credentials["SecretAccessKey"])
        cred_config.set(target_profile, "aws_session_token", credentials["SessionToken"])

        with open(AWS_CREDENTIALS_FILE, "w") as f:
            cred_config.write(f)
        os.chmod(AWS_CREDENTIALS_FILE, 0o600)

        # 4. Update ~/.aws/config (Sync with source_profile)
        aws_config = configparser.ConfigParser()
        if AWS_CONFIG_FILE.exists():
            aws_config.read(AWS_CONFIG_FILE)
        
        source_section = find_config_section(aws_config, source_profile)
        target_section = "default" if target_profile == "default" else f"profile {target_profile}"

        if source_section:
            if target_section not in aws_config.sections():
                aws_config.add_section(target_section)
            
            for key, value in aws_config.items(source_section):
                aws_config.set(target_section, key, value)
            
            with open(AWS_CONFIG_FILE, "w") as f:
                aws_config.write(f)
            os.chmod(AWS_CONFIG_FILE, 0o600)
            typer.echo(f"Synced config for [{target_section}] from [{source_section}]")
        else:
            typer.secho(
                f"Note: Source profile [{source_profile}] not found in {AWS_CONFIG_FILE}. Skipping config sync.",
                fg=typer.colors.CYAN
            )

        typer.secho(f"Successfully updated [{target_profile}] profile in {AWS_CREDENTIALS_FILE}", fg=typer.colors.GREEN)
        typer.echo(f"Expires at: {credentials['Expiration']}")

    except (AwsOperationError, BotoCoreError, ClientError) as e:
        print_aws_error(e)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.secho(f"An error occurred: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

@app.command()
def region_loop(
    profile: str = typer.Option("default", help="The AWS profile to use for fetching regions and running commands"),
):
    """
    Run an AWS CLI command across all available regions (Interactive Mode).
    """
    try:
        # 0. Get command via interactive prompt
        typer.secho("\n[Interactive Region Loop]", fg=typer.colors.CYAN, bold=True)
        typer.echo("Enter the AWS CLI command exactly as you would run it (e.g., aws ec2 describe-vpcs ...)")
        command = typer.prompt("Command", prompt_suffix="> ", type=str)

        # 1. Fetch available regions
        session = boto3.Session(profile_name=profile)
        # Using us-east-1 as a default to fetch region list
        ec2 = session.client("ec2", region_name="us-east-1")
        
        typer.echo("Fetching available regions...")
        regions_resp = ec2.describe_regions()
        regions = order_regions_by_priority([r["RegionName"] for r in regions_resp["Regions"]])
        
        # Parse the command safely
        parts = shlex.split(command)
        if not parts or parts[0] != "aws":
            typer.secho("Error: Command must start with 'aws'", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        
        # 2. Show Preview
        example_region = regions[0]
        preview_parts = [parts[0], "--region", example_region] + parts[1:]
        if profile != "default" and "--profile" not in parts:
            preview_parts.extend(["--profile", profile])
        
        preview_command = " ".join([shlex.quote(p) for p in preview_parts])
        
        typer.secho("\n[Region Loop Preview]", fg=typer.colors.CYAN, bold=True)
        typer.echo("Target Regions:")
        print_regions_list(regions)
        
        typer.echo(f"\nExample Command: {preview_command}")
        typer.echo(f"Total Regions: {len(regions)}")
        
        # 3. Ask Confirmation
        if not typer.confirm("\nDo you want to run this command across all regions?"):
            typer.echo("Operation cancelled.")
            raise typer.Abort()

        for region in regions:
            typer.secho(f"\n{'-'*20} Region: {region} {'-'*20}", fg=typer.colors.BLUE, bold=True)
            
            # Construct: aws --region <region> [rest of parts]
            new_command = [parts[0], "--region", region] + parts[1:]
            
            # If a profile was specified and not in the command string, add it
            if profile != "default" and "--profile" not in parts:
                new_command.extend(["--profile", profile])

            try:
                # Execute the command
                subprocess.run(new_command, check=False)
            except KeyboardInterrupt:
                typer.secho("\nLoop interrupted by user.", fg=typer.colors.YELLOW)
                raise typer.Exit(code=1)
            except Exception as e:
                typer.secho(f"Failed to execute command in {region}: {e}", fg=typer.colors.RED)

    except (AwsOperationError, BotoCoreError, ClientError) as e:
        print_aws_error(e)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.secho(f"An error occurred: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@app.command()
def resolve_instance(
    target: str = typer.Argument(..., help="EC2 instance id, IP address, or Name tag value to resolve"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the local resolver cache"),
    connect_timeout: int = typer.Option(
        DEFAULT_CONNECT_TIMEOUT_SECONDS,
        "--connect-timeout",
        min=1,
        help="EC2 API connection timeout in seconds for each region lookup",
    ),
    read_timeout: int = typer.Option(
        DEFAULT_READ_TIMEOUT_SECONDS,
        "--read-timeout",
        min=1,
        help="EC2 API read timeout in seconds for each region lookup",
    ),
    max_attempts: int = typer.Option(
        DEFAULT_MAX_ATTEMPTS,
        "--max-attempts",
        min=1,
        help="Total EC2 API attempts per region lookup, including retries",
    ),
):
    """
    Resolve an EC2 instance id, IP address, or Name tag value to region and instance metadata.
    """
    try:
        cache_hit = False
        typer.echo(f"Resolving [{target}] using profile [{DEFAULT_PROFILE}] across enabled regions...")
        matches = None if no_cache else get_cached_resolve_result(target)
        if matches is not None:
            cache_hit = True
        else:
            matches = resolve_instance_matches(
                target,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                max_attempts=max_attempts,
            )
            if len(matches) == 1:
                cache_resolve_result(target, matches)

        if not matches:
            typer.secho(f"No instance found for [{target}].", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        if len(matches) > 1:
            typer.secho(
                f"Multiple instances matched [{target}]. Please resolve the ambiguity first.",
                fg=typer.colors.RED,
                err=True,
            )
            print_instance_matches(matches)
            raise typer.Exit(code=1)

        if cache_hit:
            typer.secho("Cache hit: returning cached resolver result.", fg=typer.colors.CYAN)
        print_instance_matches(matches)
    except (AwsOperationError, BotoCoreError, ClientError) as e:
        print_aws_error(e)
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        typer.secho(f"An error occurred: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@app.command()
def ssm(
    target: Optional[str] = typer.Argument(
        None,
        help="EC2 instance id, IP address, or Name tag value to start an SSM session against. Leave empty to browse online SSM targets.",
    ),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the local resolver cache"),
    connect_timeout: int = typer.Option(
        DEFAULT_CONNECT_TIMEOUT_SECONDS,
        "--connect-timeout",
        min=1,
        help="EC2 API connection timeout in seconds for each region lookup",
    ),
    read_timeout: int = typer.Option(
        DEFAULT_READ_TIMEOUT_SECONDS,
        "--read-timeout",
        min=1,
        help="EC2 API read timeout in seconds for each region lookup",
    ),
    max_attempts: int = typer.Option(
        DEFAULT_MAX_ATTEMPTS,
        "--max-attempts",
        min=1,
        help="Total EC2 API attempts per region lookup, including retries",
    ),
):
    """
    Resolve the target and start an AWS SSM session.
    """
    try:
        cache_hit = False
        preview_command_printed = False
        match: Optional[Dict[str, Any]] = None
        aws_cli_path = shutil.which("aws")
        if aws_cli_path is None:
            typer.secho("AWS CLI not found in PATH.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        if target is None:
            match = SsmSelectionApp(
                title_text="AWS SSM Targets",
                status_text=f"Loading SSM targets with profile [{DEFAULT_PROFILE}]...",
                live_load=True,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                max_attempts=max_attempts,
            ).run()
            if match is None:
                raise typer.Exit(code=1)
        else:
            typer.echo(f"Resolving [{target}] using profile [{DEFAULT_PROFILE}] across enabled regions...")
            matches = None if no_cache else get_cached_resolve_result(target)
            if matches is not None:
                cache_hit = True
            else:
                def print_first_match_command(match: Dict[str, Any]):
                    nonlocal preview_command_printed
                    if preview_command_printed:
                        return
                    preview_command_printed = True
                    console.print(Panel.fit(
                        "First match found. You can use this command right away:",
                        border_style="cyan",
                        title="SSM Preview",
                    ))
                    console.print(f"[bold]{' '.join(shlex.quote(part) for part in build_ssm_command(match))}[/bold]")

                matches = resolve_instance_matches(
                    target,
                    on_first_match=print_first_match_command,
                    connect_timeout=connect_timeout,
                    read_timeout=read_timeout,
                    max_attempts=max_attempts,
                )
                if len(matches) == 1:
                    cache_resolve_result(target, matches)

            if not matches:
                typer.secho(f"No instance found for [{target}].", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)

            if len(matches) > 1:
                match = SsmSelectionApp(
                    initial_matches=sorted(
                        matches,
                        key=lambda item: (
                            item["region"],
                            (item.get("name") or "").lower(),
                            item["instance_id"],
                        ),
                    ),
                    title_text=f"SSM Matches for {target}",
                    status_text=f"{len(matches)} instance(s) matched [{target}]. Use arrow keys to choose one.",
                    live_load=False,
                ).run()
                if match is None:
                    raise typer.Exit(code=1)
            else:
                match = matches[0]

        if match is None:
            typer.secho("No instance was selected.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        command = build_ssm_command(match)

        if cache_hit:
            typer.secho("Cache hit: using cached resolver result.", fg=typer.colors.CYAN)
        console.print(Panel.fit("Resolved target", border_style="green", title="SSM"))
        print_instance_matches([match])
        console.print(Panel.fit("Starting SSM session", border_style="green", title="SSM"))
        console.print(f"[bold]{' '.join(shlex.quote(part) for part in command)}[/bold]")
        # Replace the current process instead of spawning a long-lived parent
        # so the interactive SSM session behaves like a direct `aws ssm` call.
        os.execv(aws_cli_path, [aws_cli_path, *command[1:]])
    except (AwsOperationError, BotoCoreError, ClientError) as e:
        print_aws_error(e)
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        typer.secho(f"An error occurred: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

@app.command()
def version():
    """
    Show version info.
    """
    typer.echo("aws-cli-tools version 0.1.0")

if __name__ == "__main__":
    app()
