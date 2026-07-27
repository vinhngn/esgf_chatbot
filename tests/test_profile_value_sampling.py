from __future__ import annotations

from typing import Any

from neo4j_t2c.profiles.builders import neo4j


def test_value_profile_limits_nodes_before_aggregation(monkeypatch: Any) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_query(session: Any, cypher: str, **params: Any) -> list[dict]:
        calls.append((cypher, params))
        return [{"value": "Ada", "frequency": 2}]

    monkeypatch.setattr(neo4j, "_query", fake_query)

    profile = neo4j._collect_value_profile(
        object(),
        [
            {
                "label": "Person",
                "properties": [{"name": "name", "role": "text"}],
            }
        ],
        sample_limit=10_000,
        value_limit=50,
    )

    assert profile == {"Person": {"name": [{"value": "Ada", "frequency": 2}]}}
    assert "WITH n LIMIT $sample_limit" in calls[0][0]
    assert calls[0][1] == {"sample_limit": 10_000, "value_limit": 50}
