"""Compatibility helpers for profile file locations."""

from __future__ import annotations

from pathlib import Path

from config import get_settings
from neo4j_t2c.profiles.paths import (
    profile_build_command as _profile_build_command,
)
from neo4j_t2c.profiles.paths import (
    profile_dir as _profile_dir,
)
from neo4j_t2c.profiles.paths import (
    profile_path as _profile_path,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def profile_dir() -> Path:
    return _profile_dir(base_dir=PROJECT_ROOT)


def profile_path(database: str | None = None) -> Path:
    settings = get_settings()
    db_name = database or settings.profile_database_name or "unknown"
    return _profile_path(db_name, base_dir=PROJECT_ROOT)


def profile_exists(database: str | None = None) -> bool:
    return profile_path(database).is_file()


def profile_build_command(database: str | None = None) -> str:
    settings = get_settings()
    db_name = database or settings.profile_database_name or "unknown"
    return _profile_build_command(
        db_name,
        uri=settings.NEO4J_URI,
        physical_database=settings.NEO4J_DATABASE,
        username=settings.NEO4J_USERNAME,
    )


__all__ = [
    "PROJECT_ROOT",
    "profile_build_command",
    "profile_dir",
    "profile_exists",
    "profile_path",
]
