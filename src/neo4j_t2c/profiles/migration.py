"""In-memory migration from legacy profile mappings to ProfileV2."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from neo4j_t2c.profiles.fingerprint import schema_fingerprint
from neo4j_t2c.profiles.models import (
    PROFILE_VERSION,
    ProfileDocument,
)


def _source_type(source: str, explicit: str) -> str:
    if explicit:
        return explicit
    if Path(source).suffix.lower() == ".csv":
        return "benchmark_csv"
    if source.startswith(("neo4j://", "neo4j+s://", "bolt://")):
        return "neo4j_live"
    return "unknown"


def migrate_profile_payload(
    payload: Mapping[str, Any],
    *,
    database_hint: str = "",
) -> dict[str, Any]:
    """Validate and enrich any supported legacy profile without mutating it."""
    migrated = dict(payload)
    version = str(migrated.get("profile_version") or PROFILE_VERSION)
    current_major = PROFILE_VERSION.split(".", maxsplit=1)[0]
    if version.split(".", maxsplit=1)[0] != current_major:
        raise ValueError(f"Unsupported profile version: {version}")

    database = str(
        migrated.get("database") or database_hint
    ).strip().lower()
    source = str(migrated.get("source") or "")
    source_type = _source_type(
        source,
        str(migrated.get("source_type") or ""),
    )
    provenance = dict(migrated.get("provenance") or {})
    provenance.setdefault("source", source)
    provenance.setdefault("source_type", source_type)
    provenance.setdefault("generator", "neo4j-t2c")
    provenance.setdefault("generator_version", "0.1.0")
    provenance.setdefault("generated_at", None)

    migrated.update(
        {
            "profile_version": version,
            "database": database,
            "source": source,
            "source_type": source_type,
            "provenance": provenance,
            "schema_fingerprint": (
                str(migrated.get("schema_fingerprint") or "")
                or schema_fingerprint(migrated.get("schema_profile"))
            ),
            "row_count": int(
                migrated.get("row_count")
                or len(migrated.get("examples") or [])
            ),
        }
    )
    return ProfileDocument.model_validate(migrated).to_payload()
