from __future__ import annotations

from neo4j_t2c.planning import (
    QueryOperation,
    SemanticContract,
    SortDirection,
    verification_feedback,
    verify_candidate,
)


class TopKGraph:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def query(self, cypher: str, parameters=None) -> list[dict]:
        self.queries.append(cypher)
        return self.rows


def test_contract_rejects_ranking_query_without_ordering() -> None:
    contract = SemanticContract(
        target_concepts=("movie",),
        operation=QueryOperation.RANK,
        metric="revenue",
        projections=("movie title",),
        order=SortDirection.DESCENDING,
        limit=3,
        confidence=0.9,
    )

    verdict = verify_candidate(
        contract=contract,
        cypher="MATCH (m:Movie) RETURN m.title LIMIT 3",
        rows=[{"m.title": "A"}],
    )

    assert verdict.passed is False
    assert any("ORDER BY" in item for item in verdict.contradictions)
    assert "descending order" in verification_feedback(verdict)


def test_contract_rejects_wrong_operation_and_limit() -> None:
    contract = SemanticContract(
        target_concepts=("movie",),
        operation=QueryOperation.COUNT,
        limit=5,
        confidence=0.9,
    )

    verdict = verify_candidate(
        contract=contract,
        cypher="MATCH (m:Movie) RETURN m.title LIMIT 3",
        rows=[{"m.title": "A"}],
    )

    assert verdict.passed is False
    assert any("count aggregation" in item for item in verdict.contradictions)
    assert any("LIMIT 5" in item for item in verdict.contradictions)


def test_top_k_probe_checks_prefix_with_limit_plus_one() -> None:
    contract = SemanticContract(
        target_concepts=("movie",),
        operation=QueryOperation.RANK,
        metric="revenue",
        projections=("movie title",),
        order=SortDirection.DESCENDING,
        limit=3,
        confidence=0.9,
    )
    original_rows = [
        {"title": "A"},
        {"title": "B"},
        {"title": "C"},
    ]
    graph = TopKGraph(original_rows + [{"title": "D"}])

    verdict = verify_candidate(
        contract=contract,
        cypher=(
            "MATCH (m:Movie) RETURN m.title AS title "
            "ORDER BY m.revenue DESC LIMIT 3"
        ),
        rows=original_rows,
        graph=graph,
    )

    assert verdict.passed is True
    assert verdict.warnings == ()
    assert len(verdict.probes) == 1
    assert verdict.probes[0].passed is True
    assert graph.queries == [
        "MATCH (m:Movie) RETURN m.title AS title "
        "ORDER BY m.revenue DESC LIMIT 4"
    ]


def test_unstable_top_k_prefix_is_warning_not_false_semantic_proof() -> None:
    contract = SemanticContract(
        target_concepts=("movie",),
        operation=QueryOperation.RANK,
        metric="revenue",
        order=SortDirection.DESCENDING,
        limit=2,
        confidence=0.9,
    )
    graph = TopKGraph([{"title": "B"}, {"title": "A"}])

    verdict = verify_candidate(
        contract=contract,
        cypher=(
            "MATCH (m:Movie) RETURN m.title AS title "
            "ORDER BY m.revenue DESC LIMIT 2"
        ),
        rows=[{"title": "A"}, {"title": "B"}],
        graph=graph,
    )

    assert verdict.passed is True
    assert verdict.probes[0].passed is False
    assert verdict.warnings
