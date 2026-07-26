"""Stable fingerprints for the structural part of a Neo4j profile."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def _property_signature(properties: object) -> list[dict[str, str]]:
    signatures: list[dict[str, str]] = []
    if not isinstance(properties, list):
        return signatures
    for item in properties:
        if isinstance(item, str):
            signatures.append({"name": item, "type": ""})
        elif isinstance(item, Mapping):
            name = str(
                item.get("property")
                or item.get("name")
                or item.get("propertyName")
                or ""
            )
            if name:
                signatures.append(
                    {
                        "name": name,
                        "type": str(
                            item.get("type")
                            or item.get("propertyTypes")
                            or ""
                        ),
                    }
                )
    return sorted(signatures, key=lambda item: (item["name"], item["type"]))


def _schema_signature(
    schema_profile: Mapping[str, Any],
) -> dict[str, Any]:
    labels = []
    for item in schema_profile.get("labels", []):
        if not isinstance(item, Mapping):
            continue
        label = str(item.get("label") or item.get("name") or "")
        if label:
            labels.append(
                {
                    "label": label,
                    "properties": _property_signature(
                        item.get("properties")
                    ),
                }
            )

    relationships = []
    for item in schema_profile.get("relationships", []):
        if not isinstance(item, Mapping):
            continue
        rel_type = str(item.get("type") or item.get("relationshipType") or "")
        if not rel_type:
            continue
        patterns = []
        for pattern in item.get("patterns", []):
            if isinstance(pattern, Mapping):
                patterns.append(
                    {
                        "from": str(pattern.get("from") or ""),
                        "to": str(pattern.get("to") or ""),
                    }
                )
        relationships.append(
            {
                "type": rel_type,
                "patterns": sorted(
                    patterns,
                    key=lambda pattern: (
                        pattern["from"],
                        pattern["to"],
                    ),
                ),
                "properties": _property_signature(
                    item.get("properties")
                ),
            }
        )

    vector_indexes = []
    for item in schema_profile.get("vector_indexes", []):
        if not isinstance(item, Mapping):
            continue
        vector_indexes.append(
            {
                "name": str(item.get("name") or ""),
                "entity_type": str(item.get("entity_type") or ""),
                "labels_or_types": sorted(
                    str(value)
                    for value in item.get("labels_or_types", [])
                ),
                "properties": sorted(
                    str(value) for value in item.get("properties", [])
                ),
                "dimensions": item.get("dimensions"),
                "similarity_function": str(
                    item.get("similarity_function") or ""
                ),
            }
        )

    return {
        "labels": sorted(labels, key=lambda item: item["label"]),
        "relationships": sorted(
            relationships,
            key=lambda item: (
                item["type"],
                json.dumps(item["patterns"], sort_keys=True),
            ),
        ),
        "vector_indexes": sorted(
            vector_indexes,
            key=lambda item: item["name"],
        ),
    }


def schema_fingerprint(
    schema_profile: Mapping[str, Any] | None,
) -> str:
    """Return a count/value-independent fingerprint for structural schema data."""
    if not isinstance(schema_profile, Mapping):
        return ""
    signature = _schema_signature(schema_profile)
    if not any(signature.values()):
        return ""
    canonical = json.dumps(
        signature,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
