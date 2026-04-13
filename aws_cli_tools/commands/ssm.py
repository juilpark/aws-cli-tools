import os
import shlex
import shutil
from typing import Optional

import typer
from botocore.exceptions import BotoCoreError, ClientError
from rich.panel import Panel

from ..cache import cache_resolve_result, get_cached_resolve_result
from ..constants import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_MAX_ATTEMPTS, DEFAULT_PROFILE, DEFAULT_READ_TIMEOUT_SECONDS
from ..errors import AwsOperationError
from ..instances import resolve_instance_matches
from ..models import InstanceMatch
from ..output import console, print_aws_error, print_instance_matches
from ..ssm_targets import build_ssm_command
from ..ui import SsmSelectionApp


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
) -> None:
    """
    Resolve the target and start an AWS SSM session.
    """
    try:
        cache_hit = False
        preview_command_printed = False
        match: Optional[InstanceMatch] = None
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

                def print_first_match_command(found_match: InstanceMatch) -> None:
                    nonlocal preview_command_printed
                    if preview_command_printed:
                        return

                    preview_command_printed = True
                    console.print(
                        Panel.fit(
                            "First match found. You can use this command right away:",
                            border_style="cyan",
                            title="SSM Preview",
                        )
                    )
                    console.print(f"[bold]{' '.join(shlex.quote(part) for part in build_ssm_command(found_match))}[/bold]")

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
        os.execv(aws_cli_path, [aws_cli_path, *command[1:]])
    except (AwsOperationError, BotoCoreError, ClientError) as error:
        print_aws_error(error)
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as error:
        typer.secho(f"An error occurred: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

