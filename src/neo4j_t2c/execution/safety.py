"""Execution safeguards and result-quality checks for generated Cypher."""

from __future__ import annotations

import os
import re
from typing import Any

DEFAULT_EXECUTION_ROW_CAP = 100


def execution_row_cap() -> int:
    """Return the API-side row cap configured for live query execution."""
    try:
        return int(
            os.getenv(
                "T2C_API_EXECUTION_ROW_CAP",
                str(DEFAULT_EXECUTION_ROW_CAP),
            )
        )
    except ValueError:
        return DEFAULT_EXECUTION_ROW_CAP


def _limit_value(cypher: str) -> int | None:
    match = re.search(r"(?is)\bLIMIT\s+(\d+)\s*$", cypher or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def cap_execution_cypher(
    cypher: str,
    *,
    row_cap: int | None = None,
) -> tuple[str, bool]:
    """Cap live execution rows without changing the generated query contract."""
    cap = execution_row_cap() if row_cap is None else row_cap
    if cap <= 0 or not cypher:
        return cypher, False

    existing_limit = _limit_value(cypher)
    if existing_limit is not None and existing_limit <= cap:
        return cypher, False
    if existing_limit is not None:
        capped = re.sub(
            r"(?is)\bLIMIT\s+\d+\s*$",
            f"LIMIT {cap}",
            cypher.strip(),
        )
        return capped, capped != cypher
    return f"{cypher.rstrip()} LIMIT {cap}", True


def result_summary(result: Any) -> dict:
    """Return a bounded representation suitable for logs and traces."""
    if isinstance(result, list):
        return {
            "type": "list",
            "row_count": len(result),
            "sample_rows": result[:2],
        }
    return {"type": type(result).__name__, "preview": str(result)[:1000]}


def _projected_rows_are_all_null(result: Any) -> bool:
    if not isinstance(result, list) or not result:
        return False

    saw_projected_value = False
    for row in result[:10]:
        if not isinstance(row, dict) or not row:
            continue
        saw_projected_value = True
        if any(value is not None for value in row.values()):
            return False
    return saw_projected_value


def result_quality_feedback(
    result: Any,
    *,
    retry_empty: bool = False,
) -> str | None:
    """Explain retryable result-shape problems without rejecting empty answers."""
    if retry_empty and isinstance(result, list) and not result:
        return (
            "Neo4j execution returned zero rows. Re-check relationship direction, "
            "property ownership, variable scope, and literal placement against the "
            "runtime schema and retrieved evidence. Preserve the requested meaning: "
            "do not remove or weaken filters merely to manufacture rows. If the "
            "query is already schema-consistent, return it unchanged."
        )
    if _projected_rows_are_all_null(result):
        return (
            "Neo4j execution returned rows, but every projected value in the "
            "sample is null. Repair the query by avoiding null projections: "
            "use properties that actually have values, add IS NOT NULL filters "
            "for returned or ordered properties, and keep the user's requested "
            "output shape."
        )
    return None
