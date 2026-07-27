from __future__ import annotations

from neo4j_t2c.planning import (
    QueryOperation,
    SemanticContract,
    assess_uncertainty,
    build_initial_graph_program,
)


def test_clear_contract_and_dominant_evidence_stop_after_one_candidate() -> None:
    contract = SemanticContract(
        target_concepts=("lake",),
        operation=QueryOperation.RETRIEVE,
        projections=("lake name",),
        confidence=0.95,
    )
    context = {
        "selected_examples": [
            {"score": 8.0},
            {"score": 1.0},
        ],
        "query_plan_contract": {
            "target_label": "Lake",
            "candidate_labels": ["Lake", "Country"],
            "required_relationships": ["locatedIn"],
            "return_contract": {"items": ["l.name"]},
        },
        "reranking": {
            "applied": True,
            "selected_row": 4,
            "confidence": 0.95,
        },
    }

    state = build_initial_graph_program(contract, context)
    report = assess_uncertainty(state, context)

    assert state.target_label_hypotheses == ("Lake", "Country")
    assert state.relationship_hypotheses == ("locatedIn",)
    assert state.unresolved_slots == ()
    assert report.should_expand is False
    assert report.search_budget == 1


def test_ambiguous_contract_allocates_more_search_without_domain_rules() -> None:
    contract = SemanticContract(
        target_concepts=("organization",),
        projections=("name",),
        ambiguities=("organization could denote two graph concepts",),
        confidence=0.35,
    )
    context = {
        "selected_examples": [
            {"score": 3.0},
            {"score": 3.0},
            {"score": 3.0},
        ],
        "query_plan_contract": {},
        "reranking": {
            "applied": True,
            "selected_row": None,
            "confidence": 0.4,
        },
    }

    state = build_initial_graph_program(contract, context)
    report = assess_uncertainty(state, context)

    assert set(state.unresolved_slots) == {
        "target_label",
        "projection",
        "semantic_ambiguity",
    }
    assert report.should_expand is True
    assert report.search_budget == 5
    assert "retrieval_entropy" in report.reasons
    assert "reranker" in report.reasons
