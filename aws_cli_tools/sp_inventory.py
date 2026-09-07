import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from botocore.exceptions import BotoCoreError, ClientError

from .aws_common import build_boto_config, get_default_session
from .constants import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_PROFILE,
    DEFAULT_READ_TIMEOUT_SECONDS,
    SAVINGS_PLANS_API_REGION,
)
from .csv_export import write_timestamped_csv
from .errors import AwsOperationError
from .models import SavingsPlanInventory


SAVINGS_PLAN_CSV_FIELDS = [
    "captured_at_utc",
    "savings_plan_id",
    "savings_plan_arn",
    "state",
    "savings_plan_type",
    "region",
    "ec2_instance_family",
    "product_types",
    "payment_option",
    "currency",
    "commitment",
    "upfront_payment_amount",
    "recurring_payment_amount",
    "term_duration_seconds",
    "start",
    "end",
    "returnable_until",
    "offering_id",
    "description",
    "tags_json",
]


def _format_timestamp(value: object) -> Optional[str]:
    if not isinstance(value, datetime):
        return str(value) if value is not None else None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_product_types(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, list):
        return ";".join(item for item in value if isinstance(item, str))
    return str(value)


def _format_tags(value: object) -> str:
    if not isinstance(value, dict):
        return "{}"

    tags = {
        str(key): str(tag_value)
        for key, tag_value in value.items()
        if tag_value is not None
    }
    return json.dumps(tags, ensure_ascii=False, sort_keys=True)


def extract_savings_plan_inventory(
    savings_plans: Sequence[Dict[str, object]],
) -> List[SavingsPlanInventory]:
    """Normalize DescribeSavingsPlans results into CSV rows."""
    records: List[SavingsPlanInventory] = []
    for savings_plan in savings_plans:
        if not isinstance(savings_plan, dict):
            continue

        records.append(
            {
                "captured_at_utc": None,
                "savings_plan_id": savings_plan.get("savingsPlanId"),
                "savings_plan_arn": savings_plan.get("savingsPlanArn"),
                "state": savings_plan.get("state"),
                "savings_plan_type": savings_plan.get("savingsPlanType"),
                "region": savings_plan.get("region"),
                "ec2_instance_family": savings_plan.get("ec2InstanceFamily"),
                "product_types": _format_product_types(savings_plan.get("productTypes")),
                "payment_option": savings_plan.get("paymentOption"),
                "currency": savings_plan.get("currency"),
                "commitment": savings_plan.get("commitment"),
                "upfront_payment_amount": savings_plan.get("upfrontPaymentAmount"),
                "recurring_payment_amount": savings_plan.get("recurringPaymentAmount"),
                "term_duration_seconds": savings_plan.get("termDurationInSeconds"),
                "start": _format_timestamp(savings_plan.get("start")),
                "end": _format_timestamp(savings_plan.get("end")),
                "returnable_until": _format_timestamp(savings_plan.get("returnableUntil")),
                "offering_id": savings_plan.get("offeringId"),
                "description": savings_plan.get("description"),
                "tags_json": _format_tags(savings_plan.get("tags")),
            }
        )
    return records


def list_savings_plans(
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[SavingsPlanInventory]:
    """List every Savings Plan through the account-level API endpoint."""
    try:
        session = get_default_session()
        savingsplans = session.client(
            "savingsplans",
            region_name=SAVINGS_PLANS_API_REGION,
            config=build_boto_config(
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                max_attempts=max_attempts,
            ),
        )

        records: List[SavingsPlanInventory] = []
        next_token: Optional[str] = None
        while True:
            request: Dict[str, object] = {"maxResults": 1000}
            if next_token:
                request["nextToken"] = next_token

            response = savingsplans.describe_savings_plans(**request)
            savings_plans = response.get("savingsPlans", [])
            if isinstance(savings_plans, list):
                records.extend(extract_savings_plan_inventory(savings_plans))

            next_token = response.get("nextToken")
            if not isinstance(next_token, str) or not next_token:
                break

        return records
    except (BotoCoreError, ClientError) as error:
        raise AwsOperationError(
            operation="savingsplans.describe_savings_plans",
            error=error,
            region=SAVINGS_PLANS_API_REGION,
            profile=DEFAULT_PROFILE,
        ) from error


def collect_savings_plan_inventory(
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[SavingsPlanInventory]:
    """Collect and sort all Savings Plan contracts returned for the account."""
    records = list_savings_plans(
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        max_attempts=max_attempts,
    )
    return sorted(
        records,
        key=lambda record: (record["region"] or "", record["savings_plan_id"] or ""),
    )


def write_savings_plan_inventory_csv(
    records: Sequence[SavingsPlanInventory],
    output_dir: Path,
    captured_at: Optional[datetime] = None,
) -> Path:
    """Write Savings Plan rows to a timestamped CSV."""
    return write_timestamped_csv(records, output_dir, SAVINGS_PLAN_CSV_FIELDS, "sp-commitments", captured_at)
