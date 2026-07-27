from __future__ import annotations

from neo4j_t2c.profiles.builders import neo4j as builder


def test_structural_properties_are_not_polluted_by_visual_index_descriptions(
    monkeypatch,
) -> None:
    def fake_query(session, cypher: str, **params):
        del session, params
        if "db.labels" in cypher:
            return [{"label": "Movie"}]
        if "count(n)" in cypher:
            return [{"count": 10}]
        raise AssertionError(f"Unexpected query: {cypher}")

    monkeypatch.setattr(builder, "_query", fake_query)
    monkeypatch.setattr(
        builder,
        "_schema_node_property_index",
        lambda session: {"Movie": ["plot", "runtime", "title"]},
    )

    labels = builder._collect_labels(
        object(),
        sample_limit=100,
        visual_label_props={"Movie": ["title,plot"]},
    )

    assert [prop["name"] for prop in labels[0]["properties"]] == [
        "plot",
        "runtime",
        "title",
    ]


def test_vector_discovery_distinguishes_unavailable_from_empty(monkeypatch) -> None:
    def fail_query(session, cypher: str, **params):
        del session, cypher, params
        raise PermissionError("SHOW INDEXES is not allowed")

    monkeypatch.setattr(builder, "_query", fail_query)

    indexes, discovery = builder._collect_vector_indexes(object())

    assert indexes == []
    assert discovery["status"] == "unavailable"
    assert discovery["count"] is None
    assert discovery["error_type"] == "PermissionError"


def test_access_path_discovery_keeps_all_index_types_and_vector_metadata(
    monkeypatch,
) -> None:
    rows = [
        {
            "name": "company_name",
            "type": "RANGE",
            "entityType": "NODE",
            "labelsOrTypes": ["Company"],
            "properties": ["name"],
            "state": "ONLINE",
            "populationPercent": 100.0,
            "indexProvider": "range-1.0",
            "owningConstraint": "company_name_key",
            "options": {},
        },
        {
            "name": "company_embedding",
            "type": "VECTOR",
            "entityType": "NODE",
            "labelsOrTypes": ["Company"],
            "properties": ["embedding"],
            "state": "ONLINE",
            "populationPercent": 100.0,
            "indexProvider": "vector-2.0",
            "owningConstraint": None,
            "options": {
                "indexConfig": {
                    "vector.dimensions": 768,
                    "vector.similarity_function": "cosine",
                }
            },
        },
    ]

    monkeypatch.setattr(builder, "_query", lambda *args, **kwargs: rows)

    indexes, discovery = builder._collect_indexes(object())

    assert [index["type"] for index in indexes] == ["RANGE", "VECTOR"]
    assert indexes[0]["owning_constraint"] == "company_name_key"
    assert indexes[1]["dimensions"] == 768
    assert indexes[1]["similarity_function"] == "cosine"
    assert discovery == {
        "status": "available",
        "count": 2,
        "error_type": None,
        "message": "",
    }


def test_constraint_discovery_preserves_identity_and_property_semantics(
    monkeypatch,
) -> None:
    rows = [
        {
            "name": "company_id_key",
            "type": "NODE_KEY",
            "entityType": "NODE",
            "labelsOrTypes": ["Company"],
            "properties": ["id"],
            "ownedIndex": "company_id_key",
            "propertyType": None,
        },
        {
            "name": "founded_type",
            "type": "NODE_PROPERTY_TYPE",
            "entityType": "NODE",
            "labelsOrTypes": ["Company"],
            "properties": ["founded"],
            "ownedIndex": None,
            "propertyType": "INTEGER",
        },
    ]
    monkeypatch.setattr(builder, "_query", lambda *args, **kwargs: rows)

    constraints, discovery = builder._collect_constraints(object())

    assert constraints[0]["type"] == "NODE_KEY"
    assert constraints[0]["labels_or_types"] == ["Company"]
    assert constraints[0]["properties"] == ["id"]
    assert constraints[1]["property_type"] == "INTEGER"
    assert discovery["status"] == "available"
    assert discovery["count"] == 2


def test_graph_counts_are_normalized_into_planner_statistics(monkeypatch) -> None:
    data = {
        "nodes": [
            {"count": 120},
            {"label": "Company", "count": 100},
            {"label": "Industry", "count": 20},
        ],
        "relationships": [
            {"count": 500},
            {"relationshipType": "IN_INDUSTRY", "count": 100},
            {
                "relationshipType": "IN_INDUSTRY",
                "startLabel": "Company",
                "count": 95,
            },
            {
                "relationshipType": "IN_INDUSTRY",
                "endLabel": "Industry",
                "count": 100,
            },
        ],
        "indexes": [
            {
                "labels": ["Company"],
                "properties": ["name"],
                "indexType": "RANGE",
                "totalSize": 80,
                "estimatedUniqueSize": 72,
            }
        ],
        "constraints": [],
    }
    monkeypatch.setattr(
        builder,
        "_query",
        lambda *args, **kwargs: [{"section": "GRAPH COUNTS", "data": data}],
    )

    statistics, discovery = builder._collect_graph_statistics(object())

    assert statistics["all_node_count"] == 120
    assert statistics["all_relationship_count"] == 500
    assert statistics["node_counts"]["Company"] == 100
    assert statistics["relationship_counts"]["IN_INDUSTRY"] == 100
    assert statistics["relationship_start_counts"]["Company|IN_INDUSTRY"] == 95
    assert statistics["relationship_end_counts"]["IN_INDUSTRY|Industry"] == 100
    assert statistics["index_statistics"][0]["estimated_unique_size"] == 72
    assert statistics["index_statistics"][0]["unique_value_selectivity"] == (
        1 / 72
    )
    assert discovery["status"] == "available"


