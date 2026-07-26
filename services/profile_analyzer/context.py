"""Compatibility shim for ``neo4j_t2c.profiles.retrieval``."""

from neo4j_t2c.profiles.retrieval import (
    format_profile_context,
    load_profile,
    select_profile_context,
)

__all__ = [
    "format_profile_context",
    "load_profile",
    "select_profile_context",
]
