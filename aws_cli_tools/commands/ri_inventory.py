from pathlib import Path

import typer
from botocore.exceptions import BotoCoreError, ClientError

from ..constants import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_READ_TIMEOUT_SECONDS,
    RI_INVENTORY_OUTPUT_DIR,
)
from ..errors import AwsOperationError
from ..output import print_aws_error
from ..ri_inventory import collect_reserved_instance_inventory, write_ri_inventory_csv


def ri_inventory(
    output_dir: Path = typer.Option(
        RI_INVENTORY_OUTPUT_DIR,
        "--output-dir",
        file_okay=False,
        dir_okay=True,
        help="Directory where the timestamped Reserved Instances CSV will be saved",
    ),
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
    List EC2 Reserved Instances in every enabled region and save a CSV.
    """
    try:
        records = collect_reserved_instance_inventory(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_attempts=max_attempts,
        )
        output_path = write_ri_inventory_csv(records, output_dir)
        typer.echo(f"Collected {len(records)} Reserved Instance commitments.")
        typer.echo(f"CSV saved to: {output_path}")
    except (AwsOperationError, BotoCoreError, ClientError) as error:
        print_aws_error(error)
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as error:
        typer.secho(f"An error occurred: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
