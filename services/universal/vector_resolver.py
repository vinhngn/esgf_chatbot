"""Compatibility shim for ``neo4j_t2c.grounding.vectors``."""

from neo4j_t2c.grounding.vectors import (
    format_vector_context,
    resolve_vector_neighbors,
)

__all__ = ["format_vector_context", "resolve_vector_neighbors"]
