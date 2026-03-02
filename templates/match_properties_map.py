"""
Auto-generated match properties map for instance verification.

Previous version: hardcoded property maps for each database.
New version: dynamically derived from schema JSON using SchemaGraph's
             primary-search-property detection algorithm.

Public API (unchanged):
    get_match_properties_map(db_name) -> dict
"""

from __future__ import annotations

import logging
from config import get_settings
from templates.schema_loader import load_schema
from templates.schema_graph import build_schema_graph

logger = logging.getLogger(__name__)

_cache: dict[str, dict] = {}


def _build_match_properties(db_name: str) -> dict:
    """
    Auto-derive the match properties map from schema data.

    For each node label, determine which properties to search when
    verifying entity instances in the database.

    Algorithm:
      1. Use SchemaGraph.get_primary_search_property() for primary field
         (uses priority: name > title > screen_name > first String prop)
      2. Add all mandatory String properties as secondary search fields
      3. Result: {label: [prop1, prop2, ...]}
    """
    schema_data = load_schema(db_name)
    if not schema_data:
        return {}

    graph = build_schema_graph(schema_data)
    match_map: dict[str, list[str]] = {}

    for label in graph.node_labels:
        props = graph.get_node_properties(label)
        primary = graph.get_primary_search_property(label)

        search_props: list[str] = []

        # Primary search property first
        if primary:
            search_props.append(primary)

        # Add other mandatory String properties as secondary
        for p in props:
            pname = p["property"]
            types = p.get("types", [])
            if pname in search_props:
                continue
            if "String" in types and p.get("mandatory", False):
                search_props.append(pname)

        # Cap at 3 search properties per label (performance)
        match_map[label] = search_props[:3]

    logger.info(
        "[MatchProperties] Built map for '%s': %d labels",
        db_name,
        len(match_map),
    )
    return match_map


def get_match_properties_map(db_name: str | None = None) -> dict:
    """
    Get the match properties map for the given database.
    Unchanged signature -- drop-in replacement.
    """
    db = db_name or get_settings().database_name
    if db not in _cache:
        _cache[db] = _build_match_properties(db)
    return _cache[db]


# Module-level default (backward compat)
match_properties_map = get_match_properties_map()
