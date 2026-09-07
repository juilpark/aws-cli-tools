import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from botocore.exceptions import BotoCoreError, ClientError

from .aws_common import build_boto_config, get_default_session, get_enabled_regions
from .constants import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_MAX_ATTEMPTS, DEFAULT_PROFILE, DEFAULT_READ_TIMEOUT_SECONDS
from .csv_export import write_timestamped_csv
from .errors import AwsOperationError
from .models import Ec2InstanceInventory


INVENTORY_CSV_FIELDS = [
    "captured_at_utc",
    "region",
    "availability_zone",
    "instance_id",
    "name",
    "image_id",
    "instance_type",
    "state",
    "platform",
    "platform_details",
    "architecture",
    "tenancy",
    "instance_lifecycle",
    "spot_instance",
    "launch_time",
    "usage_operation",
    "usage_operation_update_time",
    "vcpu_core_count",
    "threads_per_core",
    "hypervisor",
    "virtualization_type",
    "ebs_optimized",
    "root_device_type",
    "vpc_id",
    "subnet_id",
    "private_ip",
    "public_ip",
    "placement_group_name",
    "reservation_id",
    "owner_id",
    "tags_json",
]


def _format_datetime(value: object) -> Optional[str]:
    if not isinstance(value, datetime):
        return str(value) if value is not None else None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_tags(instance: Dict[str, object]) -> Dict[str, str]:
    tags = instance.get("Tags", [])
    if not isinstance(tags, list):
        return {}

    return {
        tag["Key"]: tag["Value"]
        for tag in tags
        if isinstance(tag, dict) and isinstance(tag.get("Key"), str) and isinstance(tag.get("Value"), str)
    }


def extract_instance_inventory(
    reservations: Sequence[Dict[str, object]],
    region: str,
) -> List[Ec2InstanceInventory]:
    """Normalize DescribeInstances reservations into RI/SP analysis rows."""
    records: List[Ec2InstanceInventory] = []
    for reservation in reservations:
        if not isinstance(reservation, dict):
            continue

        instances = reservation.get("Instances", [])
        if not isinstance(instances, list):
            continue

        placement = reservation.get("Placement")
        reservation_placement = placement if isinstance(placement, dict) else {}
        reservation_id = reservation.get("ReservationId")
        owner_id = reservation.get("OwnerId")

        for instance in instances:
            if not isinstance(instance, dict) or "InstanceId" not in instance:
                continue

            tags = _extract_tags(instance)
            instance_placement = instance.get("Placement")
            placement_data = instance_placement if isinstance(instance_placement, dict) else reservation_placement
            state = instance.get("State")
            state_data = state if isinstance(state, dict) else {}
            cpu_options = instance.get("CpuOptions")
            cpu_data = cpu_options if isinstance(cpu_options, dict) else {}
            lifecycle = instance.get("InstanceLifecycle")
            lifecycle_value = lifecycle if isinstance(lifecycle, str) else None

            records.append(
                {
                    "captured_at_utc": None,
                    "region": region,
                    "availability_zone": placement_data.get("AvailabilityZone"),
                    "instance_id": str(instance["InstanceId"]),
                    "name": tags.get("Name"),
                    "image_id": instance.get("ImageId"),
                    "instance_type": instance.get("InstanceType"),
                    "state": state_data.get("Name"),
                    "platform": instance.get("Platform"),
                    "platform_details": instance.get("PlatformDetails"),
                    "architecture": instance.get("Architecture"),
                    "tenancy": placement_data.get("Tenancy"),
                    "instance_lifecycle": lifecycle_value,
                    "spot_instance": lifecycle_value == "spot",
                    "launch_time": _format_datetime(instance.get("LaunchTime")),
                    "usage_operation": instance.get("UsageOperation"),
                    "usage_operation_update_time": _format_datetime(instance.get("UsageOperationUpdateTime")),
                    "vcpu_core_count": cpu_data.get("CoreCount"),
                    "threads_per_core": cpu_data.get("ThreadsPerCore"),
                    "hypervisor": instance.get("Hypervisor"),
                    "virtualization_type": instance.get("VirtualizationType"),
                    "ebs_optimized": instance.get("EbsOptimized"),
                    "root_device_type": instance.get("RootDeviceType"),
                    "vpc_id": instance.get("VpcId"),
                    "subnet_id": instance.get("SubnetId"),
                    "private_ip": instance.get("PrivateIpAddress"),
                    "public_ip": instance.get("PublicIpAddress"),
                    "placement_group_name": placement_data.get("GroupName"),
                    "reservation_id": reservation_id,
                    "owner_id": owner_id,
                    "tags_json": json.dumps(tags, ensure_ascii=False, sort_keys=True),
                }
            )
    return records


def list_instances_in_region(
    region: str,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[Ec2InstanceInventory]:
    """List all EC2 instances in one enabled region."""
    try:
        session = get_default_session()
        ec2 = session.client(
            "ec2",
            region_name=region,
            config=build_boto_config(
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                max_attempts=max_attempts,
            ),
        )
        paginator = ec2.get_paginator("describe_instances")
        records: List[Ec2InstanceInventory] = []
        for page in paginator.paginate():
            records.extend(extract_instance_inventory(page.get("Reservations", []), region))
        return records
    except (BotoCoreError, ClientError) as error:
        raise AwsOperationError(
            operation="ec2.describe_instances",
            error=error,
            region=region,
            profile=DEFAULT_PROFILE,
        ) from error


def collect_ec2_instance_inventory(
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[Ec2InstanceInventory]:
    """Collect EC2 inventory across every region enabled for the account."""
    regions = get_enabled_regions()
    max_workers = min(12, len(regions)) or 1
    records: List[Ec2InstanceInventory] = []
    errors: List[AwsOperationError] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_region = {
            executor.submit(
                list_instances_in_region,
                region,
                connect_timeout,
                read_timeout,
                max_attempts,
            ): region
            for region in regions
        }

        for future in as_completed(future_to_region):
            try:
                records.extend(future.result())
            except AwsOperationError as error:
                errors.append(error)

    if errors:
        errors.sort(key=lambda error: error.region or "")
        raise errors[0]

    return sorted(records, key=lambda record: (record["region"], record["instance_id"]))


def write_inventory_csv(
    records: Sequence[Ec2InstanceInventory],
    output_dir: Path,
    captured_at: Optional[datetime] = None,
) -> Path:
    """Write EC2 inventory rows to a timestamped CSV."""
    return write_timestamped_csv(records, output_dir, INVENTORY_CSV_FIELDS, "ec2-instances", captured_at)
