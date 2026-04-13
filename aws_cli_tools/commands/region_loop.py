import shlex
import subprocess

import boto3
import typer
from botocore.exceptions import BotoCoreError, ClientError

from ..aws_common import order_regions_by_priority
from ..errors import AwsOperationError
from ..output import print_aws_error, print_regions_list


def region_loop(
    profile: str = typer.Option("default", help="The AWS profile to use for fetching regions and running commands"),
) -> None:
    """
    Run an AWS CLI command across all available regions (Interactive Mode).
    """
    try:
        typer.secho("\n[Interactive Region Loop]", fg=typer.colors.CYAN, bold=True)
        typer.echo("Enter the AWS CLI command exactly as you would run it (e.g., aws ec2 describe-vpcs ...)")
        command = typer.prompt("Command", prompt_suffix="> ", type=str)

        session = boto3.Session(profile_name=profile)
        ec2 = session.client("ec2", region_name="us-east-1")

        typer.echo("Fetching available regions...")
        regions_response = ec2.describe_regions()
        regions = order_regions_by_priority([region["RegionName"] for region in regions_response["Regions"]])

        parts = shlex.split(command)
        if not parts or parts[0] != "aws":
            typer.secho("Error: Command must start with 'aws'", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        example_region = regions[0]
        preview_parts = [parts[0], "--region", example_region] + parts[1:]
        if profile != "default" and "--profile" not in parts:
            preview_parts.extend(["--profile", profile])
        preview_command = " ".join(shlex.quote(part) for part in preview_parts)

        typer.secho("\n[Region Loop Preview]", fg=typer.colors.CYAN, bold=True)
        typer.echo("Target Regions:")
        print_regions_list(regions)
        typer.echo(f"\nExample Command: {preview_command}")
        typer.echo(f"Total Regions: {len(regions)}")

        if not typer.confirm("\nDo you want to run this command across all regions?"):
            typer.echo("Operation cancelled.")
            raise typer.Abort()

        for region in regions:
            typer.secho(f"\n{'-' * 20} Region: {region} {'-' * 20}", fg=typer.colors.BLUE, bold=True)

            new_command = [parts[0], "--region", region] + parts[1:]
            if profile != "default" and "--profile" not in parts:
                new_command.extend(["--profile", profile])

            try:
                subprocess.run(new_command, check=False)
            except KeyboardInterrupt:
                typer.secho("\nLoop interrupted by user.", fg=typer.colors.YELLOW)
                raise typer.Exit(code=1)
            except Exception as error:
                typer.secho(f"Failed to execute command in {region}: {error}", fg=typer.colors.RED)
    except (AwsOperationError, BotoCoreError, ClientError) as error:
        print_aws_error(error)
        raise typer.Exit(code=1)
    except typer.Abort:
        raise
    except typer.Exit:
        raise
    except Exception as error:
        typer.secho(f"An error occurred: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
