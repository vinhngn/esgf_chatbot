"""Composition of learned query evidence and live Neo4j evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from neo4j_t2c.profiles.migration import migrate_profile_payload

_LEARNED_FIELDS = (
    "question_summary",
    "cypher_summary",
    "intent_to_shape_signatures",
    "motif_to_return_contracts",
    "token_to_path_motifs",
)


def _source_entry(profile: Mapping[str, Any]) -> dict[str, str] | None:
    source = str(profile.get("source") or "")
    source_type = str(profile.get("source_type") or "")
    if not source and not source_type:
        return None
    return {"source": source, "source_type": source_type}


def merge_profile_with_live_schema(
    existing_profile: Mapping[str, Any],
    live_profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Preserve learned examples while refreshing database-derived evidence."""
    existing = migrate_profile_payload(existing_profile)
    live = migrate_profile_payload(live_profile)
    if existing["database"] != live["database"]:
        raise ValueError(
            "Cannot merge profiles for different databases: "
            f"{existing['database']} != {live['database']}"
        )
    existing_physical = str(existing.get("physical_database") or "").casefold()
    live_physical = str(live.get("physical_database") or "").casefold()
    if existing_physical and live_physical and existing_physical != live_physical:
        raise ValueError(
            "Cannot refresh a profile from a different physical database: "
            f"{existing_physical} != {live_physical}"
        )

    learned_examples = list(existing.get("examples") or [])
    has_learned_examples = (
        existing.get("source_type") in {"benchmark_csv", "hybrid"}
        and bool(learned_examples)
    )
    if not has_learned_examples:
        return live

    merged = dict(existing)
    merged.update(
        {
            "source_type": "hybrid",
            "physical_database": live.get("physical_database") or "",
            "build_options": live.get("build_options") or {},
            "schema_profile": live.get("schema_profile") or {},
            "value_profile": live.get("value_profile") or {},
            "query_recipe_profile": live.get("query_recipe_profile") or {},
            "examples": learned_examples,
            "row_count": len(learned_examples),
        }
    )
    for field in _LEARNED_FIELDS:
        merged[field] = existing.get(field) or live.get(field) or {}

    sources = [
        entry
        for entry in (_source_entry(existing), _source_entry(live))
        if entry is not None
    ]
    provenance = dict(existing.get("provenance") or {})
    provenance.update(
        {
            "source": str(existing.get("source") or ""),
            "source_type": "hybrid",
            "sources": sources,
        }
    )
    merged["provenance"] = provenance
    merged.pop("schema_fingerprint", None)
    return migrate_profile_payload(merged)
