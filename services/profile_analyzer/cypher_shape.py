"""Compatibility shim for ``neo4j_t2c.profiles.cypher_analysis``."""

from neo4j_t2c.profiles.cypher_analysis import (
    CypherShape,
    ReturnItem,
    build_signature,
    parse_cypher_shape,
    split_top_level_commas,
    summarize_shapes,
)

__all__ = [
    "CypherShape",
    "ReturnItem",
    "build_signature",
    "parse_cypher_shape",
    "split_top_level_commas",
    "summarize_shapes",
]
