"""Adaptive compute allocation from semantic and retrieval uncertainty."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from neo4j_t2c.planning.models import (
    GraphProgramState,
    SemanticContract,
    UncertaintyReport,
)


def _normalized_entropy(scores: Sequence[float]) -> float:
    """Return scale-invariant Shannon entropy in [0, 1]."""
    positive = [max(0.0, float(score)) for score in scores]
    if not positive:
        return 1.0
    if len(positive) == 1:
        return 0.0
    maximum = max(positive)
    probabilities = [math.exp(score - maximum) for score in positive]
    total = sum(probabilities)
    probabilities = [value / total for value in probabilities]
    entropy = -sum(
        probability * math.log(probability)
        for probability in probabilities
        if probability > 0
    )
    return min(1.0, entropy / math.log(len(probabilities)))


def build_initial_graph_program(
    contract: SemanticContract,
    profile_context: Mapping[str, Any],
) -> GraphProgramState:
    """Convert retrieved evidence into hypotheses, never final schema choices."""
    plan = profile_context.get("query_plan_contract") or {}
    target_labels = tuple(
        str(label).strip()
        for label in (
            [plan.get("target_label")]
            + list(plan.get("candidate_labels") or [])
        )
        if str(label or "").strip()
    )
    relationships = tuple(
        str(name).strip()
        for name in plan.get("required_relationships") or []
        if str(name).strip()
    )
    return_contract = plan.get("return_contract") or {}
    projections = tuple(
        str(item).strip()
        for item in return_contract.get("items") or []
        if str(item).strip()
    )

    unresolved = []
    if contract.target_concepts and not target_labels:
        unresolved.append("target_label")
    if contract.relations and not relationships:
        unresolved.append("relationship")
    if contract.projections and not projections:
        unresolved.append("projection")
    if contract.metric and not projections:
        unresolved.append("metric")
    if contract.constraints:
        unresolved.append("constraint_grounding")
    if contract.ambiguities:
        unresolved.append("semantic_ambiguity")

    return GraphProgramState(
        semantic_contract=contract,
        target_label_hypotheses=tuple(dict.fromkeys(target_labels)),
        relationship_hypotheses=tuple(dict.fromkeys(relationships)),
        projection_hypotheses=tuple(dict.fromkeys(projections)),
        unresolved_slots=tuple(dict.fromkeys(unresolved)),
    )


def assess_uncertainty(
    state: GraphProgramState,
    profile_context: Mapping[str, Any],
    *,
    minimum_budget: int = 1,
    maximum_budget: int = 5,
    expansion_threshold: float = 0.45,
) -> UncertaintyReport:
    """Allocate search effort using conservative, domain-neutral signals."""
    if minimum_budget < 1 or maximum_budget < minimum_budget:
        raise ValueError("Invalid adaptive search budget range")

    examples = profile_context.get("selected_examples") or []
    retrieval_entropy = _normalized_entropy(
        [float(example.get("score") or 0.0) for example in examples]
    )
    semantic_uncertainty = 1.0 - state.semantic_contract.confidence
    slot_count = max(
        1,
        len(state.semantic_contract.target_concepts)
        + len(state.semantic_contract.projections)
        + len(state.semantic_contract.relations)
        + len(state.semantic_contract.constraints)
        + (1 if state.semantic_contract.metric else 0),
    )
    unresolved_ratio = min(1.0, len(state.unresolved_slots) / slot_count)

    reranking = profile_context.get("reranking") or {}
    if reranking.get("applied"):
        reranker_uncertainty = 1.0 - float(reranking.get("confidence") or 0.0)
        if reranking.get("selected_row") is None:
            reranker_uncertainty = 1.0
    else:
        reranker_uncertainty = 0.0

    signals = {
        "semantic": round(semantic_uncertainty, 6),
        "retrieval_entropy": round(retrieval_entropy, 6),
        "unresolved_slots": round(unresolved_ratio, 6),
        "reranker": round(reranker_uncertainty, 6),
    }
    if state.schema_mapping_confidence is not None:
        signals["schema_mapping"] = round(
            1.0 - state.schema_mapping_confidence,
            6,
        )
    score = max(signals.values())
    budget_span = maximum_budget - minimum_budget
    should_expand = score >= expansion_threshold
    search_budget = (
        minimum_budget + max(1, math.ceil(score * budget_span))
        if should_expand and budget_span
        else minimum_budget
    )
    reasons = tuple(
        name
        for name, value in signals.items()
        if value >= expansion_threshold
    )
    return UncertaintyReport(
        score=round(score, 6),
        search_budget=search_budget,
        should_expand=should_expand,
        signals=signals,
        reasons=reasons,
    )
