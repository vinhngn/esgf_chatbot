"""Contract checks and bounded metamorphic verification for generated Cypher."""

from __future__ import annotations

import re
from typing import Any

from neo4j_t2c.planning.models import (
    MetamorphicProbe,
    QueryOperation,
    SemanticContract,
    SortDirection,
    VerificationVerdict,
)

_AGGREGATE = re.compile(
    r"(?i)\b(count|avg|sum|min|max|collect)\s*\("
)
_ORDER_BY = re.compile(r"(?i)\bORDER\s+BY\b")
_DESCENDING = re.compile(r"(?i)\bORDER\s+BY\b.*?\bDESC\b")
_LIMIT = re.compile(r"(?i)\bLIMIT\s+(\d+)\s*;?\s*$")


def _expanded_limit_query(
    cypher: str,
    *,
    current_limit: int,
    row_cap: int,
) -> str | None:
    expanded = min(current_limit + 1, row_cap)
    if expanded <= current_limit:
        return None
    match = _LIMIT.search(cypher)
    if match is None:
        return None
    return (
        cypher[: match.start(1)]
        + str(expanded)
        + cypher[match.end(1) :]
    )


def _contract_contradictions(
    contract: SemanticContract,
    cypher: str,
    rows: list[Any],
) -> list[str]:
    contradictions: list[str] = []
    has_aggregate = bool(_AGGREGATE.search(cypher))
    has_count = bool(re.search(r"(?i)\bcount\s*\(", cypher))
    has_order = bool(_ORDER_BY.search(cypher))
    limit_match = _LIMIT.search(cypher)
    query_limit = int(limit_match.group(1)) if limit_match else None

    if contract.operation == QueryOperation.COUNT and not has_count:
        contradictions.append(
            "The semantic contract requests a count, but the query has no count aggregation."
        )
    elif (
        contract.operation == QueryOperation.AGGREGATE
        and not has_aggregate
    ):
        contradictions.append(
            "The semantic contract requests aggregation, but the query has no aggregate function."
        )
    elif (
        contract.operation == QueryOperation.RETRIEVE
        and has_aggregate
    ):
        contradictions.append(
            "The semantic contract requests entity retrieval, but the query aggregates the result."
        )

    if contract.operation == QueryOperation.RANK and not has_order:
        contradictions.append(
            "The semantic contract requests ranking, but the query has no ORDER BY clause."
        )
    if contract.order == SortDirection.DESCENDING:
        if not has_order or not _DESCENDING.search(cypher):
            contradictions.append(
                "The semantic contract requests descending order, but the query does not order DESC."
            )
    elif (
        contract.order == SortDirection.ASCENDING
        and _DESCENDING.search(cypher)
    ):
        contradictions.append(
            "The semantic contract requests ascending order, but the query orders DESC."
        )

    if contract.limit is not None:
        if query_limit is None:
            contradictions.append(
                f"The semantic contract requests LIMIT {contract.limit}, but the query has no literal LIMIT."
            )
        elif query_limit != contract.limit:
            contradictions.append(
                f"The semantic contract requests LIMIT {contract.limit}, but the query uses LIMIT {query_limit}."
            )
        if len(rows) > contract.limit:
            contradictions.append(
                f"The result contains {len(rows)} rows, exceeding the requested limit {contract.limit}."
            )
    return contradictions


def _top_k_probe(
    *,
    contract: SemanticContract,
    cypher: str,
    rows: list[Any],
    graph: Any,
    row_cap: int,
) -> MetamorphicProbe | None:
    if (
        contract.operation != QueryOperation.RANK
        or contract.limit is None
        or not _ORDER_BY.search(cypher)
    ):
        return None
    expanded_cypher = _expanded_limit_query(
        cypher,
        current_limit=contract.limit,
        row_cap=row_cap,
    )
    if expanded_cypher is None:
        return None
    try:
        expanded_rows = graph.query(expanded_cypher)
    except Exception as exc:
        return MetamorphicProbe(
            name="top_k_prefix_consistency",
            cypher=expanded_cypher,
            passed=False,
            error=f"{type(exc).__name__}: {exc}",
            detail="Expanded LIMIT probe could not be executed.",
        )

    prefix_matches = expanded_rows[: len(rows)] == rows
    cardinality_monotonic = len(expanded_rows) >= len(rows)
    passed = prefix_matches and cardinality_monotonic
    return MetamorphicProbe(
        name="top_k_prefix_consistency",
        cypher=expanded_cypher,
        passed=passed,
        detail=(
            "Increasing LIMIT preserved the original ordered prefix."
            if passed
            else (
                "Increasing LIMIT changed the original prefix or reduced "
                "cardinality; ordering may be unstable or semantically inconsistent."
            )
        ),
    )


def verify_candidate(
    *,
    contract: SemanticContract,
    cypher: str,
    rows: list[Any],
    graph: Any | None = None,
    row_cap: int = 100,
) -> VerificationVerdict:
    """Verify meaning-bearing invariants without treating execution as proof."""
    contradictions = _contract_contradictions(
        contract,
        cypher,
        rows,
    )
    probes: list[MetamorphicProbe] = []
    warnings: list[str] = []
    if graph is not None and not contradictions:
        probe = _top_k_probe(
            contract=contract,
            cypher=cypher,
            rows=rows,
            graph=graph,
            row_cap=row_cap,
        )
        if probe is not None:
            probes.append(probe)
            if not probe.passed:
                warnings.append(probe.detail)

    return VerificationVerdict(
        passed=not contradictions,
        contradictions=tuple(contradictions),
        warnings=tuple(warnings),
        probes=tuple(probes),
    )


def verification_feedback(verdict: VerificationVerdict) -> str:
    """Build focused repair feedback from hard contract contradictions."""
    return " ".join(verdict.contradictions)


__all__ = ["verification_feedback", "verify_candidate"]
