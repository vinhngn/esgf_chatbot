"""Adaptive schema mapping and evidence acquisition loop."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from neo4j_t2c.planning.evidence import (
    apply_evidence_observation,
    execute_evidence_action,
    propose_evidence_actions,
    select_evidence_action,
)
from neo4j_t2c.planning.models import (
    EvidenceActionType,
    EvidenceObservation,
    GraphProgramState,
    SchemaMapping,
    UncertaintyReport,
)
from neo4j_t2c.planning.schema_mapping import (
    apply_schema_mapping,
    infer_schema_mapping,
)
from neo4j_t2c.planning.uncertainty import assess_uncertainty
from neo4j_t2c.ports import ChatModel

_SCHEMA_MAPPING_SLOTS = {
    "target_label",
    "relationship",
    "projection",
    "metric",
}


class AdaptiveSearchOutcome(BaseModel):
    """Auditable result of bounded test-time graph search."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    initial_state: GraphProgramState
    final_state: GraphProgramState
    uncertainty: UncertaintyReport
    mapping: SchemaMapping | None = None
    mapping_error: str | None = None
    observations: tuple[EvidenceObservation, ...] = ()
    evidence_context: str = ""


def _needs_schema_mapping(state: GraphProgramState) -> bool:
    return bool(_SCHEMA_MAPPING_SLOTS & set(state.unresolved_slots))


def format_supported_evidence(
    observations: tuple[EvidenceObservation, ...],
) -> str:
    """Expose only database-supported facts to the Cypher renderer."""
    supported = [
        observation
        for observation in observations
        if observation.supported and observation.error is None
    ]
    if not supported:
        return ""
    lines = [
        "=== DATA-BACKED GRAPH EVIDENCE ===",
        "These existence probes returned at least one row in Neo4j:",
    ]
    for observation in supported:
        action = observation.action
        if action.action_type == EvidenceActionType.VERIFY_TARGET_LABEL:
            lines.append(f"- Label :{action.target_label} contains data.")
        elif action.action_type == EvidenceActionType.VERIFY_RELATIONSHIP:
            lines.append(
                "- Pattern "
                f"(:{action.start_label})-[:{action.relationship_type}]->"
                f"(:{action.end_label}) contains data."
            )
    return "\n".join(lines)


def format_query_ir(state: GraphProgramState) -> str:
    """Render the verified typed IR without adding schema assumptions."""
    if state.query_ir is None:
        return ""
    return "\n".join(
        [
            "=== VERIFIED NEO4J QUERY IR ===",
            json.dumps(
                state.query_ir.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        ]
    )


def run_adaptive_search(
    *,
    state: GraphProgramState,
    uncertainty: UncertaintyReport,
    profile_context: dict[str, Any],
    runtime_schema: str,
    graph: Any,
    model: ChatModel,
) -> AdaptiveSearchOutcome:
    """Spend the allocated budget on mapping and bounded live observations."""
    if not uncertainty.should_expand:
        return AdaptiveSearchOutcome(
            initial_state=state,
            final_state=state,
            uncertainty=uncertainty,
        )

    current = state
    mapping = None
    mapping_error = None
    mapping_cost = 0
    if _needs_schema_mapping(current):
        mapping_cost = 1
        try:
            mapping = infer_schema_mapping(
                current.semantic_contract,
                runtime_schema,
                model,
                planner_profile=profile_context.get("planner_profile"),
            )
            current = apply_schema_mapping(current, mapping)
        except Exception as exc:
            mapping_error = f"{type(exc).__name__}: {exc}"

    updated_uncertainty = assess_uncertainty(
        current,
        profile_context,
        minimum_budget=1,
        maximum_budget=max(1, uncertainty.search_budget),
    )
    probe_budget = max(0, uncertainty.search_budget - mapping_cost)
    observations: list[EvidenceObservation] = []
    attempted: set[str] = set()

    for _ in range(probe_budget):
        actions = [
            action
            for action in propose_evidence_actions(
                current,
                runtime_schema,
            )
            if action.cypher not in attempted
            and action.hypothesis not in current.rejected_hypotheses
        ]
        selected = select_evidence_action(actions)
        if selected is None:
            break
        attempted.add(selected.cypher)
        observation = execute_evidence_action(graph, selected)
        observations.append(observation)
        current = apply_evidence_observation(current, observation)

    observation_tuple = tuple(observations)
    evidence_context = "\n\n".join(
        block
        for block in (
            format_query_ir(current),
            format_supported_evidence(observation_tuple),
        )
        if block
    )
    return AdaptiveSearchOutcome(
        initial_state=state,
        final_state=current,
        uncertainty=updated_uncertainty,
        mapping=mapping,
        mapping_error=mapping_error,
        observations=observation_tuple,
        evidence_context=evidence_context,
    )


__all__ = [
    "AdaptiveSearchOutcome",
    "format_query_ir",
    "format_supported_evidence",
    "run_adaptive_search",
]
