"""
Neo4j graph connection and schema management.
Uses langchain_neo4j.Neo4jGraph (proper GraphStore subclass) instead of
langchain_community.graphs.Neo4jGraph to be compatible with the new
GraphCypherQAChain which requires a GraphStore instance.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict

from config import get_settings
from langchain_neo4j import Neo4jGraph
from utils.helpers import parse_schema

logger = logging.getLogger(__name__)

_graph: Neo4jGraph | None = None
_graph_lock = threading.Lock()

_schema_labels: set[str] = set()
_schema_relationships: set[str] = set()
_schema_patterns: set[tuple[str, str, str]] = set()
_schema_node_properties: dict[str, list[str]] = {}
_schema_relationship_properties: dict[str, list[str]] = {}
_schema_sample_values: dict[str, list[str]] = {}
_schema_last_refresh: float = 0.0
_SCHEMA_REFRESH_INTERVAL = 300  # 5 minutes


def get_graph() -> Neo4jGraph:
    """Get or create the Neo4j graph connection (thread-safe singleton)."""
    global _graph
    if _graph is None:
        with _graph_lock:
            if _graph is None:  # double-checked locking
                settings = get_settings()
                logger.info(
                    "[Graph] Connecting to Neo4j: %s / db=%s",
                    settings.NEO4J_URI,
                    settings.NEO4J_DATABASE,
                )
                _graph = Neo4jGraph(
                    url=settings.NEO4J_URI,
                    username=settings.NEO4J_USERNAME,
                    password=settings.NEO4J_PASSWORD,
                    database=settings.NEO4J_DATABASE,
                    sanitize=True,
                )
                refresh_schema()
    return _graph


def refresh_schema() -> None:
    """Refresh schema from Neo4j and parse labels/relationships."""
    global _schema_labels, _schema_relationships, _schema_last_refresh
    global _schema_patterns
    global _schema_node_properties, _schema_relationship_properties
    global _schema_sample_values
    graph = get_graph()
    graph.refresh_schema()
    schema_text = graph.get_schema
    _schema_labels, _schema_relationships = parse_schema(schema_text)
    _schema_patterns = _parse_schema_patterns(schema_text)
    _schema_node_properties = _load_node_properties(graph)
    _schema_relationship_properties = _load_relationship_properties(graph)
    _schema_sample_values = _load_sample_values(graph, _schema_node_properties)
    _schema_last_refresh = time.time()
    logger.info(
        "Schema refreshed: %d labels, %d relationships, %d property groups",
        len(_schema_labels),
        len(_schema_relationships),
        len(_schema_node_properties),
    )


def maybe_refresh_schema() -> None:
    """Refresh schema only if the refresh interval has elapsed."""
    if time.time() - _schema_last_refresh > _SCHEMA_REFRESH_INTERVAL:
        refresh_schema()


def get_schema_labels() -> set[str]:
    """Return the cached set of node labels (refreshes if empty)."""
    if not _schema_labels:
        refresh_schema()
    return _schema_labels


def get_schema_relationships() -> set[str]:
    """Return the cached set of relationship types (refreshes if empty)."""
    if not _schema_relationships:
        refresh_schema()
    return _schema_relationships


def get_schema_node_properties() -> dict[str, list[str]]:
    """Return cached node label -> properties metadata."""
    if not _schema_node_properties:
        refresh_schema()
    return _schema_node_properties


def get_schema_patterns() -> set[tuple[str, str, str]]:
    """Return cached valid schema triples as (source_label, rel_type, target_label)."""
    if not _schema_patterns:
        refresh_schema()
    return _schema_patterns


def get_schema_relationship_properties() -> dict[str, list[str]]:
    """Return cached relationship type -> properties metadata."""
    if not _schema_relationship_properties:
        refresh_schema()
    return _schema_relationship_properties


def get_schema_sample_values() -> dict[str, list[str]]:
    """Return cached sample entity values for selected labels."""
    if not _schema_sample_values:
        refresh_schema()
    return _schema_sample_values


def get_schema_context() -> str:
    """Return a compact, prompt-friendly schema summary with properties and examples."""
    labels = sorted(get_schema_labels())
    rels = sorted(get_schema_relationships())
    node_properties = get_schema_node_properties()
    rel_properties = get_schema_relationship_properties()
    sample_values = get_schema_sample_values()

    lines = ["Schema Context:"]

    if labels:
        lines.append("Node labels:")
        for label in labels:
            props = ", ".join(node_properties.get(label, [])[:8]) or "no known properties"
            samples = ", ".join(sample_values.get(label, [])[:3])
            line = f"- {label}: properties [{props}]"
            if samples:
                line += f"; sample values [{samples}]"
            lines.append(line)

    if rels:
        lines.append("Relationship types:")
        for rel in rels:
            props = ", ".join(rel_properties.get(rel, [])[:6]) or "no properties"
            lines.append(f"- {rel}: properties [{props}]")

    patterns = sorted(get_schema_patterns())
    if patterns:
        lines.append("Valid patterns:")
        for source, rel, target in patterns[:20]:
            lines.append(f"- ({source})-[:{rel}]->({target})")

    return "\n".join(lines)


def get_schema_text() -> str:
    """Get a human-readable schema string for display/debugging."""
    labels = get_schema_labels()
    rels = get_schema_relationships()
    return (
        "Available Labels:\n"
        + "\n".join(f"- {label}" for label in sorted(labels))
        + "\n\nAvailable Relationships:\n"
        + "\n".join(f"- {rel}" for rel in sorted(rels))
    )


def _load_node_properties(graph: Neo4jGraph) -> dict[str, list[str]]:
    """Load node properties from Neo4j schema procedures."""
    grouped: dict[str, set[str]] = defaultdict(set)
    try:
        rows = graph.query(
            """
            CALL db.schema.nodeTypeProperties()
            YIELD nodeLabels, propertyName
            RETURN nodeLabels, propertyName
            """
        )
        for row in rows:
            labels = row.get("nodeLabels") or []
            prop = row.get("propertyName")
            if not prop:
                continue
            for label in labels:
                grouped[str(label)].add(str(prop))
    except Exception as e:
        logger.warning("[Graph] Failed to load node properties: %s", e)
    return {label: sorted(props) for label, props in grouped.items()}


def _load_relationship_properties(graph: Neo4jGraph) -> dict[str, list[str]]:
    """Load relationship properties from Neo4j schema procedures."""
    grouped: dict[str, set[str]] = defaultdict(set)
    try:
        rows = graph.query(
            """
            CALL db.schema.relTypeProperties()
            YIELD relType, propertyName
            RETURN relType, propertyName
            """
        )
        for row in rows:
            rel_type = str(row.get("relType") or "").strip(":")
            prop = row.get("propertyName")
            if rel_type and prop:
                grouped[rel_type].add(str(prop))
    except Exception as e:
        logger.warning("[Graph] Failed to load relationship properties: %s", e)
    return {rel: sorted(props) for rel, props in grouped.items()}


def _load_sample_values(
    graph: Neo4jGraph,
    node_properties: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Load a few high-signal sample values to ground entity matching."""
    sample_values: dict[str, list[str]] = {}
    preferred_props = ("name", "title", "screen_name", "categoryName", "companyName")

    for label, properties in node_properties.items():
        selected_prop = next((prop for prop in preferred_props if prop in properties), None)
        if not selected_prop:
            continue
        try:
            rows = graph.query(
                f"""
                MATCH (n:{label})
                WHERE n.{selected_prop} IS NOT NULL
                RETURN DISTINCT toString(n.{selected_prop}) AS value
                ORDER BY value
                LIMIT 3
                """
            )
            values = [str(row.get("value")) for row in rows if row.get("value")]
            if values:
                sample_values[label] = values
        except Exception as e:
            logger.debug(
                "[Graph] Failed to load sample values for %s.%s: %s",
                label,
                selected_prop,
                e,
            )

    return sample_values


def _parse_schema_patterns(schema_text: str) -> set[tuple[str, str, str]]:
    """Extract valid directed label-relationship-label patterns from schema text."""
    patterns: set[tuple[str, str, str]] = set()
    regex = re.compile(
        r"\(:([A-Za-z0-9_]+)\)\s*-\s*\[:([A-Za-z0-9_]+)\]\s*->\s*\(:([A-Za-z0-9_]+)\)"
    )
    for source, rel, target in regex.findall(schema_text):
        patterns.add((source, rel, target))
    return patterns
