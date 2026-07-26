from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

_INDEX_CACHE: dict[int, list[dict]] = {}


def _enabled() -> bool:
    return os.getenv("T2C_ENTITY_RESOLVER_ENABLED", "true").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


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


def _schema_vocabulary(graph: Any, specs: list[dict]) -> set[str]:
    vocabulary: set[str] = set()
    schema = getattr(graph, "schema", "")
    if callable(schema):
        schema = schema()
    if not schema:
        schema = getattr(graph, "get_schema", "")
    if callable(schema):
        schema = schema()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]*", str(schema or "")):
        vocabulary.add(_normalized_literal(token).replace(" ", ""))
    for spec in specs:
        vocabulary.add(_normalized_literal(spec.get("label")).replace(" ", ""))
        vocabulary.add(_normalized_literal(spec.get("property")).replace(" ", ""))
    return vocabulary


def _candidate_literals(question: str, schema_vocabulary: set[str]) -> list[str]:
    quoted = re.findall(r"['\"]([^'\"]{2,80})['\"]", question or "")
    capitalized_phrases = [
        match.group(0)
        for match in re.finditer(
            r"\b[A-Z][a-z0-9]+(?:\s+[A-Z][a-z0-9]+){0,5}\b",
            question or "",
        )
        if not (
            match.start() == 0 and " " not in match.group(0)
        )
        and _normalized_literal(match.group(0)).replace(" ", "")
        not in schema_vocabulary
    ]
    symbolic_tokens = [
        token
        for token in re.findall(r"\b[A-Za-z0-9][A-Za-z0-9_+.#-]{1,60}\b", question or "")
        if any(char.isdigit() or char in "+.#-_" for char in token)
        and _normalized_literal(token).replace(" ", "") not in schema_vocabulary
    ]
    candidates = quoted + capitalized_phrases + symbolic_tokens
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


def _node_property(node: Any, property_name: str) -> Any:
    if isinstance(node, Mapping):
        return node.get(property_name)
    try:
        return node[property_name]
    except (KeyError, TypeError):
        return None


def _normalized_literal(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _fulltext_value_matches(literal: str, value: Any) -> bool:
    expected = _normalized_literal(literal)
    observed = _normalized_literal(value)
    if not expected or not observed:
        return False
    if expected in observed or observed in expected:
        return True
    expected_terms = set(expected.split())
    observed_terms = set(observed.split())
    return bool(expected_terms) and expected_terms <= observed_terms


def resolve_question_entities(question: str, graph: Any) -> dict:
    """Resolve question literals to indexed Neo4j nodes without scanning labels."""
    if not _enabled():
        return {"enabled": False, "anchors": [], "indexes": []}

    specs = _indexed_node_properties(graph)
    literals = _candidate_literals(question, _schema_vocabulary(graph, specs))
    searchable_specs = [
        spec
        for spec in specs
        if spec["index_type"] in {"RANGE", "TEXT", "FULLTEXT"}
    ]

    anchors: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for literal in literals:
        for spec in searchable_specs:
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
                if value is None:
                    value = _node_property(hit.get("node"), spec["property"])
                if (
                    spec["index_type"] == "FULLTEXT"
                    and not _fulltext_value_matches(literal, value)
                ):
                    continue
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
                        "element_id": hit.get("element_id"),
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
        "indexed_properties": searchable_specs[:20],
        "vector_indexes": [
            spec for spec in specs if spec["index_type"] == "VECTOR"
        ],
    }


def format_entity_resolution_context(resolution: dict) -> str:
    if not resolution.get("enabled", True):
        return ""
    anchors = resolution.get("anchors") or []
    if not anchors:
        return ""
    lines = ["=== INDEXED ENTITY/LITERAL RESOLUTION ==="]
    lines.append("Resolved anchors from indexed lookup. Prefer these exact bindings when relevant:")
    for anchor in anchors:
        lines.append(
            f"- literal {anchor['literal']!r} => "
            f"(:{anchor['label']} {{{anchor['property']}: {anchor['value']!r}}}) "
            f"via {anchor['index_type']}"
        )
    return "\n".join(lines)
