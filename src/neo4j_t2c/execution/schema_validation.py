"""Validate generated relationship structure against the runtime schema graph."""

from __future__ import annotations

import re

from neo4j_t2c.schema import parse_schema_text

_DIRECTED_PATTERN = re.compile(
    r"(?P<left>\([^)]*\))\s*"
    r"(?P<left_connector><-|-)\s*"
    r"(?P<relationship>\[[^\]]*\])\s*"
    r"(?P<right_connector>->|-)\s*"
    r"(?P<right>\([^)]*\))"
)


def _node_labels(node: str) -> set[str]:
    return set(
        re.findall(
            r":\s*`?([A-Za-z_][A-Za-z0-9_]*)`?",
            re.sub(r"\{[^}]*\}", "", node),
        )
    )


def _relationship_types(relationship: str) -> set[str]:
    return set(
        re.findall(
            r":\s*`?([A-Za-z_][A-Za-z0-9_]*)`?",
            relationship,
        )
    )


def relationship_schema_feedback(cypher: str, runtime_schema: str) -> str | None:
    """Return precise feedback when a typed directed edge contradicts schema."""
    schema = parse_schema_text(runtime_schema)
    declared = {
        (path.start, path.rel_type, path.end)
        for path in schema.paths
    }
    if not declared:
        return None

    for match in _DIRECTED_PATTERN.finditer(cypher or ""):
        left_connector = match.group("left_connector")
        right_connector = match.group("right_connector")
        if left_connector == "-" and right_connector == "->":
            start_node, end_node = match.group("left"), match.group("right")
        elif left_connector == "<-" and right_connector == "-":
            start_node, end_node = match.group("right"), match.group("left")
        else:
            continue

        start_labels = _node_labels(start_node)
        end_labels = _node_labels(end_node)
        rel_types = _relationship_types(match.group("relationship"))
        if not start_labels or not end_labels or not rel_types:
            continue

        for rel_type in rel_types:
            actual = {
                (start_label, rel_type, end_label)
                for start_label in start_labels
                for end_label in end_labels
            }
            if actual & declared:
                continue

            allowed = sorted(
                edge for edge in declared if edge[1] == rel_type
            )
            if not allowed:
                continue

            used = sorted(actual)[0]
            allowed_text = ", ".join(
                f"(:{start})-[:{relationship}]->(:{end})"
                for start, relationship, end in allowed
            )
            used_text = (
                f"(:{used[0]})-[:{used[1]}]->(:{used[2]})"
            )
            return (
                "Runtime schema contradiction: the query uses "
                f"{used_text}, but the relationship is declared as "
                f"{allowed_text}. Repair the relationship direction or endpoint "
                "labels while preserving the question, filters, and return shape."
            )
    return None


__all__ = ["relationship_schema_feedback"]
