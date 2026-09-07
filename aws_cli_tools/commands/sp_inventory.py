from pathlib import Path

import typer
from botocore.exceptions import BotoCoreError, ClientError

from ..constants import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_READ_TIMEOUT_SECONDS,
    SAVINGS_PLAN_INVENTORY_OUTPUT_DIR,
)
from ..errors import AwsOperationError
from ..output import print_aws_error
from ..sp_inventory import collect_savings_plan_inventory, write_savings_plan_inventory_csv


def sp_inventory(
    output_dir: Path = typer.Option(
        SAVINGS_PLAN_INVENTORY_OUTPUT_DIR,
        "--output-dir",
        file_okay=False,
        dir_okay=True,
        help="Directory where the timestamped Savings Plans CSV will be saved",
    ),
    connect_timeout: int = typer.Option(
        DEFAULT_CONNECT_TIMEOUT_SECONDS,
        "--connect-timeout",
        min=1,
        help="Savings Plans API connection timeout in seconds",
    ),
    read_timeout: int = typer.Option(
        DEFAULT_READ_TIMEOUT_SECONDS,
        "--read-timeout",
        min=1,
        help="Savings Plans API read timeout in seconds",
    ),
    max_attempts: int = typer.Option(
        DEFAULT_MAX_ATTEMPTS,
        "--max-attempts",
        min=1,
        help="Total Savings Plans API attempts, including retries",
    ),
) -> None:
    """
    List every Savings Plan contract and save a CSV.
    """
    try:
        records = collect_savings_plan_inventory(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_attempts=max_attempts,
        )
        output_path = write_savings_plan_inventory_csv(records, output_dir)
        typer.echo(f"Collected {len(records)} Savings Plan commitments.")
        typer.echo(f"CSV saved to: {output_path}")
    except (AwsOperationError, BotoCoreError, ClientError) as error:
        print_aws_error(error)
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as error:
        typer.secho(f"An error occurred: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
