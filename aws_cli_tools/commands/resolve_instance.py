import typer
from botocore.exceptions import BotoCoreError, ClientError

from ..cache import cache_resolve_result, get_cached_resolve_result
from ..constants import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_MAX_ATTEMPTS, DEFAULT_PROFILE, DEFAULT_READ_TIMEOUT_SECONDS
from ..errors import AwsOperationError
from ..instances import resolve_instance_matches
from ..output import print_aws_error, print_instance_matches


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
) -> None:
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
    except (AwsOperationError, BotoCoreError, ClientError) as error:
        print_aws_error(error)
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as error:
        typer.secho(f"An error occurred: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

