from datetime import datetime, timezone
from pathlib import Path

import typer
from botocore.exceptions import BotoCoreError, ClientError

from ..commitment_inventory import (
    build_commitment_eligibility,
    collect_commitment_resource_inventory,
    write_commitment_eligibility_csv,
    write_commitment_resource_inventory_csv,
)
from ..constants import (
    COMMITMENT_INVENTORY_OUTPUT_DIR,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_READ_TIMEOUT_SECONDS,
)
from ..errors import AwsOperationError
from ..output import print_aws_error


def commitment_inventory(
    output_dir: Path = typer.Option(
        COMMITMENT_INVENTORY_OUTPUT_DIR,
        "--output-dir",
        file_okay=False,
        dir_okay=True,
        help="Directory where commitment eligibility and resource CSVs will be saved",
    ),
    connect_timeout: int = typer.Option(
        DEFAULT_CONNECT_TIMEOUT_SECONDS,
        "--connect-timeout",
        min=1,
        help="AWS API connection timeout in seconds for each regional lookup",
    ),
    read_timeout: int = typer.Option(
        DEFAULT_READ_TIMEOUT_SECONDS,
        "--read-timeout",
        min=1,
        help="AWS API read timeout in seconds for each regional lookup",
    ),
    max_attempts: int = typer.Option(
        DEFAULT_MAX_ATTEMPTS,
        "--max-attempts",
        min=1,
        help="Total AWS API attempts per regional lookup, including retries",
    ),
) -> None:
    """
    Save RI/Savings Plans eligibility and resource inventories as CSV files.
    """
    try:
        captured_at = datetime.now(timezone.utc)
        eligibility = build_commitment_eligibility()
        eligibility_path = write_commitment_eligibility_csv(eligibility, output_dir, captured_at=captured_at)
        resources = collect_commitment_resource_inventory(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_attempts=max_attempts,
        )
        resources_path = write_commitment_resource_inventory_csv(resources, output_dir, captured_at=captured_at)
        error_count = sum(1 for record in resources if record["collection_status"] == "error")
        resource_count = len(resources) - error_count
        typer.echo(f"Collected {resource_count} commitment-relevant resource rows.")
        if error_count:
            typer.echo(f"Recorded {error_count} regional collection errors in the resource CSV.")
        typer.echo(f"Eligibility CSV saved to: {eligibility_path}")
        typer.echo(f"Resource CSV saved to: {resources_path}")
    except (AwsOperationError, BotoCoreError, ClientError) as error:
        print_aws_error(error)
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as error:
        typer.secho(f"An error occurred: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
