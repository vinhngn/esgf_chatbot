"""Compatibility shim for ``neo4j_t2c.grounding.context``."""

from neo4j_t2c.grounding.context import (
    EntityEvidence,
    get_entity_resolution_context,
    get_grounded_schema,
    get_learned_profile_context,
    get_runtime_property_context,
    has_profile_for_current_database,
    schema_grounding_mode,
)

__all__ = [
    "EntityEvidence",
    "get_entity_resolution_context",
    "get_grounded_schema",
    "get_learned_profile_context",
    "get_runtime_property_context",
    "has_profile_for_current_database",
    "schema_grounding_mode",
]
