from __future__ import annotations

import pytest
from pydantic import ValidationError

from neo4j_t2c import (
    Text2CypherEngine,
    Text2CypherRequest,
    Text2CypherResult,
)


def test_public_engine_translates_existing_service_payload() -> None:
    calls: list[tuple[str, str]] = []

    def backend(question: str, schema: str) -> dict:
        calls.append((question, schema))
        return {
            "cypher_query": "MATCH (m:Movie) RETURN m.title LIMIT 1",
            "result": [{"m.title": "The Matrix"}],
            "error": None,
            "trace_id": "trace-1",
            "rewritten": "List one movie",
            "verified_triples": [["Movie", "HAS_PROPERTY", "title"]],
            "instance_triples": [],
            "grounding_mode": "profile",
        }

    result = Text2CypherEngine(backend).generate(
        "  List one movie  ",
        schema="  (:Movie {title: STRING})  ",
    )

    assert calls == [("List one movie", "(:Movie {title: STRING})")]
    assert isinstance(result, Text2CypherResult)
    assert result.cypher == "MATCH (m:Movie) RETURN m.title LIMIT 1"
    assert result.rows == [{"m.title": "The Matrix"}]
    assert result.succeeded is True
    assert result.verified_triples == [("Movie", "HAS_PROPERTY", "title")]
    assert result.metadata == {"grounding_mode": "profile"}


def test_public_engine_accepts_typed_request() -> None:
    request = Text2CypherRequest(
        question="List movies",
        schema="(:Movie {title: STRING})",
    )
    engine = Text2CypherEngine(
        lambda question, schema: {
            "cypher_query": "MATCH (m:Movie) RETURN m.title",
            "result": [],
        }
    )

    result = engine.invoke(request)

    assert result.cypher == "MATCH (m:Movie) RETURN m.title"
    assert result.rows == []
    assert result.succeeded is True


def test_public_request_rejects_empty_question() -> None:
    with pytest.raises(ValidationError):
        Text2CypherRequest(question="   ")


def test_public_engine_rejects_schema_from_two_sources() -> None:
    request = Text2CypherRequest(question="List movies", schema="(:Movie)")

    with pytest.raises(ValueError, match="not both"):
        Text2CypherEngine(lambda question, schema: {}).generate(
            request,
            schema="(:Person)",
        )


def test_public_engine_rejects_invalid_backend_contract() -> None:
    engine = Text2CypherEngine(lambda question, schema: "invalid")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="must return a mapping"):
        engine.generate("List movies")
