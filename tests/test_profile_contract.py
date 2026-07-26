from __future__ import annotations

import copy
import json
from pathlib import Path

from neo4j_t2c.profiles import (
    PROFILE_VERSION,
    load_profile_file,
    migrate_profile_payload,
    schema_fingerprint,
)
from neo4j_t2c.profiles.retrieval import (
    format_profile_context,
    rerank_profile_context,
    select_profile_context,
)
from services.profile_analyzer.context import (
    select_profile_context as legacy_select_profile_context,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = PROJECT_ROOT / "generated_profiles"


def _raw_profile(database: str) -> dict:
    return json.loads(
        (PROFILE_DIR / f"{database}_profile.json").read_text(
            encoding="utf-8"
        )
    )


def test_profile_migration_adds_version_and_preserves_provenance() -> None:
    migrated = migrate_profile_payload(_raw_profile("movies"))

    assert migrated["profile_version"] == PROFILE_VERSION
    assert migrated["database"] == "movies"
    assert migrated["source_type"] in {"benchmark_csv", "hybrid", "neo4j_live"}
    assert migrated["provenance"]["source_type"] == migrated["source_type"]
    assert migrated["row_count"] == len(migrated["examples"])


def test_live_profile_fingerprint_ignores_counts() -> None:
    profile = _raw_profile("stackoverflow")
    schema = profile["schema_profile"]
    changed = copy.deepcopy(schema)
    changed["labels"][0]["count"] = 1
    changed["relationships"][0]["patterns"][0]["count"] = 999

    assert schema_fingerprint(schema)
    assert schema_fingerprint(changed) == schema_fingerprint(schema)


def test_profile_loader_infers_database_from_filename(tmp_path) -> None:
    path = tmp_path / "custom_graph_profile.json"
    path.write_text(
        json.dumps({"source": "input.csv", "examples": []}),
        encoding="utf-8",
    )

    loaded = load_profile_file(path)

    assert loaded["database"] == "custom_graph"
    assert loaded["profile_version"] == PROFILE_VERSION


def test_migration_preserves_retrieval_context_across_profile_types() -> None:
    cases = {
        "movies": "List the first 3 movies that have been directed by actors.",
        "northwind": "Which products have the highest unit price?",
        "recommendations": "Find movies similar to Inception",
        "stackoverflow": "Which users answered Python questions?",
    }

    for database, question in cases.items():
        raw = _raw_profile(database)
        migrated = migrate_profile_payload(raw)
        before = select_profile_context(question, raw)
        after = select_profile_context(question, migrated)

        assert after == before
        assert format_profile_context(after) == format_profile_context(before)


def test_legacy_profile_import_points_to_canonical_retriever() -> None:
    assert legacy_select_profile_context is select_profile_context


def test_formatted_profile_context_emits_one_compact_dynamic_contract() -> None:
    scaffold = "MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) RETURN g.name, avg(m.runtime)"
    context = {
        "schema_profile_summary": {
            "label_count": 0,
            "relationship_type_count": 0,
        },
        "vector_indexes": [{"name": "irrelevantVectorIndex"}],
        "question_evidence": {
            "terms": ["average", "runtime", "movies", "genre"],
            "literals": [],
            "numbers": [],
        },
        "query_plan_contract": {
            "target_label": "Movie",
            "primary_question": "Average runtime by genre",
            "scaffold_cypher": scaffold,
            "candidate_labels": ["Movie", "Genre"],
            "required_relationships": ["IN_GENRE"],
            "expected_operation": "aggregate",
        },
        "selected_recipes": [
            {
                "row": 1,
                "score": 5.0,
                "question": "Average runtime by genre",
                "cypher": scaffold,
                "contract": {},
            }
        ],
        "selected_path_motifs": [
            ["(Movie)-[:IN_GENRE]->(Genre)", 10],
        ],
        "selected_examples": [
            {
                "row": 1,
                "score": 5.0,
                "question": "Average runtime by genre",
                "cypher": scaffold,
            }
        ],
    }

    formatted = format_profile_context(context)

    assert formatted.count(scaffold) == 1
    assert "QUERY PLAN CONTRACT JSON" in formatted
    assert "Primary selected query recipe" not in formatted
    assert "Closest learned examples" not in formatted
    assert "Likely graph motifs" not in formatted
    assert "Available Neo4j vector indexes" not in formatted
    assert "Live schema profile: 0 labels" not in formatted


class _ProfileReranker:
    def __init__(self, selected_row: int | None, confidence: float = 0.95) -> None:
        self.selected_row = selected_row
        self.confidence = confidence

    def invoke(self, input, config=None, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(
            content=json.dumps(
                {
                    "selected_row": self.selected_row,
                    "confidence": self.confidence,
                    "reason": "The candidate preserves target, relation, and output shape.",
                }
            )
        )


def test_llm_reranker_selects_structurally_equivalent_profile_recipe() -> None:
    count_query = (
        "MATCH (c:Country)<-[:locatedIn]-(l:Lake) "
        "RETURN count(DISTINCT c)"
    )
    names_query = (
        "MATCH (l:Lake)-[:locatedIn]->(c:Country {name: 'Armenia'}) "
        "RETURN l.name"
    )
    context = {
        "selected_examples": [
            {
                "row": 68,
                "score": 5.0,
                "question": "How many countries have lakes within their borders?",
                "cypher": count_query,
                "shape": "aggregate",
            },
            {
                "row": 4,
                "score": 4.0,
                "question": "What are the names of lakes situated in Armenia?",
                "cypher": names_query,
                "shape": "retrieve",
            },
        ],
        "selected_recipes": [],
        "query_plan_contract": {},
        "schema_paths": [],
    }

    reranked = rerank_profile_context(
        "Show every lake found within Armenia.",
        context,
        _ProfileReranker(selected_row=4),
    )

    assert reranked["selected_examples"][0]["row"] == 4
    assert reranked["query_plan_contract"]["primary_question"] == (
        "What are the names of lakes situated in Armenia?"
    )
    assert reranked["query_plan_contract"]["expected_operation"] == "retrieve"
    assert reranked["reranking"]["selected_row"] == 4


def test_llm_reranker_drops_contract_when_no_candidate_is_reliable() -> None:
    context = {
        "selected_examples": [
            {
                "row": 68,
                "score": 5.0,
                "question": "How many countries have lakes within their borders?",
                "cypher": "MATCH (c:Country)<-[:locatedIn]-(l:Lake) RETURN count(c)",
                "shape": "aggregate",
            }
        ],
        "selected_recipes": [],
        "query_plan_contract": {"primary_question": "wrong"},
        "schema_paths": [],
    }

    reranked = rerank_profile_context(
        "Show every lake found within Armenia.",
        context,
        _ProfileReranker(selected_row=None, confidence=0.9),
    )

    assert reranked["query_plan_contract"] == {}
    assert reranked["selected_recipes"] == []
    assert reranked["reranking"]["selected_row"] is None
