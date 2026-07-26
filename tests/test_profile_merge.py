from __future__ import annotations

import pytest

from neo4j_t2c.profiles import merge_profile_with_live_schema


def test_merge_preserves_learned_examples_and_uses_live_schema() -> None:
    learned = {
        "source": "recommendations.csv",
        "source_type": "benchmark_csv",
        "database": "recommendations",
        "row_count": 2,
        "examples": [
            {"row": 1, "question": "question one", "cypher": "RETURN 1"},
            {"row": 2, "question": "question two", "cypher": "RETURN 2"},
        ],
        "question_summary": {"top_tokens": [["movie", 2]]},
        "cypher_summary": {"return_kind_counts": [["property", 2]]},
        "intent_to_shape_signatures": {"entity_list": [["shape", 2]]},
        "motif_to_return_contracts": {"Movie": [["property", 2]]},
        "token_to_path_motifs": {"movie": [["Movie", 2]]},
        "schema_profile": {
            "vector_indexes": [{"name": "movieEmbedding"}],
            "summary": {"vector_index_count": 1},
        },
    }
    live = {
        "source": "neo4j+s://example.test",
        "source_type": "neo4j_live",
        "database": "recommendations",
        "row_count": 1,
        "examples": [{"row": 1, "question": "generated", "cypher": "MATCH (n) RETURN n"}],
        "schema_profile": {
            "labels": [{"label": "Movie", "count": 100, "properties": []}],
            "relationships": [
                {
                    "type": "IN_GENRE",
                    "patterns": [{"from": "Movie", "to": "Genre", "count": None}],
                    "properties": [],
                }
            ],
            "paths": [],
            "vector_indexes": [{"name": "movieEmbedding"}],
            "summary": {
                "label_count": 1,
                "relationship_type_count": 1,
                "vector_index_count": 1,
            },
        },
        "value_profile": {"Movie": {"title": [{"value": "Inception", "frequency": 1}]}},
        "query_recipe_profile": {"recipes": [{"name": "entity_list"}]},
    }

    merged = merge_profile_with_live_schema(learned, live)

    assert merged["source_type"] == "hybrid"
    assert merged["row_count"] == 2
    assert merged["examples"] == learned["examples"]
    assert merged["question_summary"] == learned["question_summary"]
    assert merged["schema_profile"] == live["schema_profile"]
    assert merged["value_profile"] == live["value_profile"]
    assert merged["query_recipe_profile"] == live["query_recipe_profile"]
    assert merged["schema_fingerprint"]
    assert merged["provenance"]["sources"] == [
        {"source": "recommendations.csv", "source_type": "benchmark_csv"},
        {"source": "neo4j+s://example.test", "source_type": "neo4j_live"},
    ]


def test_merge_uses_live_examples_when_no_learned_examples_exist() -> None:
    existing = {
        "source": "",
        "database": "company",
        "examples": [],
    }
    live = {
        "source": "bolt://localhost:15062",
        "source_type": "neo4j_live",
        "database": "company",
        "examples": [{"row": 1, "question": "List Company", "cypher": "MATCH (n:Company) RETURN n"}],
        "schema_profile": {
            "labels": [{"label": "Company", "count": 1, "properties": []}],
            "relationships": [],
            "paths": [],
            "vector_indexes": [],
            "summary": {"label_count": 1, "relationship_type_count": 0},
        },
    }

    merged = merge_profile_with_live_schema(existing, live)

    assert merged["source_type"] == "neo4j_live"
    assert merged["row_count"] == 1
    assert merged["examples"] == live["examples"]


def test_merge_replaces_generated_examples_in_a_live_only_profile() -> None:
    existing = {
        "source": "neo4j://old-host",
        "source_type": "neo4j_live",
        "database": "company",
        "physical_database": "neo4j",
        "examples": [{"question": "stale generated recipe", "cypher": "RETURN 1"}],
    }
    live = {
        "source": "neo4j://new-host",
        "source_type": "neo4j_live",
        "database": "company",
        "physical_database": "neo4j",
        "examples": [{"question": "fresh generated recipe", "cypher": "RETURN 2"}],
    }

    merged = merge_profile_with_live_schema(existing, live)

    assert merged["source_type"] == "neo4j_live"
    assert merged["examples"] == live["examples"]


def test_merge_rejects_a_different_physical_database() -> None:
    existing = {
        "database": "research",
        "physical_database": "recommendations",
        "examples": [{"question": "learned", "cypher": "RETURN 1"}],
    }
    live = {
        "database": "research",
        "physical_database": "northwind",
        "examples": [],
    }

    with pytest.raises(ValueError, match="different physical database"):
        merge_profile_with_live_schema(existing, live)
