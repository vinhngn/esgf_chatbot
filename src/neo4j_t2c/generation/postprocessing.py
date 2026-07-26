"""Syntax-only cleanup for LLM-generated Cypher."""

from __future__ import annotations

import re


def repair_backticked_label_with_inline_map(cypher: str) -> str:
    """Repair malformed ``:`Label {property: value}`` node patterns."""
    if not cypher:
        return cypher
    return re.sub(
        r":`([A-Za-z_][A-Za-z0-9_]*)\s+(\{[^`]+?\})`",
        r":\1 \2",
        cypher,
    )
