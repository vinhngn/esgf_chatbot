from __future__ import annotations

import json
from types import SimpleNamespace

from neo4j_t2c.planning import (
    QueryOperation,
    SemanticContract,
    assess_uncertainty,
    build_initial_graph_program,
    run_adaptive_search,
)

SCHEMA = """
Node properties:
Lake {name: STRING}
Country {name: STRING}
Relationship properties:

The relationships:
(:Lake)-[:locatedIn]->(:Country)
"""


class SearchModel:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, input, config=None, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            content=json.dumps(
                {
                    "labels": [
                        {
                            "concept": "lake",
                            "label": "Lake",
                            "confidence": 0.96,
                        }
                    ],
                    "properties": [
                        {
                            "concept": "name",
                            "label": "Lake",
                            "property_name": "name",
                            "confidence": 0.95,
                        }
                    ],
                    "edges": [
                        {
                            "relation": "within",
                            "start_label": "Lake",
                            "relationship_type": "locatedIn",
                            "end_label": "Country",
                            "confidence": 0.94,
                        }
                    ],
                    "ambiguities": [],
                    "confidence": 0.94,
                }
            )
        )


class SearchGraph:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, cypher: str, parameters=None) -> list[dict]:
        self.queries.append(cypher)
        return [{"evidence": 1}]


def test_search_maps_missing_schema_then_spends_only_available_budget() -> None:
    contract = SemanticContract(
        target_concepts=("lake",),
        operation=QueryOperation.RETRIEVE,
        relations=("within country",),
        projections=("lake name",),
        confidence=0.35,
    )
    profile_context = {
        "selected_examples": [],
        "query_plan_contract": {},
        "reranking": {
            "applied": True,
            "selected_row": None,
            "confidence": 0.4,
        },
    }
    state = build_initial_graph_program(
        contract,
        profile_context,
    )
    uncertainty = assess_uncertainty(state, profile_context)
    graph = SearchGraph()
    model = SearchModel()

    outcome = run_adaptive_search(
        state=state,
        uncertainty=uncertainty,
        profile_context=profile_context,
        runtime_schema=SCHEMA,
        graph=graph,
        model=model,
    )

    assert model.calls == 1
    assert outcome.mapping is not None
    assert outcome.final_state.target_label_hypotheses == ("Lake",)
    assert outcome.final_state.relationship_hypotheses == ("locatedIn",)
    assert outcome.final_state.projection_hypotheses == ("Lake.name",)
    assert len(outcome.observations) == 2
    assert len(graph.queries) == 2
    assert all(query.endswith("LIMIT 1") for query in graph.queries)
    assert "Label :Lake contains data." in outcome.evidence_context
    assert (
        "(:Lake)-[:locatedIn]->(:Country) contains data."
        in outcome.evidence_context
    )
    assert "=== VERIFIED NEO4J QUERY IR ===" in outcome.evidence_context
    assert '"types":["locatedIn"]' in outcome.evidence_context
    assert '"property_name":"name"' in outcome.evidence_context
