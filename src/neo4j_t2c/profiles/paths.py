from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_DIRECTORY = "generated_profiles"


def profile_dir(
    configured: str | os.PathLike[str] | None = None,
    *,
    base_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve the profile directory without importing application settings."""
    configured = configured or os.getenv("T2C_PROFILE_DIR", _DEFAULT_DIRECTORY)
    path = Path(configured)
    if not path.is_absolute():
        path = Path(base_dir) / path if base_dir is not None else Path.cwd() / path
    return path


def profile_path(
    database: str,
    *,
    directory: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Return the conventional JSON path for one normalized database name."""
    db_name = database.strip().lower() or "unknown"
    return profile_dir(directory, base_dir=base_dir) / f"{db_name}_profile.json"


def profile_exists(
    database: str,
    *,
    directory: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> bool:
    return profile_path(database, directory=directory, base_dir=base_dir).is_file()


def profile_build_command(
    database: str,
    *,
    uri: str | None = None,
    physical_database: str | None = None,
    username: str | None = None,
) -> str:
    """Build a copy-pasteable profile command using explicit values or env vars."""
    db_name = database.strip().lower() or "unknown"
    resolved_uri = uri or os.getenv("NEO4J_URI") or "neo4j+s://demo.neo4jlabs.com"
    resolved_database = (
        physical_database
        or os.getenv("NEO4J_DATABASE")
        or db_name
    )
    resolved_username = username or os.getenv("NEO4J_USERNAME")
    username_arg = f" --username {resolved_username}" if resolved_username else ""
    return (
        "py tools/profile_client.py --mode live "
        f"--uri {resolved_uri} "
        f"--database {resolved_database} --profile-name {db_name}"
        f"{username_arg}"
    )
