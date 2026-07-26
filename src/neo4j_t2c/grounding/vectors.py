"""Expose anchored Neo4j vector capabilities without interpreting language."""

from __future__ import annotations

import os


def _enabled() -> bool:
    return os.getenv("T2C_VECTOR_RESOLVER_ENABLED", "true").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def resolve_vector_neighbors(
    question: str,
    graph: object,
    entity_resolution: dict,
) -> dict:
    """Return vector-search capabilities; the LLM decides whether they are relevant."""
    del question, graph
    if not _enabled():
        return {"enabled": False, "active": False, "reason": "disabled"}

    anchors = [
        anchor
        for anchor in entity_resolution.get("anchors") or []
        if anchor.get("element_id")
    ]
    vector_indexes = entity_resolution.get("vector_indexes") or []
    capabilities: list[dict] = []
    for anchor in anchors:
        anchor_labels = set(anchor.get("labels") or [anchor.get("label")])
        for spec in vector_indexes:
            if spec.get("label") not in anchor_labels:
                continue
            capabilities.append(
                {
                    "anchor": {
                        "label": anchor.get("label"),
                        "property": anchor.get("property"),
                        "value": anchor.get("value"),
                        "element_id": anchor.get("element_id"),
                    },
                    "index_name": spec.get("index_name"),
                    "label": spec.get("label"),
                    "embedding_property": spec.get("property"),
                }
            )

    return {
        "enabled": True,
        "active": bool(capabilities),
        "reason": "anchored_vector_capability_available" if capabilities else "no_compatible_anchor",
        "capabilities": capabilities[:4],
    }


def format_vector_context(resolution: dict) -> str:
    capabilities = resolution.get("capabilities") or []
    if not capabilities:
        return ""

    lines = [
        "=== OPTIONAL ANCHORED VECTOR SEARCH ===",
        "Use only if the question requires semantic or vector similarity.",
    ]
    for capability in capabilities:
        anchor = capability["anchor"]
        lines.append(
            "- anchor "
            f"(:{anchor['label']} {{{anchor['property']}: {anchor['value']!r}}}); "
            f"vector index={capability['index_name']!r}; "
            f"embedding property={capability['embedding_property']!r}"
        )
    lines.append(
        "Pattern: match the anchor, then call db.index.vector.queryNodes("
        "indexName, k, anchor.embeddingProperty) YIELD node, score."
    )
    return "\n".join(lines)
