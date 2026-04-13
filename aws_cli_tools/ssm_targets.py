from typing import List

from botocore.exceptions import BotoCoreError, ClientError

from .aws_common import build_boto_config, get_default_session
from .constants import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_MAX_ATTEMPTS, DEFAULT_PROFILE, DEFAULT_READ_TIMEOUT_SECONDS
from .errors import AwsOperationError
from .instances import describe_instances_by_ids_in_region
from .models import InstanceMatch


def build_ssm_command(match: InstanceMatch) -> List[str]:
    """Build the AWS CLI command used to start an SSM session."""
    return [
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


def list_ssm_candidates_in_region(
    region: str,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[InstanceMatch]:
    """List online SSM-managed EC2 instances in a single region."""
    session = get_default_session()
    ssm_client = session.client(
        "ssm",
        region_name=region,
        config=build_boto_config(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_attempts=max_attempts,
        ),
    )

    instance_ids: List[str] = []
    try:
        paginator = ssm_client.get_paginator("describe_instance_information")
        for page in paginator.paginate(
            Filters=[
                {"Key": "PingStatus", "Values": ["Online"]},
                {"Key": "ResourceType", "Values": ["EC2Instance"]},
            ]
        ):
            for info in page.get("InstanceInformationList", []):
                instance_id = info.get("InstanceId")
                if instance_id and instance_id.startswith("i-"):
                    instance_ids.append(instance_id)
    except ClientError as error:
        raise AwsOperationError(
            operation="ssm.describe_instance_information",
            error=error,
            region=region,
            profile=DEFAULT_PROFILE,
        ) from error
    except BotoCoreError as error:
        raise AwsOperationError(
            operation="ssm.describe_instance_information",
            error=error,
            region=region,
            profile=DEFAULT_PROFILE,
        ) from error

    matches = describe_instances_by_ids_in_region(
        region,
        sorted(set(instance_ids)),
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        max_attempts=max_attempts,
    )
    return sorted(
        matches,
        key=lambda match: (
            (match.get("name") or "").lower(),
            match["region"],
            match["instance_id"],
        ),
    )

