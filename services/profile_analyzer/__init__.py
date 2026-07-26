"""Compatibility imports for the versioned ``neo4j_t2c.profiles`` package."""

from neo4j_t2c.profiles.builders.csv import build_profile_from_csv
from neo4j_t2c.profiles.builders.neo4j import build_profile_from_neo4j

__all__ = ["build_profile_from_csv", "build_profile_from_neo4j"]
