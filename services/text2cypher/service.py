"""Compatibility service used by the Flask API and benchmark harness."""

from __future__ import annotations

from config import get_settings
from neo4j_t2c.profiles import load_profile_file
from neo4j_t2c.service import get_raw_results
from services.profile_analyzer.store import profile_dir


def get_available_databases() -> list[str]:
    """Return logical databases backed by generated profiles."""
    databases = {get_settings().profile_database_name}
    directory = profile_dir()
    if directory.exists():
        suffix = "_profile.json"
        databases.update(path.name[: -len(suffix)] for path in directory.glob(f"*{suffix}"))
    return sorted(databases)


def get_database_info() -> dict:
    """Return profile metadata for the active logical database."""
    return get_schema_info(get_settings().profile_database_name)


def get_schema_info(database: str | None = None) -> dict:
    """Return versioned profile metadata for one logical database."""
    db_name = database or get_settings().profile_database_name
    path = profile_dir() / f"{db_name.lower()}_profile.json"
    if not path.is_file():
        return {
            "database": db_name,
            "profile_available": False,
            "schema_profile": {},
            "row_count": 0,
        }
    profile = load_profile_file(path)
    return {
        "database": db_name,
        "profile_available": True,
        "profile_version": profile.get("profile_version"),
        "source_type": profile.get("source_type"),
        "schema_fingerprint": profile.get("schema_fingerprint"),
        "schema_profile": profile.get("schema_profile") or {},
        "row_count": profile.get("row_count", 0),
    }


__all__ = [
    "get_available_databases",
    "get_database_info",
    "get_raw_results",
    "get_schema_info",
]
