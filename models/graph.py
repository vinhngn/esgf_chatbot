"""
Neo4j graph connection and schema management.

Supports multi-database connections with auto-routing.
Each database gets its own Neo4jGraph singleton.
"""

from __future__ import annotations

import logging
import threading
import time

from config import get_settings
from langchain_neo4j import Neo4jGraph
from utils.helpers import parse_schema

logger = logging.getLogger(__name__)

# Multi-DB connection pool: db_name -> Neo4jGraph
_graphs: dict[str, Neo4jGraph] = {}
_graph_lock = threading.Lock()

# Schema cache per DB
_schema_cache: dict[str, dict] = {}
_schema_last_refresh: dict[str, float] = {}
_SCHEMA_REFRESH_INTERVAL = 300  # 5 minutes


def get_graph(db_name: str | None = None) -> Neo4jGraph:
    """
    Get or create the Neo4j graph connection (thread-safe).

    If db_name is provided, connects to that specific database.
    Otherwise, uses the database from .env config.
    """
    from templates.db_router import get_db_config

    db = db_name or get_settings().database_name
    if not db:
        db = "movies"  # default fallback

    if db not in _graphs:
        with _graph_lock:
            if db not in _graphs:
                config = get_db_config(db)
                logger.info(
                    "[Graph] Connecting to Neo4j: %s / db=%s",
                    config["url"], db,
                )
                try:
                    _graphs[db] = Neo4jGraph(
                        url=config["url"],
                        username=config["username"],
                        password=config["password"],
                        database=config["database"],
                        sanitize=True,
                    )
                    _refresh_schema(db)
                except Exception as e:
                    logger.error("[Graph] Failed to connect to '%s': %s", db, e)
                    raise

    return _graphs[db]


def _refresh_schema(db_name: str) -> None:
    """Refresh schema for a specific database."""
    graph = _graphs.get(db_name)
    if not graph:
        return
    graph.refresh_schema()
    labels, rels = parse_schema(graph.get_schema)
    _schema_cache[db_name] = {"labels": labels, "relationships": rels}
    _schema_last_refresh[db_name] = time.time()
    logger.info(
        "Schema refreshed for '%s': %d labels, %d relationships",
        db_name, len(labels), len(rels),
    )


def refresh_schema() -> None:
    """Refresh schema for the default database (backward compat)."""
    db = get_settings().database_name or "movies"
    if db in _graphs:
        _refresh_schema(db)


def maybe_refresh_schema(db_name: str | None = None) -> None:
    """Refresh schema only if the refresh interval has elapsed."""
    db = db_name or get_settings().database_name or "movies"
    last = _schema_last_refresh.get(db, 0.0)
    if time.time() - last > _SCHEMA_REFRESH_INTERVAL and db in _graphs:
        _refresh_schema(db)


def get_schema_labels(db_name: str | None = None) -> set[str]:
    """Return the cached set of node labels."""
    db = db_name or get_settings().database_name or "movies"
    cache = _schema_cache.get(db, {})
    if not cache and db in _graphs:
        _refresh_schema(db)
        cache = _schema_cache.get(db, {})
    return cache.get("labels", set())


def get_schema_relationships(db_name: str | None = None) -> set[str]:
    """Return the cached set of relationship types."""
    db = db_name or get_settings().database_name or "movies"
    cache = _schema_cache.get(db, {})
    if not cache and db in _graphs:
        _refresh_schema(db)
        cache = _schema_cache.get(db, {})
    return cache.get("relationships", set())


def get_schema_text(db_name: str | None = None) -> str:
    """Get a human-readable schema string."""
    labels = get_schema_labels(db_name)
    rels = get_schema_relationships(db_name)
    return (
        "Available Labels:\n"
        + "\n".join(f"- {label}" for label in sorted(labels))
        + "\n\nAvailable Relationships:\n"
        + "\n".join(f"- {rel}" for rel in sorted(rels))
    )


def reset_graph(db_name: str | None = None) -> None:
    """Reset a specific DB connection (or all if db_name is None)."""
    with _graph_lock:
        if db_name:
            _graphs.pop(db_name, None)
            _schema_cache.pop(db_name, None)
            _schema_last_refresh.pop(db_name, None)
        else:
            _graphs.clear()
            _schema_cache.clear()
            _schema_last_refresh.clear()
    logger.info("[Graph] Connection reset for: %s", db_name or "ALL")
