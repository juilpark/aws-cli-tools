import csv
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Sequence


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return value


def _format_datetime_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _unique_output_path(output_dir: Path, file_prefix: str, captured_at: datetime) -> Path:
    timestamp = captured_at.strftime("%Y%m%dT%H%M%SZ")
    base_path = output_dir / f"{file_prefix}-{timestamp}.csv"
    if not base_path.exists():
        return base_path

    index = 2
    while True:
        candidate = output_dir / f"{file_prefix}-{timestamp}-{index}.csv"
        if not candidate.exists():
            return candidate
        index += 1


def write_timestamped_csv(
    records: Sequence[Mapping[str, object]],
    output_dir: Path,
    fieldnames: Sequence[str],
    file_prefix: str,
    captured_at: Optional[datetime] = None,
) -> Path:
    """Write timestamped CSV rows atomically and return the created path."""
    captured_at = captured_at or datetime.now(timezone.utc)
    captured_at = captured_at.astimezone(timezone.utc) if captured_at.tzinfo else captured_at.replace(tzinfo=timezone.utc)
    captured_at_utc = _format_datetime_utc(captured_at)

    output_dir = output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = _unique_output_path(output_dir, file_prefix, captured_at)
    temporary_path: Optional[Path] = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=output_dir,
            prefix=f".{output_path.stem}-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            writer = csv.DictWriter(temporary_file, fieldnames=list(fieldnames), extrasaction="ignore")
            writer.writeheader()
            for record in records:
                row = {field: _csv_value(record.get(field)) for field in fieldnames}
                if "captured_at_utc" in fieldnames:
                    row["captured_at_utc"] = captured_at_utc
                writer.writerow(row)

        os.replace(temporary_path, output_path)
    except Exception:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        raise

    return output_path
