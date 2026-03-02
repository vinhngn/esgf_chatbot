"""
Auto-generated entity definitions for triple extraction.

Previous version: hardcoded descriptions for each database.
New version: dynamically built from schema JSON using SchemaGraph.

Public API (unchanged):
    get_entity_definitions(db_name) -> str
"""

from __future__ import annotations

import logging
from config import get_settings
from templates.schema_loader import load_schema
from templates.schema_graph import build_schema_graph

logger = logging.getLogger(__name__)

_cache: dict[str, str] = {}


def _build_entity_definitions(db_name: str) -> str:
    """
    Auto-generate entity definitions from schema introspection data.

    For each node label, lists:
    - Label name
    - Properties with types and mandatory/optional status
    - Node count in database
    - Primary search property

    For each relationship type, lists:
    - Relationship type
    - Connected node labels (start -> end)
    - Properties on the relationship (if any)
    """
    schema_data = load_schema(db_name)
    if not schema_data:
        return f"No schema data available for database '{db_name}'."

    graph = build_schema_graph(schema_data)
    lines: list[str] = [f"=== Entity definitions for '{db_name}' database ===\n"]

    # -- Node labels --
    lines.append("### Node Labels ###")
    centrality = graph.degree_centrality()
    sorted_labels = sorted(centrality, key=centrality.get, reverse=True)

    for label in sorted_labels:
        count = graph.get_node_count(label)
        props = graph.get_node_properties(label)
        search_prop = graph.get_primary_search_property(label)

        lines.append(f"\n- **{label}** ({count:,} nodes)")
        if search_prop:
            lines.append(f"  Primary identifier: {search_prop}")
        if props:
            lines.append("  Properties:")
            for p in props:
                types_str = ", ".join(p.get("types", []))
                req = "required" if p.get("mandatory") else "optional"
                lines.append(f"    - {p['property']} ({types_str}, {req})")

    # -- Relationship types --
    lines.append("\n### Relationship Types ###")
    for edge in graph.edges:
        start, end, rel = edge["start"], edge["end"], edge["type"]
        rel_props = edge.get("properties", [])
        prop_str = ""
        if rel_props:
            prop_names = [f"{p['property']} ({', '.join(p.get('types', []))})"
                          for p in rel_props]
            prop_str = f" -- Properties: {', '.join(prop_names)}"
        lines.append(f"\n- [:{rel}] ({start} -> {end}){prop_str}")

    return "\n".join(lines)


def get_entity_definitions(db_name: str | None = None) -> str:
    """
    Get entity definitions for the given database.
    Unchanged signature -- drop-in replacement.
    """
    db = db_name or get_settings().database_name
    if db not in _cache:
        _cache[db] = _build_entity_definitions(db)
    return _cache[db]


# Module-level default (backward compat for any code that imports this directly)
entity_definitions = get_entity_definitions()
