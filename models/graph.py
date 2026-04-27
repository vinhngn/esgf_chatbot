"""
Neo4j graph connection and schema management.
Uses langchain_neo4j.Neo4jGraph (proper GraphStore subclass) instead of
langchain_community.graphs.Neo4jGraph to be compatible with the new
GraphCypherQAChain which requires a GraphStore instance.
"""

from __future__ import annotations

import logging
import threading
import time

from config import get_settings
from langchain_neo4j import Neo4jGraph
from utils.helpers import parse_schema

logger = logging.getLogger(__name__)

_graph: Neo4jGraph | None = None
_graph_lock = threading.RLock()

_schema_labels: set[str] = set()
_schema_relationships: set[str] = set()
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
    graph = get_graph()
    graph.refresh_schema()
    _schema_labels, _schema_relationships = parse_schema(graph.get_schema)
    _schema_last_refresh = time.time()
    logger.info(
        "Schema refreshed: %d labels, %d relationships",
        len(_schema_labels),
        len(_schema_relationships),
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
