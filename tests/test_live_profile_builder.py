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
