"""Value normalization at the Neo4j/application boundary."""

from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Any


def _is_json_serializable(value: Any) -> bool:
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError, OverflowError):
        return False


def normalize_value(value: Any) -> Any:
    """Convert Neo4j values into JSON-safe values without changing containers."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: normalize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(normalize_value(item) for item in value)
    if hasattr(value, "iso_format"):
        return value.iso_format()
    if not _is_json_serializable(value):
        return str(value)
    return value


def strip_quotes(value: str) -> str:
    return value.strip("'").strip('"')


__all__ = ["normalize_value", "strip_quotes"]
