"""Filesystem profile persistence."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from neo4j_t2c.profiles import load_profile_file, migrate_profile_payload

_PROFILE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class JsonProfileStore:
    """Store profiles as readable JSON files with atomic replacement."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().resolve()

    def path_for(self, database: str) -> Path:
        normalized = database.strip().lower()
        if not normalized or not _PROFILE_NAME.fullmatch(normalized):
            raise ValueError(
                "Database profile names may contain only letters, numbers, underscores, and hyphens"
            )
        return self.directory / f"{normalized}_profile.json"

    def exists(self, database: str) -> bool:
        return self.path_for(database).is_file()

    def load(self, database: str) -> dict[str, Any]:
        return load_profile_file(self.path_for(database))

    def save(self, database: str, profile: Mapping[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.path_for(database)
        normalized = migrate_profile_payload(
            profile,
            database_hint=database,
        )
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.stem}-",
            suffix=".tmp",
            dir=self.directory,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(
                    normalized,
                    stream,
                    ensure_ascii=False,
                    indent=2,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
