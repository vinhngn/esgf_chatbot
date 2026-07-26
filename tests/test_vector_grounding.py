from __future__ import annotations

import csv
from pathlib import Path

from neo4j_t2c.grounding.entities import (
    format_entity_resolution_context,
    resolve_question_entities,
)
from neo4j_t2c.grounding.vectors import (
    format_vector_context,
    resolve_vector_neighbors,
)
from services.universal.vector_resolver import (
    resolve_vector_neighbors as legacy_resolve_vector_neighbors,
)
from tools.clean_t2c_input import clean_t2c_input


class FakeVectorGraph:
    schema = """
    Node properties:
    Movie {title: STRING, runtime: INTEGER}
    Genre {name: STRING}
    Relationships:
    (:Movie)-[:IN_GENRE]->(:Genre)
    """

    def __init__(self) -> None:
        self.vector_calls = 0

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        params = params or {}
        if "SHOW INDEXES" in cypher:
            return [
                {
                    "name": "movieTitle",
                    "type": "RANGE",
                    "entityType": "NODE",
                    "labelsOrTypes": ["Movie"],
                    "properties": ["title"],
                },
                {
                    "name": "moviePlotsEmbedding",
                    "type": "VECTOR",
                    "entityType": "NODE",
                    "labelsOrTypes": ["Movie"],
                    "properties": ["plotEmbedding"],
                },
            ]
        if "db.index.vector.queryNodes" in cypher:
            self.vector_calls += 1
            assert params["index_name"] == "moviePlotsEmbedding"
            assert params["element_id"] == "movie-1"
            return [
                {
                    "labels": ["Movie"],
                    "element_id": "movie-2",
                    "node": {
                        "title": "Interstellar",
                        "plotEmbedding": [0.1] * 1536,
                    },
                    "score": 0.93,
                }
            ]
        if params.get("value") == "Inception":
            return [
                {
                    "labels": ["Movie"],
                    "value": "Inception",
                    "element_id": "movie-1",
                }
            ]
        return []


def test_vector_grounding_exposes_anchored_capability_without_querying_neighbors() -> None:
    graph = FakeVectorGraph()
    question = "Which movies are similar to Inception?"

    entities = resolve_question_entities(question, graph)
    vectors = resolve_vector_neighbors(question, graph, entities)

    assert vectors["active"] is True
    assert vectors["capabilities"][0]["anchor"]["value"] == "Inception"
    assert "moviePlotsEmbedding" in format_vector_context(vectors)
    assert graph.vector_calls == 0


def test_llm_receives_optional_vector_capability_without_keyword_routing() -> None:
    graph = FakeVectorGraph()
    question = "Who directed Inception?"

    entities = resolve_question_entities(question, graph)
    vectors = resolve_vector_neighbors(question, graph, entities)

    assert vectors["active"] is True
    assert graph.vector_calls == 0
    assert "Use only if the question requires" in format_vector_context(vectors)


def test_empty_entity_resolution_adds_no_prompt_context() -> None:
    assert (
        format_entity_resolution_context(
            {
                "enabled": True,
                "anchors": [],
                "indexed_properties": [{"label": "Movie", "property": "title"}],
            }
        )
        == ""
    )


class NoiseMatchingGraph(FakeVectorGraph):
    def __init__(self) -> None:
        super().__init__()
        self.lookup_values: list[str] = []

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        params = params or {}
        if "SHOW INDEXES" in cypher:
            return super().query(cypher, params)
        if "value" in params:
            self.lookup_values.append(str(params["value"]))
            return [
                {
                    "labels": ["Movie"],
                    "value": f"Matched {params['value']}",
                    "element_id": "noise",
                }
            ]
        return []


def test_entity_grounding_excludes_query_language_and_schema_terms() -> None:
    graph = NoiseMatchingGraph()

    entities = resolve_question_entities(
        "What is the average runtime of movies in each genre?",
        graph,
    )

    assert entities["literals"] == []
    assert entities["anchors"] == []
    assert graph.lookup_values == []


def test_legacy_vector_import_points_to_canonical_resolver() -> None:
    assert legacy_resolve_vector_neighbors is resolve_vector_neighbors


def test_t2c_cleaner_removes_corrupt_result_column_without_losing_cases(
    tmp_path: Path,
) -> None:
    source = tmp_path / "recommendations.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "question",
                "schema",
                "cypher",
                "database_name",
                "original_result",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "question": "Which movies are similar to Inception?",
                "schema": "Node properties:\nMovie {title: STRING}",
                "cypher": "MATCH (m:Movie) RETURN m.title",
                "database_name": "recommendations",
                "original_result": '[{"broken": "\\u',
            }
        )

    summary = clean_t2c_input(source)

    with source.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert summary["rows"] == 1
    assert list(rows[0]) == [
        "question",
        "schema",
        "cypher",
        "database_name",
    ]
    assert rows[0]["schema"].startswith("Node properties:")
