"""Compatibility shim for ``neo4j_t2c.grounding.schema``."""

from neo4j_t2c.grounding.schema import (
    GroundingSelection,
    build_grounded_schema,
)

__all__ = ["GroundingSelection", "build_grounded_schema"]
