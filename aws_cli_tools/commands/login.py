import configparser
import os
from typing import Optional

import boto3
import typer
from botocore.exceptions import BotoCoreError, ClientError

from ..aws_common import find_config_section, get_profile_region
from ..constants import AWS_CONFIG_FILE, AWS_CREDENTIALS_FILE, AWS_DOT_AWS_DIR
from ..errors import AwsOperationError
from ..output import print_aws_error


def login(
    source_profile: str = typer.Option(
        os.getenv("AWS_SOURCE_PROFILE", "example_source_profile"),
        help="The source AWS profile to use for STS authentication. Can also be set via AWS_SOURCE_PROFILE in .env",
    ),
    target_profile: str = typer.Option("default", help="The profile to update in credentials file"),
    duration: int = typer.Option(28800, help="Duration in seconds (default 28800s / 8h)"),
    mfa_serial: Optional[str] = typer.Option(
        os.getenv("AWS_MFA_SERIAL"),
        help="MFA Serial Number (ARN). Can also be set via AWS_MFA_SERIAL in .env",
    ),
    token_code: Optional[str] = typer.Option(None, help="MFA Token Code if required"),
) -> None:
    """
    Get temporary session token from AWS STS and update ~/.aws/credentials & config.
    """
    try:
        cred_config = configparser.ConfigParser()
        if AWS_CREDENTIALS_FILE.exists():
            cred_config.read(AWS_CREDENTIALS_FILE)
            if target_profile in cred_config.sections() and "aws_session_token" not in cred_config[target_profile]:
                typer.secho(
                    f"WARNING: Profile [{target_profile}] exists but does not have a session token.",
                    fg=typer.colors.YELLOW,
                    bold=True,
                )
                typer.secho(
                    "It looks like a permanent IAM User credential. Overwriting it with temporary STS tokens...",
                    fg=typer.colors.YELLOW,
                )

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

        if not AWS_DOT_AWS_DIR.exists():
            AWS_DOT_AWS_DIR.mkdir(parents=True, exist_ok=True)

        if target_profile not in cred_config.sections():
            cred_config.add_section(target_profile)

        cred_config.set(target_profile, "aws_access_key_id", credentials["AccessKeyId"])
        cred_config.set(target_profile, "aws_secret_access_key", credentials["SecretAccessKey"])
        cred_config.set(target_profile, "aws_session_token", credentials["SessionToken"])

        with open(AWS_CREDENTIALS_FILE, "w") as credentials_file:
            cred_config.write(credentials_file)
        os.chmod(AWS_CREDENTIALS_FILE, 0o600)

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

            with open(AWS_CONFIG_FILE, "w") as config_file:
                aws_config.write(config_file)
            os.chmod(AWS_CONFIG_FILE, 0o600)
            typer.echo(f"Synced config for [{target_section}] from [{source_section}]")
        else:
            typer.secho(
                f"Note: Source profile [{source_profile}] not found in {AWS_CONFIG_FILE}. Skipping config sync.",
                fg=typer.colors.CYAN,
            )

        typer.secho(f"Successfully updated [{target_profile}] profile in {AWS_CREDENTIALS_FILE}", fg=typer.colors.GREEN)
        typer.echo(f"Expires at: {credentials['Expiration']}")
    except (AwsOperationError, BotoCoreError, ClientError) as error:
        print_aws_error(error)
        raise typer.Exit(code=1)
    except Exception as error:
        typer.secho(f"An error occurred: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

