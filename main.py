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
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

app = typer.Typer(help="AWS CLI Tools")

AWS_DOT_AWS_DIR = Path.home() / ".aws"
AWS_CREDENTIALS_FILE = AWS_DOT_AWS_DIR / "credentials"
AWS_CONFIG_FILE = AWS_DOT_AWS_DIR / "config"
DEFAULT_PROFILE = "default"
DEFAULT_STS_REGION = "ap-northeast-2"
CACHE_DIR = Path.home() / ".cache" / "aws-cli-tools"
RESOLVE_CACHE_FILE = CACHE_DIR / "resolve-instance.json"
INSTANCE_ID_CACHE_TTL_SECONDS = 300
IP_CACHE_TTL_SECONDS = 60


class AwsOperationError(Exception):
    """Wrap AWS SDK errors with operation context."""

    def __init__(self, operation: str, error: Exception, region: Optional[str] = None, profile: Optional[str] = None):
        self.operation = operation
        self.error = error
        self.region = region
        self.profile = profile
        super().__init__(str(error))


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


def get_cache_ttl_seconds(target: str) -> int:
    """Return cache TTL based on target type."""
    return INSTANCE_ID_CACHE_TTL_SECONDS if is_instance_id(target) else IP_CACHE_TTL_SECONDS


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
    ec2 = session.client("ec2", region_name="us-east-1")
    try:
        response = ec2.describe_regions()
        return sorted(region["RegionName"] for region in response["Regions"])
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
    ec2 = session.client("ec2", region_name="us-east-1")
    try:
        response = ec2.describe_regions(AllRegions=True)
        return sorted(
            region["RegionName"]
            for region in response["Regions"]
            if region.get("OptInStatus") in {"opt-in-not-required", "opted-in"}
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


def resolve_instance_matches_in_region(region: str, target: str, target_kind: str) -> List[Dict[str, Any]]:
    """Resolve an instance target within a single region."""
    session = get_default_session()
    ec2 = session.client("ec2", region_name=region)
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

    return matches


def resolve_instance_matches(target: str) -> List[Dict[str, Any]]:
    """Resolve an EC2 instance by instance id or IP across enabled regions."""
    regions = get_enabled_regions()
    matches: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    if is_instance_id(target):
        target_kind = "instance_id"
    elif is_ipv4_address(target):
        target_kind = "ip"
    else:
        target_kind = "name"

    max_workers = min(12, len(regions)) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_region = {
            executor.submit(resolve_instance_matches_in_region, region, target, target_kind): region
            for region in regions
        }

        for future in as_completed(future_to_region):
            region_matches = future.result()
            for match in region_matches:
                key = (match["region"], match["instance_id"])
                if key not in seen:
                    seen.add(key)
                    matches.append(match)

    return matches


def print_instance_matches(matches: List[Dict[str, Any]]):
    """Print resolved instance matches in a readable format."""
    for match in matches:
        name_suffix = f", name={match['name']}" if match.get("name") else ""
        private_ip = match.get("private_ip") or "-"
        public_ip = match.get("public_ip") or "-"
        state = match.get("state") or "unknown"
        typer.echo(
            f"- region={match['region']}, instance_id={match['instance_id']}, "
            f"private_ip={private_ip}, public_ip={public_ip}, state={state}{name_suffix}"
        )


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
        regions = sorted([r["RegionName"] for r in regions_resp["Regions"]])
        
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
            matches = resolve_instance_matches(target)
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
    target: str = typer.Argument(..., help="EC2 instance id, IP address, or Name tag value to start an SSM session against"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the local resolver cache"),
):
    """
    Resolve the target and start an AWS SSM session.
    """
    try:
        cache_hit = False
        if shutil.which("aws") is None:
            typer.secho("AWS CLI not found in PATH.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        typer.echo(f"Resolving [{target}] using profile [{DEFAULT_PROFILE}] across enabled regions...")
        matches = None if no_cache else get_cached_resolve_result(target)
        if matches is not None:
            cache_hit = True
        else:
            matches = resolve_instance_matches(target)
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

        match = matches[0]
        command = [
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

        if cache_hit:
            typer.secho("Cache hit: using cached resolver result.", fg=typer.colors.CYAN)
        typer.echo("Resolved target:")
        print_instance_matches([match])
        typer.echo("Starting SSM session:")
        typer.echo(" ".join(shlex.quote(part) for part in command))

        subprocess.run(command, check=False)
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
