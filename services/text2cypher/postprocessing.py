"""Compatibility shim for ``neo4j_t2c.generation.postprocessing``."""

from neo4j_t2c.generation.postprocessing import (
    repair_backticked_label_with_inline_map,
)

__all__ = [
    "repair_backticked_label_with_inline_map",
]
