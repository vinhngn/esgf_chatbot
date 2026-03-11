from __future__ import annotations

import json
import re
from datetime import date, datetime, time


def _is_json_serializable(value) -> bool:
    """Check if a value can be serialized by json.dumps."""
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError, OverflowError):
        return False


def normalize_value(value):
    if value is None:
        return value
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(normalize_value(v) for v in value)
    # Neo4j temporal types and any other non-serializable objects → str()
    if hasattr(value, 'iso_format'):
        return value.iso_format()
    if not _is_json_serializable(value):
        return str(value)
    return value


def strip_quotes(s: str) -> str:
    return s.strip("'").strip('"')


def parse_schema(schema_text: str) -> tuple[set[str], set[str]]:
    """Parse Neo4j schema text to extract node labels and relationship types."""
    labels, relationships = set(), set()
    for line in schema_text.splitlines():
        for label in re.findall(r"\(:([A-Za-z0-9_]+)\)", line):
            labels.add(label)
        for rel in re.findall(r"\[:([A-Za-z0-9_]+)\]", line):
            relationships.add(rel)
    return labels, relationships


def clean_cypher_query(query_raw: str) -> str:
    """Clean up LLM-generated Cypher query (remove markdown, prefix, semicolons)."""
    query = re.sub(r"```cypher\s*", "", query_raw, flags=re.IGNORECASE)
    query = re.sub(r"```\s*", "", query)
    query = re.sub(r"^\s*cypher\s+", "", query, flags=re.IGNORECASE)
    query = query.rstrip(";").strip()
    return normalize_cypher_query(query)


def normalize_cypher_query(query: str) -> str:
    """Normalize a few common LLM Cypher mistakes without changing intent."""
    if not query:
        return ""

    normalized = query

    # Convert invalid COUNT(pattern) into Neo4j 5 count{} syntax.
    normalized = re.sub(
        r"COUNT\s*\(\s*(\([^)]+\)\s*-\s*\[[^]]+\]\s*->\s*\([^)]+\))\s*\)",
        r"count{\1}",
        normalized,
        flags=re.IGNORECASE,
    )

    # Convert invalid COUNT(:Label) into COUNT(*) when the model tries to count matched rows.
    normalized = re.sub(
        r"\bCOUNT\s*\(\s*:\s*[A-Za-z_][A-Za-z0-9_]*\s*\)",
        "COUNT(*)",
        normalized,
        flags=re.IGNORECASE,
    )

    # Normalize COUNT(*) spacing/casing for downstream comparisons.
    normalized = re.sub(r"\bCOUNT\s*\(\s*\*\s*\)", "COUNT(*)", normalized, flags=re.IGNORECASE)

    # Remove accidental doubled whitespace introduced by repairs.
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)

    return normalized.strip()
