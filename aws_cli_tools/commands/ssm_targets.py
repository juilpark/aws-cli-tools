import os

import typer
from botocore.exceptions import BotoCoreError, ClientError

from ..constants import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_MAX_ATTEMPTS, DEFAULT_READ_TIMEOUT_SECONDS
from ..errors import AwsOperationError, is_request_expired_error
from ..output import print_aws_error, print_instance_matches_csv
from ..ssm_target_loader import load_ssm_target_candidates
from .login import run_login


def ssm_targets(
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the local SSM browser cache"),
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
) -> None:
    """
    Print online SSM-managed EC2 targets as CSV.
    """
    try:
        has_reauthenticated = False
        while True:
            try:
                matches = load_ssm_target_candidates(
                    use_cached_results=not no_cache,
                    connect_timeout=connect_timeout,
                    read_timeout=read_timeout,
                    max_attempts=max_attempts,
                )
                print_instance_matches_csv(matches)
                return
            except (AwsOperationError, BotoCoreError, ClientError) as error:
                if not has_reauthenticated and is_request_expired_error(error):
                    has_reauthenticated = True
                    typer.secho(
                        "AWS request expired while loading SSM targets. Running login and retrying once...",
                        fg=typer.colors.YELLOW,
                    )
                    run_login(
                        source_profile=os.getenv("AWS_SOURCE_PROFILE", "example_source_profile"),
                        mfa_serial=os.getenv("AWS_MFA_SERIAL"),
                    )
                    continue

                raise
    except (AwsOperationError, BotoCoreError, ClientError) as error:
        print_aws_error(error)
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as error:
        typer.secho(f"An error occurred: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
