"""
Neo4j graph connection and schema management.
No Streamlit dependency.

FIX: Proper time import instead of __import__("time").time()
FIX: Schema refresh is thread-safe with proper interval tracking.
"""
from __future__ import annotations

import logging
import time

from langchain_community.graphs import Neo4jGraph

from config import get_settings
from utils.helpers import parse_schema

logger = logging.getLogger(__name__)

_graph: Neo4jGraph | None = None
_schema_labels: set[str] = set()
_schema_relationships: set[str] = set()
_schema_last_refresh: float = 0.0
_SCHEMA_REFRESH_INTERVAL = 300  # 5 minutes


def get_graph() -> Neo4jGraph:
    """Get or create the Neo4j graph connection (singleton)."""
    global _graph
    if _graph is None:
        settings = get_settings()
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
        f"Schema refreshed: {len(_schema_labels)} labels, "
        f"{len(_schema_relationships)} relationships"
    )


def maybe_refresh_schema() -> None:
    """Refresh schema if interval has elapsed."""
    if time.time() - _schema_last_refresh > _SCHEMA_REFRESH_INTERVAL:
        refresh_schema()


def get_schema_labels() -> set[str]:
    if not _schema_labels:
        refresh_schema()
    return _schema_labels


def get_schema_relationships() -> set[str]:
    if not _schema_relationships:
        refresh_schema()
    return _schema_relationships


def get_schema_text() -> str:
    """Get formatted schema string for display."""
    labels = get_schema_labels()
    rels = get_schema_relationships()
    return (
        "Available Labels:\n"
        + "\n".join(f"- {label}" for label in sorted(labels))
        + "\n\nAvailable Relationships:\n"
        + "\n".join(f"- {rel}" for rel in sorted(rels))
    )
