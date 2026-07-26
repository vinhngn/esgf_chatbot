"""Profile builders, loaded lazily so CSV analysis needs no Neo4j driver."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["build_profile_from_csv", "build_profile_from_neo4j"]

_BUILDERS = {
    "build_profile_from_csv": "neo4j_t2c.profiles.builders.csv",
    "build_profile_from_neo4j": "neo4j_t2c.profiles.builders.neo4j",
}


def __getattr__(name: str) -> Any:
    module_name = _BUILDERS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
