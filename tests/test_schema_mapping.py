from __future__ import annotations

import json
from types import SimpleNamespace

from neo4j_t2c.planning import (
    QueryOperation,
    SemanticContract,
    apply_schema_mapping,
    build_initial_graph_program,
    infer_schema_mapping,
    render_schema_slice,
    schema_catalog,
)

SCHEMA = """
Node properties:
Lake {name: STRING, area: FLOAT}
Country {name: STRING}
Relationship properties:

The relationships:
(:Lake)-[:locatedIn]->(:Country)
"""


class MappingModel:
    def invoke(self, input, config=None, **kwargs):
        return SimpleNamespace(
            content=json.dumps(
                {
                    "labels": [
                        {
                            "concept": "lake",
                            "label": "Lake",
                            "confidence": 0.98,
                        },
                        {
                            "concept": "place",
                            "label": "Invented",
                            "confidence": 0.9,
                        },
                    ],
                    "properties": [
                        {
                            "concept": "lake name",
                            "label": "Lake",
                            "property_name": "name",
                            "confidence": 0.97,
                        },
                        {
                            "concept": "population",
                            "label": "Lake",
                            "property_name": "population",
                            "confidence": 0.8,
                        },
                    ],
                    "edges": [
                        {
                            "relation": "within",
                            "start_label": "Lake",
                            "relationship_type": "locatedIn",
                            "end_label": "Country",
                            "confidence": 0.96,
                        },
                        {
                            "relation": "within",
                            "start_label": "Country",
                            "relationship_type": "locatedIn",
                            "end_label": "Lake",
                            "confidence": 0.8,
                        },
                    ],
                    "ambiguities": [],
                    "confidence": 0.94,
                }
            )
        )


def test_catalog_preserves_runtime_names_and_directions() -> None:
    catalog = schema_catalog(SCHEMA)

    assert catalog["labels"] == [
        {"name": "Country", "properties": ["name"]},
        {"name": "Lake", "properties": ["area", "name"]},
    ]
    assert catalog["relationships"] == [
        {"start": "Lake", "type": "locatedIn", "end": "Country"}
    ]


def test_mapper_drops_invented_symbols_and_reversed_edges() -> None:
    contract = SemanticContract(
        target_concepts=("lake",),
        operation=QueryOperation.RETRIEVE,
        relations=("within country",),
        projections=("lake name",),
        confidence=0.8,
    )

    mapping = infer_schema_mapping(contract, SCHEMA, MappingModel())

    assert [item.label for item in mapping.labels] == ["Lake"]
    assert [
        (item.label, item.property_name)
        for item in mapping.properties
    ] == [("Lake", "name")]
    assert [
        (
            item.start_label,
            item.relationship_type,
            item.end_label,
        )
        for item in mapping.edges
    ] == [("Lake", "locatedIn", "Country")]


def test_mapper_drops_schema_valid_hypotheses_contradicted_by_graph_counts() -> None:
    contract = SemanticContract(
        target_concepts=("lake",),
        operation=QueryOperation.RETRIEVE,
        relations=("within country",),
        projections=("lake name",),
        confidence=0.8,
    )
    planner_profile = {
        "graph_statistics": {
            "node_counts": {"Lake": 0, "Country": 10},
            "pattern_steps": [
                {
                    "from": "Lake",
                    "type": "locatedIn",
                    "to": "Country",
                    "estimated_count": 0,
                    "count_kind": "upper_bound",
                }
            ],
        }
    }

    mapping = infer_schema_mapping(
        contract,
        SCHEMA,
        MappingModel(),
        planner_profile=planner_profile,
    )

    assert mapping.labels == ()
    assert mapping.edges == ()
    assert [
        (item.label, item.property_name)
        for item in mapping.properties
    ] == []


def test_mapper_does_not_treat_positive_upper_bound_as_proof_or_reweighting() -> None:
    contract = SemanticContract(
        target_concepts=("lake",),
        operation=QueryOperation.RETRIEVE,
        relations=("within country",),
        projections=("lake name",),
        confidence=0.8,
    )
    planner_profile = {
        "graph_statistics": {
            "node_counts": {"Lake": 5, "Country": 10},
            "pattern_steps": [
                {
                    "from": "Lake",
                    "type": "locatedIn",
                    "to": "Country",
                    "estimated_count": 3,
                    "count_kind": "upper_bound",
                }
            ],
        }
    }

    mapping = infer_schema_mapping(
        contract,
        SCHEMA,
        MappingModel(),
        planner_profile=planner_profile,
    )

    assert [item.label for item in mapping.labels] == ["Lake"]
    assert [item.relationship_type for item in mapping.edges] == [
        "locatedIn"
    ]
    assert mapping.confidence == 0.94


def test_valid_mapping_populates_partial_graph_program() -> None:
    contract = SemanticContract(
        target_concepts=("lake",),
        operation=QueryOperation.RETRIEVE,
        relations=("within country",),
        projections=("lake name",),
        confidence=0.8,
    )
    state = build_initial_graph_program(
        contract,
        {"query_plan_contract": {}},
    )

    mapped = apply_schema_mapping(
        state,
        infer_schema_mapping(contract, SCHEMA, MappingModel()),
    )

    assert mapped.target_label_hypotheses == ("Lake",)
    assert mapped.relationship_hypotheses == ("locatedIn",)
    assert mapped.projection_hypotheses == ("Lake.name",)
    assert mapped.schema_mapping_confidence == 0.94
    assert mapped.unresolved_slots == ()
    assert mapped.query_ir is not None
    assert [
        (node.variable, node.labels)
        for node in mapped.query_ir.nodes
    ] == [
        ("n0", ("Lake",)),
        ("n1", ("Country",)),
    ]
    assert [
        (
            edge.start_variable,
            edge.types,
            edge.end_variable,
            edge.direction,
        )
        for edge in mapped.query_ir.relationships
    ] == [
        ("n0", ("locatedIn",), "n1", "outgoing"),
    ]
    assert [
        (projection.variable, projection.property_name)
        for projection in mapped.query_ir.projections
    ] == [("n0", "name")]


def test_schema_slice_contains_only_mapped_subgraph() -> None:
    contract = SemanticContract(
        target_concepts=("lake",),
        operation=QueryOperation.RETRIEVE,
        relations=("within country",),
        projections=("lake name",),
        confidence=0.8,
    )
    state = build_initial_graph_program(
        contract,
        {"query_plan_contract": {}},
    )
    mapped = apply_schema_mapping(
        state,
        infer_schema_mapping(contract, SCHEMA, MappingModel()),
    )

    sliced = render_schema_slice(SCHEMA, mapped)

    assert "Lake {area: FLOAT, name: STRING}" in sliced
    assert "Country {name: STRING}" in sliced
    assert "(:Lake)-[:locatedIn]->(:Country)" in sliced
    assert "Invented" not in sliced
