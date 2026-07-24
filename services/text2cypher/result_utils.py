"""Normalize Text-to-Cypher pipeline results for API consumers."""

from __future__ import annotations

import urllib.parse


def extract_cypher_queries(
    chain_result: dict,
) -> tuple[str | None, str | None]:
    """Return URL-encoded and raw Cypher from pipeline intermediate steps."""
    steps = chain_result.get("intermediate_steps", [])
    if not isinstance(steps, list):
        return None, None

    for step in steps:
        if not isinstance(step, dict):
            continue
        cleaned = step.get("query")
        if cleaned:
            return urllib.parse.quote(cleaned), cleaned
    return None, None
