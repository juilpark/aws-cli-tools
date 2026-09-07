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
from .models import ReservedInstanceInventory


RI_CSV_FIELDS = [
    "captured_at_utc",
    "region",
    "reserved_instances_id",
    "instance_type",
    "instance_count",
    "state",
    "scope",
    "availability_zone",
    "availability_zone_id",
    "instance_tenancy",
    "product_description",
    "offering_class",
    "offering_type",
    "duration_seconds",
    "start",
    "end",
    "currency_code",
    "fixed_price",
    "usage_price",
    "recurring_charge_amount",
    "recurring_charge_frequency",
    "recurring_charges_json",
    "tags_json",
]


def _format_datetime(value: object) -> Optional[str]:
    if not isinstance(value, datetime):
        return str(value) if value is not None else None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_tags(reserved_instance: Dict[str, object]) -> Dict[str, str]:
    tags = reserved_instance.get("Tags", [])
    if not isinstance(tags, list):
        return {}

    return {
        tag["Key"]: tag["Value"]
        for tag in tags
        if isinstance(tag, dict) and isinstance(tag.get("Key"), str) and isinstance(tag.get("Value"), str)
    }


def _extract_recurring_charges(reserved_instance: Dict[str, object]) -> tuple[Optional[float], Optional[str], str]:
    charges = reserved_instance.get("RecurringCharges", [])
    if not isinstance(charges, list):
        charges = []

    valid_charges = [charge for charge in charges if isinstance(charge, dict)]
    amounts = [charge["Amount"] for charge in valid_charges if isinstance(charge.get("Amount"), (int, float))]
    frequencies = sorted(
        {charge["Frequency"] for charge in valid_charges if isinstance(charge.get("Frequency"), str)}
    )
    recurring_amount = float(sum(amounts)) if amounts else None
    recurring_frequency = ";".join(frequencies) if frequencies else None
    recurring_json = json.dumps(valid_charges, ensure_ascii=False, sort_keys=True)
    return recurring_amount, recurring_frequency, recurring_json


def extract_reserved_instance_inventory(
    reserved_instances: Sequence[Dict[str, object]],
    region: str,
) -> List[ReservedInstanceInventory]:
    """Normalize DescribeReservedInstances results into CSV rows."""
    records: List[ReservedInstanceInventory] = []
    for reserved_instance in reserved_instances:
        if not isinstance(reserved_instance, dict):
            continue

        tags = _extract_tags(reserved_instance)
        recurring_amount, recurring_frequency, recurring_json = _extract_recurring_charges(reserved_instance)
        records.append(
            {
                "captured_at_utc": None,
                "region": region,
                "reserved_instances_id": reserved_instance.get("ReservedInstancesId"),
                "instance_type": reserved_instance.get("InstanceType"),
                "instance_count": reserved_instance.get("InstanceCount"),
                "state": reserved_instance.get("State"),
                "scope": reserved_instance.get("Scope"),
                "availability_zone": reserved_instance.get("AvailabilityZone"),
                "availability_zone_id": reserved_instance.get("AvailabilityZoneId"),
                "instance_tenancy": reserved_instance.get("InstanceTenancy"),
                "product_description": reserved_instance.get("ProductDescription"),
                "offering_class": reserved_instance.get("OfferingClass"),
                "offering_type": reserved_instance.get("OfferingType"),
                "duration_seconds": reserved_instance.get("Duration"),
                "start": _format_datetime(reserved_instance.get("Start")),
                "end": _format_datetime(reserved_instance.get("End")),
                "currency_code": reserved_instance.get("CurrencyCode"),
                "fixed_price": reserved_instance.get("FixedPrice"),
                "usage_price": reserved_instance.get("UsagePrice"),
                "recurring_charge_amount": recurring_amount,
                "recurring_charge_frequency": recurring_frequency,
                "recurring_charges_json": recurring_json,
                "tags_json": json.dumps(tags, ensure_ascii=False, sort_keys=True),
            }
        )
    return records


def list_reserved_instances_in_region(
    region: str,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[ReservedInstanceInventory]:
    """List all EC2 Reserved Instances in one enabled region."""
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
        response = ec2.describe_reserved_instances()
        return extract_reserved_instance_inventory(response.get("ReservedInstances", []), region)
    except (BotoCoreError, ClientError) as error:
        raise AwsOperationError(
            operation="ec2.describe_reserved_instances",
            error=error,
            region=region,
            profile=DEFAULT_PROFILE,
        ) from error


def collect_reserved_instance_inventory(
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[ReservedInstanceInventory]:
    """Collect EC2 Reserved Instances across every region enabled for the account."""
    regions = get_enabled_regions()
    max_workers = min(12, len(regions)) or 1
    records: List[ReservedInstanceInventory] = []
    errors: List[AwsOperationError] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_region = {
            executor.submit(
                list_reserved_instances_in_region,
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

    return sorted(
        records,
        key=lambda record: (record["region"], record["reserved_instances_id"] or ""),
    )


def write_ri_inventory_csv(
    records: Sequence[ReservedInstanceInventory],
    output_dir: Path,
    captured_at: Optional[datetime] = None,
) -> Path:
    """Write Reserved Instance rows to a timestamped CSV."""
    return write_timestamped_csv(records, output_dir, RI_CSV_FIELDS, "ri-commitments", captured_at)
