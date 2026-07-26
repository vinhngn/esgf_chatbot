"""Versioned database profile contracts and migration helpers."""

from neo4j_t2c.profiles.fingerprint import schema_fingerprint
from neo4j_t2c.profiles.io import load_profile_file
from neo4j_t2c.profiles.merge import merge_profile_with_live_schema
from neo4j_t2c.profiles.migration import migrate_profile_payload
from neo4j_t2c.profiles.models import (
    PROFILE_VERSION,
    ProfileDocument,
    ProfileProvenance,
)

__all__ = [
    "PROFILE_VERSION",
    "ProfileDocument",
    "ProfileProvenance",
    "load_profile_file",
    "merge_profile_with_live_schema",
    "migrate_profile_payload",
    "schema_fingerprint",
]
