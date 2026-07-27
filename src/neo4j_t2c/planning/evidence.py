"""Bounded evidence acquisition for partial graph programs."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from neo4j_t2c.planning.models import (
    EvidenceAction,
    EvidenceActionType,
    EvidenceObservation,
    GraphProgramState,
)
from neo4j_t2c.schema import parse_schema_text


def _quote_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def _hypothesis_gain(alternative_count: int) -> float:
    """Uniform-prior information available from testing one alternative."""
    return max(1.0, math.log2(max(1, alternative_count) + 1))


def propose_evidence_actions(
    state: GraphProgramState,
    runtime_schema: str,
) -> tuple[EvidenceAction, ...]:
    """Compile grounded hypotheses into safe existence probes."""
    schema = parse_schema_text(runtime_schema)
    actions: list[EvidenceAction] = []

    target_labels = [
        label
        for label in state.target_label_hypotheses
        if label in schema.nodes
    ]
    target_gain = _hypothesis_gain(len(target_labels))
    for rank, label in enumerate(target_labels):
        quoted_label = _quote_identifier(label)
        actions.append(
            EvidenceAction(
                action_type=EvidenceActionType.VERIFY_TARGET_LABEL,
                hypothesis=label,
                cypher=(
                    f"MATCH (n:{quoted_label}) "
                    "RETURN 1 AS evidence LIMIT 1"
                ),
                resolves_slot="target_label",
                information_gain=target_gain,
                estimated_cost=1.0,
                prior_weight=1.0 / (rank + 1),
                alternative_count=max(1, len(target_labels)),
                target_label=label,
            )
        )

    relationship_types = set(state.relationship_hypotheses)
    target_set = set(target_labels)
    paths = [
        path
        for path in schema.paths
        if path.rel_type in relationship_types
        and (
            not target_set
            or path.start in target_set
            or path.end in target_set
        )
    ]
    relationship_gain = _hypothesis_gain(len(paths))
    for path in paths:
        start = _quote_identifier(path.start)
        relationship = _quote_identifier(path.rel_type)
        end = _quote_identifier(path.end)
        signature = f"(:{path.start})-[:{path.rel_type}]->(:{path.end})"
        actions.append(
            EvidenceAction(
                action_type=EvidenceActionType.VERIFY_RELATIONSHIP,
                hypothesis=signature,
                cypher=(
                    f"MATCH (a:{start})-[r:{relationship}]->(b:{end}) "
                    "RETURN 1 AS evidence LIMIT 1"
                ),
                resolves_slot="relationship",
                information_gain=relationship_gain,
                estimated_cost=2.0,
                alternative_count=max(1, len(paths)),
                start_label=path.start,
                relationship_type=path.rel_type,
                end_label=path.end,
            )
        )

    return tuple(actions)


def select_evidence_action(
    actions: Sequence[EvidenceAction],
) -> EvidenceAction | None:
    """Choose the highest expected information gain per unit cost."""
    if not actions:
        return None
    return min(
        actions,
        key=lambda action: (
            -action.utility,
            action.estimated_cost,
            action.action_type,
            action.hypothesis,
        ),
    )


def execute_evidence_action(
    graph: Any,
    action: EvidenceAction,
) -> EvidenceObservation:
    """Execute one precompiled read-only existence probe."""
    try:
        rows = graph.query(action.cypher)
        return EvidenceObservation(
            action=action,
            supported=bool(rows),
        )
    except Exception as exc:
        return EvidenceObservation(
            action=action,
            supported=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def apply_evidence_observation(
    state: GraphProgramState,
    observation: EvidenceObservation,
) -> GraphProgramState:
    """Update hypotheses without turning one positive probe into false certainty."""
    action = observation.action
    targets = list(state.target_label_hypotheses)
    relationships = list(state.relationship_hypotheses)
    rejected = list(state.rejected_hypotheses)

    if not observation.supported:
        if action.action_type == EvidenceActionType.VERIFY_TARGET_LABEL:
            targets = [
                label
                for label in targets
                if label != action.target_label
            ]
        elif action.action_type == EvidenceActionType.VERIFY_RELATIONSHIP:
            rejected.append(action.hypothesis)

    unresolved = list(state.unresolved_slots)
    if observation.supported:
        if (
            action.resolves_slot == "target_label"
            and len(targets) == 1
        ) or (
            action.resolves_slot == "relationship"
            and action.alternative_count == 1
        ):
            unresolved = [
                slot for slot in unresolved if slot != action.resolves_slot
            ]

    return state.model_copy(
        update={
            "target_label_hypotheses": tuple(targets),
            "relationship_hypotheses": tuple(relationships),
            "unresolved_slots": tuple(unresolved),
            "rejected_hypotheses": tuple(dict.fromkeys(rejected)),
        }
    )