def test_graph_statistics_fallback_keeps_known_label_counts_without_claiming_totals() -> None:
    labels = [
        {"label": "Company", "count": 100},
        {"label": "Industry", "count": 20},
    ]

    statistics = builder._fallback_graph_statistics(labels)

    assert statistics["source"] == "label_count_queries"
    assert statistics["all_node_count"] is None
    assert statistics["all_relationship_count"] is None
    assert statistics["node_counts"] == {
        "Company": 100,
        "Industry": 20,
    }
    assert statistics["relationship_counts"] == {}


def test_count_store_fallback_collects_directional_relationship_statistics(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_query(session, cypher: str, **params):
        del session, params
        compact = " ".join(cypher.split())
        calls.append(compact)
        if compact == "MATCH (n) RETURN count(n) AS count":
            return [{"count": 120}]
        if compact == "MATCH ()-[r]->() RETURN count(r) AS count":
            return [{"count": 100}]
        if "MATCH ()-[r:`IN_INDUSTRY`]->()" in compact:
            return [{"count": 100}]
        if "MATCH (:`Company`)-[r:`IN_INDUSTRY`]->()" in compact:
            return [{"count": 95}]
        if "MATCH ()-[r:`IN_INDUSTRY`]->(:`Industry`)" in compact:
            return [{"count": 100}]
        raise AssertionError(compact)

    monkeypatch.setattr(builder, "_query", fake_query)
    labels = [
        {"label": "Company", "count": 100},
        {"label": "Industry", "count": 20},
    ]
    relationships = [
        {
            "type": "IN_INDUSTRY",
            "patterns": [{"from": "Company", "to": "Industry"}],
        }
    ]

    statistics, discovery = builder._collect_count_store_statistics(
        object(),
        labels,
        relationships,
    )

    assert statistics["all_node_count"] == 120
    assert statistics["all_relationship_count"] == 100
    assert statistics["relationship_counts"]["IN_INDUSTRY"] == 100
    assert statistics["relationship_start_counts"][
        "Company|IN_INDUSTRY"
    ] == 95
    assert statistics["relationship_end_counts"][
        "IN_INDUSTRY|Industry"
    ] == 100
    assert discovery["status"] == "fallback"
    assert not any(
        "(:`Company`)-[r:`IN_INDUSTRY`]->(:`Industry`)" in query
        for query in calls
    )


def test_pattern_step_statistics_use_neo4j_upper_bound_and_directional_fanout() -> None:
    relationships = [
        {
            "type": "IN_INDUSTRY",
            "patterns": [
                {
                    "from": "Company",
                    "to": "Industry",
                    "count": None,
                }
            ],
            "properties": [],
        }
    ]
    statistics = {
        "node_counts": {"Company": 100, "Industry": 20},
        "relationship_counts": {"IN_INDUSTRY": 100},
        "relationship_start_counts": {"Company|IN_INDUSTRY": 95},
        "relationship_end_counts": {"IN_INDUSTRY|Industry": 100},
    }

    enriched = builder._attach_pattern_statistics(relationships, statistics)
    pattern = enriched[0]["patterns"][0]

    assert pattern["count"] == 95
    assert pattern["count_kind"] == "upper_bound"
    assert pattern["count_method"] == "neo4j_min_endpoint_counts"
    assert pattern["outgoing_fanout"] == 0.95
    assert pattern["incoming_fanout"] == 4.75


def test_schema_paths_include_mixed_direction_simple_traversals() -> None:
    relationships = [
        {
            "type": "PURCHASED",
            "patterns": [{"from": "Customer", "to": "Order"}],
        },
        {
            "type": "ORDERS",
            "patterns": [{"from": "Order", "to": "Product"}],
        },
        {
            "type": "SUPPLIES",
            "patterns": [{"from": "Supplier", "to": "Product"}],
        },
    ]

    paths = builder._enumerate_schema_paths(relationships, max_hops=3)
    signatures = {path["signature"] for path in paths}

    assert (
        "(Customer)-[:PURCHASED]->(Order)-[:ORDERS]->"
        "(Product)<-[:SUPPLIES]-(Supplier)"
    ) in signatures
    assert all(
        len({path["edges"][0]["from"], *(edge["to"] for edge in path["edges"])})
        == len(path["edges"]) + 1
        for path in paths
    )


def test_schema_summary_uses_the_paths_already_built() -> None:
    paths = [{"signature": f"path-{index}"} for index in range(7)]
    discovery = {"status": "available", "count": 0}
    path_discovery = {
        "status": "complete",
        "count": 7,
        "limit": 100,
        "max_hops": 3,
    }

    summary = builder._summarize_live_profile(
        [],
        [],
        paths,
        [],
        discovery,
        path_discovery,
    )

    assert summary["schema_path_count"] == 7
    assert summary["vector_index_discovery"] == discovery
    assert summary["path_discovery"] == path_discovery


def test_schema_path_budget_marks_the_enumeration_boundary(monkeypatch) -> None:
    monkeypatch.setenv("T2C_PROFILE_PATH_LIMIT", "2")
    relationships = [
        {
            "type": "REL",
            "patterns": [
                {"from": "A", "to": "B"},
                {"from": "B", "to": "C"},
            ],
        }
    ]

    paths = builder._enumerate_schema_paths(relationships, max_hops=3)

    assert len(paths) == 2
