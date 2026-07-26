"""Compatibility shim for ``neo4j_t2c.profiles.builders.csv``."""

from neo4j_t2c.profiles.builders.csv import (
    build_profile_from_csv,
    build_profiles,
)

__all__ = ["build_profile_from_csv", "build_profiles"]
