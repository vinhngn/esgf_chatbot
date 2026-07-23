from __future__ import annotations

import os
from pathlib import Path

from config import get_settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def profile_dir() -> Path:
    configured = os.getenv("T2C_PROFILE_DIR", "generated_profiles")
    path = Path(configured)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def profile_path(database: str | None = None) -> Path:
    db_name = (database or get_settings().profile_database_name or "unknown").strip().lower()
    return profile_dir() / f"{db_name}_profile.json"


def profile_exists(database: str | None = None) -> bool:
    return profile_path(database).exists()


def profile_build_command(database: str | None = None) -> str:
    settings = get_settings()
    db_name = (database or settings.profile_database_name or "unknown").strip().lower()
    username_arg = f" --username {settings.NEO4J_USERNAME}" if settings.NEO4J_USERNAME else ""
    return (
        "py tools/profile_client.py --mode live "
        f"--uri {settings.NEO4J_URI or 'neo4j+s://demo.neo4jlabs.com'} "
        f"--database {settings.NEO4J_DATABASE} --profile-name {db_name}"
        f"{username_arg}"
    )
