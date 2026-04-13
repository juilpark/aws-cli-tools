from typing import List, Optional

import typer
from botocore.exceptions import ClientError
from rich import box
from rich.console import Console
from rich.table import Table

from .errors import AwsOperationError
from .models import InstanceMatch

console = Console()


def print_instance_matches(matches: List[InstanceMatch]) -> None:
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


def print_regions_list(regions: List[str], cols: int = 4) -> None:
    """Print regions in a formatted grid."""
    for index in range(0, len(regions), cols):
        row = regions[index:index + cols]
        typer.echo("  " + "  ".join(f"{region:<20}" for region in row))


def print_aws_error(error: Exception) -> None:
    """Print AWS errors with extra diagnostic context."""
    operation: Optional[str] = None
    region: Optional[str] = None
    profile: Optional[str] = None
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

