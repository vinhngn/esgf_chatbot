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
    return query.rstrip(";").strip()


def rewrite_bare_node_returns(cypher: str, graph) -> str:
    """
    Detect RETURN clauses that return bare node variables (e.g. RETURN m, RETURN p)
    and rewrite them to return explicit properties instead.

    This is critical for t2c_eval_framework scoring: returning a full node causes
    container flattening which creates extra columns → __MISSING__ → exact_match=0.

    Strategy:
      1. Parse the RETURN clause to find bare variables (no dots, no functions).
      2. For each bare variable, find its label from the MATCH clause.
      3. Query the graph schema for that label's properties.
      4. Rewrite RETURN to project those properties explicitly.
    """
    if not cypher or "RETURN" not in cypher.upper():
        return cypher

    # Extract the RETURN clause
    match = re.search(r"(?i)\bRETURN\b\s+(.*)", cypher)
    if not match:
        return cypher

    return_body = match.group(1)

    # Strip ORDER BY / LIMIT / SKIP from the return body for analysis
    return_core = re.split(
        r"\bORDER BY\b|\bLIMIT\b|\bSKIP\b",
        return_body,
        flags=re.IGNORECASE,
    )[0].strip()

    suffix = return_body[len(return_core):]

    # Split return items by top-level commas
    items = _split_top_level_commas(return_core)

    # Find variable→label mapping from MATCH clauses
    var_to_label = _extract_var_labels(cypher)

    # Check which items are bare variables
    new_items = []
    changed = False
    for item in items:
        clean_item = re.sub(r"(?i)\bDISTINCT\b", "", item).strip()
        has_distinct = "DISTINCT" in item.upper()

        # Check if it's a bare variable (single identifier, no dots, no parens, no AS)
        if (
            re.match(r"^[a-zA-Z_]\w*$", clean_item)
            and clean_item in var_to_label
            and " AS " not in item.upper()
        ):
            label = var_to_label[clean_item]
            props = _get_label_properties(label, graph)
            if props:
                prefix = "DISTINCT " if has_distinct else ""
                expanded = ", ".join(
                    f"{prefix}{clean_item}.{p}" for p in props
                )
                new_items.append(expanded)
                changed = True
                continue

        new_items.append(item)

    if not changed:
        return cypher

    new_return = "RETURN " + ", ".join(new_items) + suffix
    # Replace the old RETURN clause
    cypher_before_return = cypher[: match.start()]
    return cypher_before_return + new_return


def _split_top_level_commas(s: str) -> list[str]:
    """Split a string by commas, respecting parentheses and quotes."""
    items: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quote = False
    quote_char = None

    for ch in s:
        if ch in ("'", '"'):
            if not in_quote:
                in_quote = True
                quote_char = ch
            elif quote_char == ch:
                in_quote = False
                quote_char = None
        if not in_quote:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(0, depth - 1)
            if ch == "," and depth == 0:
                item = "".join(buf).strip()
                if item:
                    items.append(item)
                buf = []
                continue
        buf.append(ch)

    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return items


def _extract_var_labels(cypher: str) -> dict[str, str]:
    """Extract variable→label mapping from MATCH/OPTIONAL MATCH clauses."""
    var_to_label: dict[str, str] = {}
    # Match patterns like (v:Variable), (s:Source), (m:Movie)
    for m in re.finditer(r"\((\w+):(\w+)(?:\s*\{[^}]*\})?\)", cypher):
        var_name, label = m.group(1), m.group(2)
        var_to_label[var_name] = label
    return var_to_label


def _get_label_properties(label: str, graph) -> list[str]:
    """
    Get the list of property names for a node label from the graph schema.
    Returns a sorted list of property names, or empty list if unavailable.
    """
    if graph is None:
        return []
    try:
        schema_text = graph.get_schema
        # Parse properties from schema text like:
        # - **Movie**
        #   - `title`: STRING
        #   - `released`: INTEGER
        # Or from structured schema:
        # Node properties: [(:Movie {title: STRING, released: INTEGER})]
        props: list[str] = []

        # Pattern 1: structured schema format
        pattern1 = re.compile(
            rf"\(:{re.escape(label)}\s*\{{([^}}]+)\}}\)", re.IGNORECASE
        )
        m = pattern1.search(schema_text)
        if m:
            for prop_match in re.finditer(r"(\w+):", m.group(1)):
                props.append(prop_match.group(1))

        # Pattern 2: markdown-style schema
        if not props:
            in_label = False
            for line in schema_text.splitlines():
                if re.match(rf"^\s*-\s*\*\*{re.escape(label)}\*\*", line):
                    in_label = True
                    continue
                if in_label:
                    prop_m = re.match(r"^\s*-\s*`(\w+)`", line)
                    if prop_m:
                        props.append(prop_m.group(1))
                    elif re.match(r"^\s*-\s*\*\*", line):
                        break  # next label

        # Fallback: query Neo4j directly
        if not props:
            try:
                rows = graph.query(
                    f"MATCH (n:{label}) WITH n LIMIT 1 "
                    f"RETURN keys(n) AS props"
                )
                if rows and rows[0].get("props"):
                    props = sorted(rows[0]["props"])
            except Exception:
                pass

        return sorted(set(props)) if props else []
    except Exception:
        return []
