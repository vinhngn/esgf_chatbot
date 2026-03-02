"""
Schema Loader -- loads and caches pre-generated Neo4j schema JSON files.

Schema files are produced by schema_introspector.py and stored in
esgf_chatbot/templates/schemas/{db_name}_schema.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMAS_DIR = Path(__file__).parent / "schemas"
_cache: dict[str, dict[str, Any]] = {}


def load_schema(db_name: str) -> dict[str, Any]:
    """
    Load the schema JSON for *db_name* and cache it in memory.

    Returns an empty dict if the file does not exist (graceful fallback).
    """
    if db_name in _cache:
        return _cache[db_name]

    schema_file = _SCHEMAS_DIR / f"{db_name}_schema.json"
    if not schema_file.exists():
        logger.warning(
            "[SchemaLoader] Schema file not found: %s -- using empty schema",
            schema_file,
        )
        _cache[db_name] = {}
        return _cache[db_name]

    try:
        with open(schema_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _cache[db_name] = data
        logger.info(
            "[SchemaLoader] Loaded schema for '%s' (%d bytes)",
            db_name,
            schema_file.stat().st_size,
        )
    except Exception as exc:
        logger.error("[SchemaLoader] Failed to load %s: %s", schema_file, exc)
        _cache[db_name] = {}

    return _cache[db_name]


def get_available_databases() -> list[str]:
    """Return database names that have a schema JSON file."""
    if not _SCHEMAS_DIR.exists():
        return []
    return sorted(
        p.stem.replace("_schema", "")
        for p in _SCHEMAS_DIR.glob("*_schema.json")
        if p.stem != "all_schemas"
    )


def clear_cache() -> None:
    """Clear the in-memory schema cache (useful for testing)."""
    _cache.clear()
