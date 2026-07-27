"""LLM-assisted semantic-to-schema mapping with deterministic validation."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from neo4j_t2c.planning.models import (
    GraphProgramState,
    IRDirection,
    Neo4jQueryIR,
    QueryNodeIR,
    QueryProjectionIR,
    QueryRelationshipIR,
    SchemaMapping,
    SemanticContract,
)
from neo4j_t2c.ports import ChatModel
from neo4j_t2c.schema import parse_schema_text

_SYSTEM_PROMPT = """Map a schema-independent semantic contract to the supplied
Neo4j schema catalog. Return hypotheses, not Cypher.

Rules:
1. Copy label, property, and relationship names exactly from the catalog.
2. Preserve relationship direction exactly as declared.
3. Map only concepts required by the semantic contract.
4. Return multiple hypotheses when the mapping is genuinely ambiguous.
5. Do not invent schema symbols or infer facts about stored rows.

Return one JSON object only:
{
  "labels": [
    {"concept": "semantic concept", "label": "ExactLabel", "confidence": 0.0}
  ],
  "properties": [
    {"concept": "semantic attribute", "label": "ExactLabel",
     "property_name": "exactProperty", "confidence": 0.0}
  ],
  "edges": [
    {"relation": "semantic relation", "start_label": "ExactStart",
     "relationship_type": "EXACT_TYPE", "end_label": "ExactEnd",
     "confidence": 0.0}
  ],
  "ambiguities": [],
  "confidence": 0.0
}"""


def _json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(
        r"```(?:json)?\s*",
        "",
        text or "",
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Schema mapper did not return a JSON object")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Schema mapper returned a non-object JSON value")
    return value


def schema_catalog(runtime_schema: str) -> dict[str, Any]:
    """Build a compact authoritative catalog for the mapping model."""
    schema = parse_schema_text(runtime_schema)
    return {
        "labels": [
            {
                "name": label,
                "properties": sorted(node.properties),
            }
            for label, node in sorted(schema.nodes.items())
        ],
        "relationships": [
            {
                "start": path.start,
                "type": path.rel_type,
                "end": path.end,
            }
            for path in sorted(
                schema.paths,
                key=lambda item: (
                    item.start,
                    item.rel_type,
                    item.end,
                ),
            )
        ],
    }


def _validated_mapping(
    proposed: SchemaMapping,
    runtime_schema: str,
    planner_profile: dict[str, Any] | None = None,
) -> SchemaMapping:
    schema = parse_schema_text(runtime_schema)
    graph_statistics = (
        planner_profile.get("graph_statistics") or {}
        if isinstance(planner_profile, dict)
        else {}
    )
    zero_labels = {
        str(label)
        for label, count in (
            graph_statistics.get("node_counts") or {}
        ).items()
        if count == 0
    }
    zero_edges = {
        (
            str(step.get("from") or ""),
            str(step.get("type") or ""),
            str(step.get("to") or ""),
        )
        for step in graph_statistics.get("pattern_steps") or []
        if step.get("estimated_count") == 0
    }
    declared_edges = {
        (path.start, path.rel_type, path.end)
        for path in schema.paths
    }
    labels = tuple(
        hypothesis
        for hypothesis in proposed.labels
        if hypothesis.label in schema.nodes
        and hypothesis.label not in zero_labels
    )
    properties = tuple(
        hypothesis
        for hypothesis in proposed.properties
        if hypothesis.label in schema.nodes
        and hypothesis.label not in zero_labels
        and hypothesis.property_name
        in schema.nodes[hypothesis.label].properties
    )
    edges = tuple(
        hypothesis
        for hypothesis in proposed.edges
        if (
            hypothesis.start_label,
            hypothesis.relationship_type,
            hypothesis.end_label,
        )
        in declared_edges
        and (
            hypothesis.start_label,
            hypothesis.relationship_type,
            hypothesis.end_label,
        )
        not in zero_edges
    )
    return proposed.model_copy(
        update={
            "labels": labels,
            "properties": properties,
            "edges": edges,
        }
    )


def infer_schema_mapping(
    contract: SemanticContract,
    runtime_schema: str,
    model: ChatModel,
    *,
    planner_profile: dict[str, Any] | None = None,
) -> SchemaMapping:
    """Ask the LLM for language mapping, then enforce runtime schema."""
    payload = {
        "semantic_contract": contract.model_dump(mode="json"),
        "schema_catalog": schema_catalog(runtime_schema),
    }
    response = model.invoke(
        [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(
                content=json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ),
        ]
    )
    proposed = SchemaMapping.model_validate(
        _json_object(str(response.content))
    )
    return _validated_mapping(
        proposed,
        runtime_schema,
        planner_profile,
    )


def apply_schema_mapping(
    state: GraphProgramState,
    mapping: SchemaMapping,
) -> GraphProgramState:
    """Merge schema-valid hypotheses into the partial graph program."""
    labels = tuple(
        dict.fromkeys(
            hypothesis.label
            for hypothesis in sorted(
                mapping.labels,
                key=lambda item: -item.confidence,
            )
        )
    )
    relationships = tuple(
        dict.fromkeys(
            hypothesis.relationship_type
            for hypothesis in sorted(
                mapping.edges,
                key=lambda item: -item.confidence,
            )
        )
    )
    properties = tuple(
        dict.fromkeys(
            f"{hypothesis.label}.{hypothesis.property_name}"
            for hypothesis in sorted(
                mapping.properties,
                key=lambda item: -item.confidence,
            )
        )
    )

    unresolved = list(state.unresolved_slots)
    if labels:
        unresolved = [
            slot for slot in unresolved if slot != "target_label"
        ]
    if relationships:
        unresolved = [
            slot for slot in unresolved if slot != "relationship"
        ]
    if properties:
        unresolved = [
            slot
            for slot in unresolved
            if slot not in {"projection", "metric"}
        ]

    query_ir = _compile_query_ir(
        state.semantic_contract,
        mapping,
    )
    return state.model_copy(
        update={
            "target_label_hypotheses": labels
            or state.target_label_hypotheses,
            "relationship_hypotheses": relationships
            or state.relationship_hypotheses,
            "projection_hypotheses": properties
            or state.projection_hypotheses,
            "unresolved_slots": tuple(unresolved),
            "query_ir": query_ir,
            "schema_mapping_confidence": mapping.confidence,
        }
    )


def _compile_query_ir(
    contract: SemanticContract,
    mapping: SchemaMapping,
) -> Neo4jQueryIR | None:
    ordered_labels = list(
        dict.fromkeys(
            hypothesis.label
            for hypothesis in sorted(
                mapping.labels,
                key=lambda item: -item.confidence,
            )
        )
    )
    for edge in sorted(mapping.edges, key=lambda item: -item.confidence):
        for label in (edge.start_label, edge.end_label):
            if label not in ordered_labels:
                ordered_labels.append(label)
    if not ordered_labels:
        return None

    variables = {
        label: f"n{index}"
        for index, label in enumerate(ordered_labels)
    }
    nodes = tuple(
        QueryNodeIR(
            variable=variables[label],
            labels=(label,),
            concepts=tuple(
                dict.fromkeys(
                    hypothesis.concept
                    for hypothesis in mapping.labels
                    if hypothesis.label == label
                )
            ),
        )
        for label in ordered_labels
    )
    relationships = tuple(
        QueryRelationshipIR(
            variable=f"r{index}",
            start_variable=variables[edge.start_label],
            end_variable=variables[edge.end_label],
            types=(edge.relationship_type,),
            direction=IRDirection.OUTGOING,
            semantic_relation=edge.relation,
        )
        for index, edge in enumerate(
            sorted(mapping.edges, key=lambda item: -item.confidence)
        )
    )
    projections = tuple(
        QueryProjectionIR(
            variable=variables[property_hypothesis.label],
            property_name=property_hypothesis.property_name,
            concept=property_hypothesis.concept,
        )
        for property_hypothesis in sorted(
            mapping.properties,
            key=lambda item: -item.confidence,
        )
        if property_hypothesis.label in variables
    )
    return Neo4jQueryIR(
        operation=contract.operation,
        nodes=nodes,
        relationships=relationships,
        projections=projections,
        constraints=contract.constraints,
        metric=contract.metric,
        grouping=contract.grouping,
        order=contract.order,
        limit=contract.limit,
        ambiguities=tuple(
            dict.fromkeys(contract.ambiguities + mapping.ambiguities)
        ),
    )


def render_schema_slice(
    runtime_schema: str,
    state: GraphProgramState,
) -> str:
    """Render only the schema subgraph supported by current hypotheses."""
    schema = parse_schema_text(runtime_schema)
    labels = set(state.target_label_hypotheses)
    relationship_types = set(state.relationship_hypotheses)
    paths = [
        path
        for path in schema.paths
        if path.rel_type in relationship_types
        and (
            not labels
            or path.start in labels
            or path.end in labels
        )
    ]
    for path in paths:
        labels.add(path.start)
        labels.add(path.end)
    labels &= set(schema.nodes)
    if not labels and not paths:
        return ""

    lines = ["Node properties:"]
    for label in sorted(labels):
        properties = schema.nodes[label].properties
        body = ", ".join(
            f"{name}: {property_type}"
            for name, property_type in sorted(properties.items())
        )
        lines.append(f"{label} {{{body}}}")

    lines.append("Relationship properties:")
    for relationship_type in sorted(
        {path.rel_type for path in paths}
    ):
        properties = schema.relationships[
            relationship_type
        ].properties
        body = ", ".join(
            f"{name}: {property_type}"
            for name, property_type in sorted(properties.items())
        )
        lines.append(f"{relationship_type} {{{body}}}")

    lines.append("The relationships:")
    lines.extend(
        f"(:{path.start})-[:{path.rel_type}]->(:{path.end})"
        for path in paths
    )
    return "\n".join(lines)


__all__ = [
    "apply_schema_mapping",
    "infer_schema_mapping",
    "render_schema_slice",
    "schema_catalog",
]
