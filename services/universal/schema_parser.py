"""Compatibility shim for ``neo4j_t2c.schema.parser``."""

from neo4j_t2c.schema.parser import (
    ParsedNode,
    ParsedPath,
    ParsedRelationship,
    SchemaGraph,
    parse_schema_text,
)

__all__ = [
    "ParsedNode",
    "ParsedPath",
    "ParsedRelationship",
    "SchemaGraph",
    "parse_schema_text",
]
