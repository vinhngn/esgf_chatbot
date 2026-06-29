from __future__ import annotations

import os
import re
from typing import Any


STOPWORDS = {
    "all",
    "and",
    "are",
    "asked",
    "been",
    "by",
    "connected",
    "first",
    "for",
    "from",
    "have",
    "how",
    "in",
    "into",
    "list",
    "many",
    "most",
    "number",
    "of",
    "posted",
    "questions",
    "records",
    "show",
    "tagged",
    "that",
    "the",
    "through",
    "to",
    "top",
    "users",
    "what",
    "which",
    "who",
    "with",
}

IDENTITY_PROPS = {
    "accountid",
    "customerid",
    "id",
    "movieid",
    "name",
    "orderid",
    "productid",
    "screen_name",
    "screenname",
    "supplierid",
    "title",
    "tmdbid",
    "url",
    "userid",
}

_INDEX_CACHE: dict[int, list[dict]] = {}


def _enabled() -> bool:
    return os.getenv("T2C_ENTITY_RESOLVER_ENABLED", "true").lower() not in {"0", "false", "no", "off"}


def _max_tokens() -> int:
    try:
        return int(os.getenv("T2C_ENTITY_RESOLVER_MAX_TOKENS", "8"))
    except ValueError:
        return 8


def _max_hits() -> int:
    try:
        return int(os.getenv("T2C_ENTITY_RESOLVER_MAX_HITS", "8"))
    except ValueError:
        return 8


def _quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _prop_key(prop: str) -> str:
    return "".join(ch for ch in prop.lower() if ch.isalnum() or ch == "_")


def _is_identity_property(prop: str) -> bool:
    key = _prop_key(prop)
    return key in IDENTITY_PROPS or key.endswith("id") or "name" in key or "title" in key


def _candidate_literals(question: str) -> list[str]:
    quoted = re.findall(r"['\"]([^'\"]{2,80})['\"]", question or "")
    capitalized_phrases = [
        match.group(0)
        for match in re.finditer(
            r"\b[A-Z][a-z0-9]+(?:\s+[A-Z][a-z0-9]+){1,5}\b",
            question or "",
        )
    ]
    words = [
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_+.#-]{1,60}", question or "")
        if token.lower() not in STOPWORDS
    ]
    candidates = quoted + capitalized_phrases + words
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped[: _max_tokens()]


def _safe_indexes(graph: Any) -> list[dict]:
    try:
        return graph.query(
            """
            SHOW INDEXES
            YIELD name, type, entityType, labelsOrTypes, properties
            RETURN name, type, entityType, labelsOrTypes, properties
            """
        )
    except Exception:
        return []


def _indexed_node_properties(graph: Any) -> list[dict]:
    cache_key = id(graph)
    if cache_key in _INDEX_CACHE:
        return _INDEX_CACHE[cache_key]
    indexes = _safe_indexes(graph)
    specs: list[dict] = []
    for index in indexes:
        if index.get("entityType") != "NODE":
            continue
        index_type = str(index.get("type") or "").upper()
        labels = index.get("labelsOrTypes") or []
        props = index.get("properties") or []
        if not labels or not props:
            continue
        if index_type not in {"RANGE", "TEXT", "FULLTEXT", "VECTOR"}:
            continue
        for label in labels:
            if str(label).startswith("_"):
                continue
            for prop in props:
                specs.append(
                    {
                        "index_name": index.get("name"),
                        "index_type": index_type,
                        "label": str(label),
                        "property": str(prop),
                    }
                )
    _INDEX_CACHE[cache_key] = specs
    return specs


def _exact_lookup(graph: Any, label: str, prop: str, literal: str) -> list[dict]:
    cypher = (
        f"MATCH (n:{_quote_ident(label)}) "
        f"WHERE n.{_quote_ident(prop)} = $value "
        f"RETURN labels(n) AS labels, n.{_quote_ident(prop)} AS value, "
        "elementId(n) AS element_id "
        "LIMIT 3"
    )
    try:
        return graph.query(cypher, params={"value": literal})
    except TypeError:
        return graph.query(cypher, {"value": literal})
    except Exception:
        return []


def _fulltext_lookup(graph: Any, index_name: str, literal: str) -> list[dict]:
    try:
        return graph.query(
            """
            CALL db.index.fulltext.queryNodes($index_name, $query)
            YIELD node, score
            RETURN labels(node) AS labels, node AS node, score, elementId(node) AS element_id
            LIMIT 5
            """,
            params={"index_name": index_name, "query": literal},
        )
    except Exception:
        return []


def resolve_question_entities(question: str, graph: Any) -> dict:
    """Resolve question literals to indexed Neo4j nodes without scanning labels."""
    if not _enabled():
        return {"enabled": False, "anchors": [], "indexes": []}

    literals = _candidate_literals(question)
    specs = _indexed_node_properties(graph)
    identity_specs = [spec for spec in specs if _is_identity_property(spec["property"])]

    anchors: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for literal in literals:
        for spec in identity_specs:
            if len(anchors) >= _max_hits():
                break
            if spec["index_type"] == "FULLTEXT":
                hits = _fulltext_lookup(graph, spec["index_name"], literal)
            elif spec["index_type"] in {"RANGE", "TEXT"}:
                hits = _exact_lookup(graph, spec["label"], spec["property"], literal)
            else:
                hits = []
            for hit in hits:
                labels = hit.get("labels") or [spec["label"]]
                value = hit.get("value")
                key = (literal.lower(), spec["label"], spec["property"])
                if key in seen:
                    continue
                seen.add(key)
                anchors.append(
                    {
                        "literal": literal,
                        "label": spec["label"],
                        "property": spec["property"],
                        "value": value,
                        "labels": labels,
                        "index_type": spec["index_type"],
                        "index_name": spec["index_name"],
                    }
                )
                break
        if len(anchors) >= _max_hits():
            break

    return {
        "enabled": True,
        "literals": literals,
        "anchors": anchors,
        "indexed_properties": identity_specs[:20],
    }


def format_entity_resolution_context(resolution: dict) -> str:
    lines = ["=== INDEXED ENTITY/LITERAL RESOLUTION ==="]
    if not resolution.get("enabled", True):
        lines.append("Entity resolver disabled.")
        return "\n".join(lines)
    anchors = resolution.get("anchors") or []
    if not anchors:
        lines.append("No indexed literal anchors were resolved. Do not invent exact entity bindings.")
        indexed = resolution.get("indexed_properties") or []
        if indexed:
            lines.append("Available indexed lookup properties:")
            for spec in indexed[:10]:
                lines.append(f"- ({spec['label']}).{spec['property']} via {spec['index_type']}")
        return "\n".join(lines)
    lines.append("Resolved anchors from indexed lookup. Prefer these exact bindings when relevant:")
    for anchor in anchors:
        lines.append(
            f"- literal {anchor['literal']!r} => "
            f"(:{anchor['label']} {{{anchor['property']}: {anchor['value']!r}}}) "
            f"via {anchor['index_type']}"
        )
    return "\n".join(lines)
