"""Parse the selected query-plan contract from profile context."""

from __future__ import annotations

import json
import re


def _primary_target_label_from_context(learned_context: str) -> str:
    match = re.search(r"\bprimary_target_label=([A-Za-z_][A-Za-z0-9_]*)", learned_context or "")
    if match:
        return match.group(1)
    json_match = re.search(
        r'"target_label"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"', learned_context or ""
    )
    return json_match.group(1) if json_match else ""


def _expected_operation_from_context(learned_context: str) -> str:
    match = re.search(
        r'"expected_operation"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"', learned_context or ""
    )
    return match.group(1).lower() if match else ""


def _query_plan_from_context(learned_context: str) -> dict:
    text = learned_context or ""
    marker = "QUERY PLAN CONTRACT JSON:"
    marker_index = text.find(marker)
    if marker_index < 0:
        return {}
    after_marker = text[marker_index + len(marker) :].lstrip()
    json_line = after_marker.splitlines()[0].strip() if after_marker else ""
    if not json_line:
        return {}
    try:
        return json.loads(json_line)
    except Exception:
        return {}
