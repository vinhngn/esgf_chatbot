"""Compatibility shim for ``neo4j_t2c.grounding.entities``."""

from neo4j_t2c.grounding.entities import (
    format_entity_resolution_context,
    resolve_question_entities,
)

__all__ = [
    "format_entity_resolution_context",
    "resolve_question_entities",
]
