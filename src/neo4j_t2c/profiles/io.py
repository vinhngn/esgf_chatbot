"""Profile file loading through the versioned contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from neo4j_t2c.profiles.migration import migrate_profile_payload


def load_profile_file(path: str | Path) -> dict[str, Any]:
    profile_path = Path(path)
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Profile must contain a JSON object: {profile_path}")
    hint = profile_path.stem.removesuffix("_profile")
    return migrate_profile_payload(payload, database_hint=hint)
