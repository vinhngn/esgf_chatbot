from __future__ import annotations

from neo4j_t2c.planning import (
    EvidenceActionType,
    QueryOperation,
    SemanticContract,
    apply_evidence_observation,
    build_initial_graph_program,
    execute_evidence_action,
    propose_evidence_actions,
    select_evidence_action,
)

SCHEMA = """
Node properties:
Lake {name: STRING}
Country {name: STRING}
Relationship properties:

The relationships:
(:Lake)-[:locatedIn]->(:Country)
"""


class ProbeGraph:
    def __init__(self, *, supported: bool = True) -> None:
        self.supported = supported
        self.queries: list[str] = []

    def query(self, cypher: str, parameters=None) -> list[dict]:
        self.queries.append(cypher)
        return [{"evidence": 1}] if self.supported else []


def _state():
    contract = SemanticContract(
        target_concepts=("lake",),
        operation=QueryOperation.RETRIEVE,
        relations=("located within",),
        projections=("lake name",),
        confidence=0.5,
    )
    return build_initial_graph_program(
        contract,
        {
            "query_plan_contract": {
                "target_label": "Lake",
                "candidate_labels": ["Lake", "Country"],
                "required_relationships": ["locatedIn"],
                "return_contract": {"items": []},
            }
        },
    )


def test_controller_compiles_only_schema_valid_bounded_probes() -> None:
    actions = propose_evidence_actions(_state(), SCHEMA)

    assert len(actions) == 3
    assert {
        action.action_type for action in actions
    } == {
        EvidenceActionType.VERIFY_TARGET_LABEL,
        EvidenceActionType.VERIFY_RELATIONSHIP,
    }
    assert all(action.cypher.endswith("RETURN 1 AS evidence LIMIT 1") for action in actions)
    relationship = next(
        action
        for action in actions
        if action.action_type == EvidenceActionType.VERIFY_RELATIONSHIP
    )
    assert (
        relationship.cypher
        == "MATCH (a:`Lake`)-[r:`locatedIn`]->(b:`Country`) "
        "RETURN 1 AS evidence LIMIT 1"
    )


def test_controller_prefers_lower_cost_target_probe() -> None:
    selected = select_evidence_action(
        propose_evidence_actions(_state(), SCHEMA)
    )

    assert selected is not None
    assert selected.action_type == EvidenceActionType.VERIFY_TARGET_LABEL
    assert selected.hypothesis == "Lake"


def test_negative_observation_removes_unsupported_hypothesis() -> None:
    state = _state()
    action = next(
        action
        for action in propose_evidence_actions(state, SCHEMA)
        if action.action_type == EvidenceActionType.VERIFY_TARGET_LABEL
        and action.hypothesis == "Country"
    )
    graph = ProbeGraph(supported=False)

    observation = execute_evidence_action(graph, action)
    updated = apply_evidence_observation(state, observation)

    assert graph.queries == [action.cypher]
    assert observation.supported is False
    assert updated.target_label_hypotheses == ("Lake",)


def test_negative_edge_probe_rejects_only_that_endpoint_hypothesis() -> None:
    state = _state()
    action = next(
        action
        for action in propose_evidence_actions(state, SCHEMA)
        if action.action_type == EvidenceActionType.VERIFY_RELATIONSHIP
    )

    observation = execute_evidence_action(
        ProbeGraph(supported=False),
        action,
    )
    updated = apply_evidence_observation(state, observation)

    assert updated.relationship_hypotheses == ("locatedIn",)
    assert updated.rejected_hypotheses == (
        "(:Lake)-[:locatedIn]->(:Country)",
    )
