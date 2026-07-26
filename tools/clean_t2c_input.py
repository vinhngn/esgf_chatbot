from __future__ import annotations

import argparse
import csv
import os
import tempfile
from pathlib import Path

REQUIRED_COLUMNS = ("question", "schema", "cypher", "database_name")


def clean_t2c_input(
    input_file: Path,
    *,
    output_file: Path | None = None,
) -> dict[str, int | str]:
    source = input_file.expanduser().resolve()
    target = (output_file or source).expanduser().resolve()
    csv.field_size_limit(2_147_483_647)

    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing required CSV columns: {sorted(missing)}")
        rows = [
            {
                column: (row.get(column) or "").strip()
                for column in REQUIRED_COLUMNS
            }
            for row in reader
            if any((row.get(column) or "").strip() for column in REQUIRED_COLUMNS)
        ]

    for row_number, row in enumerate(rows, start=2):
        empty = [column for column in REQUIRED_COLUMNS if not row[column]]
        if empty:
            raise ValueError(
                f"Row {row_number} has empty required fields: {empty}"
            )

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}-",
        suffix=".csv",
        dir=target.parent,
        text=True,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REQUIRED_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "source": str(source),
        "output": str(target),
        "rows": len(rows),
        "columns": len(REQUIRED_COLUMNS),
        "bytes": target.stat().st_size,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize a T2C input CSV and remove evaluator-output columns."
        )
    )
    parser.add_argument("input_file", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_args()
    result = clean_t2c_input(
        arguments.input_file,
        output_file=arguments.output,
    )
    for key, value in result.items():
        print(f"{key}: {value}")
