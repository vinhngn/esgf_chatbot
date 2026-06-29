from __future__ import annotations

import os
import re
from typing import Any

from services.universal.schema_parser import parse_schema_text


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 1
    }


def _label_forms(label: str) -> set[str]:
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[0-9]+", label)
    forms = {label.lower()}
    for word in words:
        lowered = word.lower()
        forms.add(lowered)
        if lowered.endswith("y"):
            forms.add(lowered[:-1] + "ies")
        else:
            forms.add(lowered + "s")
    return forms


def _labels_from_context(text: str) -> set[str]:
    labels: set[str] = set()
    labels.update(re.findall(r"\(([A-Za-z_][A-Za-z0-9_]*)\)", text or ""))
    labels.update(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\.", text or ""))
    return labels


def _quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _runtime_sample_limit() -> int:
    try:
        return int(os.getenv("T2C_RUNTIME_PROPERTY_SAMPLE_LIMIT", "2000"))
    except ValueError:
        return 2000


def _property_counts(graph: Any, label: str, properties: list[str], sample_limit: int) -> dict[str, int]:
    if not properties:
        return {}
    projections = ["count(n) AS __total"]
    alias_to_prop: dict[str, str] = {}
    for idx, prop in enumerate(properties):
        alias = f"p{idx}"
        alias_to_prop[alias] = prop
        projections.append(f"count(n.{_quote_ident(prop)}) AS {alias}")
    if sample_limit > 0:
        cypher = (
            f"MATCH (n:{_quote_ident(label)}) "
            "WITH n LIMIT $sample_limit RETURN "
            + ", ".join(projections)
        )
        rows = graph.query(cypher, params={"sample_limit": sample_limit})
    else:
        cypher = f"MATCH (n:{_quote_ident(label)}) RETURN " + ", ".join(projections)
        rows = graph.query(cypher)
    if not rows:
        return {}
    row = rows[0]
    total = int(row.get("__total") or 0)
    counts = {"__total": total}
    for alias, prop in alias_to_prop.items():
        counts[prop] = int(row.get(alias) or 0)
    return counts


def build_runtime_property_context(
    *,
    question: str,
    runtime_schema: str,
    learned_context: str,
    graph: Any,
    max_labels: int = 8,
    max_properties_per_label: int = 12,
    sample_limit: int | None = None,
) -> tuple[str, dict]:
    """Build compact property evidence from schema plus live Neo4j counts."""
    schema_graph = parse_schema_text(runtime_schema)
    question_tokens = _tokens(question)
    context_labels = _labels_from_context(learned_context)

    scored_labels: list[tuple[float, str]] = []
    for label in schema_graph.get_all_labels():
        score = 0.0
        if label in context_labels:
            score += 4.0
        if question_tokens & _label_forms(label):
            score += 3.0
        if score > 0:
            scored_labels.append((score, label))

    if not scored_labels:
        scored_labels = [(1.0, label) for label in schema_graph.get_all_labels()[:max_labels]]

    labels = [
        label
        for _, label in sorted(scored_labels, key=lambda item: (-item[0], item[1]))[:max_labels]
    ]

    lines = [
        "=== RUNTIME PROPERTY EVIDENCE ===",
        "Use these schema-valid properties for projection, filtering, and ordering.",
        "Prefer properties with non_null > 0. If ORDER BY/LIMIT uses a nullable property, add an IS NOT NULL guard when it preserves intent.",
    ]
    resolved_sample_limit = _runtime_sample_limit() if sample_limit is None else sample_limit
    debug: dict[str, Any] = {
        "labels": labels,
        "properties": {},
        "sample_limit": resolved_sample_limit,
    }

    for label in labels:
        props = sorted(schema_graph.get_node_properties(label).keys())[:max_properties_per_label]
        if not props:
            continue
        try:
            counts = _property_counts(graph, label, props, resolved_sample_limit)
        except Exception as exc:
            counts = {}
            debug.setdefault("errors", {})[label] = str(exc)
        total = counts.get("__total")
        prop_bits = []
        for prop in props:
            if prop in counts:
                suffix = "sample" if resolved_sample_limit > 0 else "full"
                prop_bits.append(f"{prop}(non_null={counts[prop]}/{total} {suffix})")
            else:
                prop_bits.append(prop)
        lines.append(f"- {label}: " + ", ".join(prop_bits))
        debug["properties"][label] = {"total": total, "properties": counts}

    return "\n".join(lines), debug
