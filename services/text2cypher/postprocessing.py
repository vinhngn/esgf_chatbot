"""Generic, database-independent cleanup for LLM-generated Cypher."""

from __future__ import annotations

import re


def _requested_limit(question: str) -> int | None:
    patterns = (
        r"\b(?:top|first|last|list|show|return)\s+(\d+)\b",
        (
            r"\b(\d+)\s+(?:nodes?|rows?|items?|records?|tweets?|movies?|"
            r"users?|products?|orders?|customers?|suppliers?|hashtags?)\b"
        ),
    )
    for pattern in patterns:
        match = re.search(pattern, question or "", flags=re.IGNORECASE)
        if not match:
            continue
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        if 0 < value <= 1000:
            return value
    return None


def ensure_requested_limit(cypher: str, question: str) -> str:
    """Add an explicit numeric limit requested by the user."""
    if not cypher or re.search(r"(?i)\bLIMIT\s+\d+\b", cypher):
        return cypher
    limit = _requested_limit(question)
    if limit is None:
        return cypher
    return f"{cypher.rstrip()} LIMIT {limit}"


def ensure_rank_order(cypher: str, question: str) -> str:
    """Add ordering when a ranking question has a projected metric."""
    if not cypher or re.search(r"(?i)\bORDER\s+BY\b", cypher):
        return cypher

    lowered = (question or "").lower()
    if not re.search(r"\b(top|highest|largest|most|lowest|least|smallest)\b", lowered):
        return cypher

    direction = "ASC" if re.search(r"\b(lowest|least|smallest)\b", lowered) else "DESC"
    return_body = re.search(
        r"(?is)\bRETURN\b\s+(.*?)(?:\bLIMIT\b|$)",
        cypher,
    )
    if not return_body:
        return cypher

    candidates = re.findall(
        r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b",
        return_body.group(1),
    )
    if not candidates:
        return cypher

    question_tokens = set(re.findall(r"[a-z0-9]+", lowered))
    selected = next(
        (
            candidate
            for candidate in candidates
            if candidate.rsplit(".", 1)[1].lower() in question_tokens
        ),
        "",
    )
    if not selected and len(candidates) > 1:
        selected = candidates[-1]
    if not selected:
        return cypher

    limit_match = re.search(r"(?is)\s+LIMIT\s+\d+\s*$", cypher)
    if limit_match:
        prefix = cypher[: limit_match.start()].rstrip()
        suffix = cypher[limit_match.start() :]
        return f"{prefix} ORDER BY {selected} {direction}{suffix}"
    return f"{cypher.rstrip()} ORDER BY {selected} {direction}"


def repair_backticked_label_with_inline_map(cypher: str) -> str:
    """Repair malformed ``:`Label {property: value}`` node patterns."""
    if not cypher:
        return cypher
    return re.sub(
        r":`([A-Za-z_][A-Za-z0-9_]*)\s+(\{[^`]+?\})`",
        r":\1 \2",
        cypher,
    )
