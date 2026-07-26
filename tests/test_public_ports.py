from __future__ import annotations

from types import SimpleNamespace

import pytest

from neo4j_t2c import TraceEvent
from neo4j_t2c.adapters import (
    CallableTraceSink,
    JsonProfileStore,
    LangChainNeo4jClient,
    NullTraceSink,
)
from neo4j_t2c.ports import GraphClient, ProfileStore, TraceSink


class FakeNeo4jGraph:
    get_schema = "(:Movie {title: STRING})"

    def __init__(self) -> None:
        self.queries: list[tuple[str, dict]] = []
        self.refreshes = 0
        self._driver = SimpleNamespace(close=lambda: None)

    def query(self, cypher: str, params: dict) -> list[dict]:
        self.queries.append((cypher, params))
        return [{"title": "The Matrix"}]

    def refresh_schema(self) -> None:
        self.refreshes += 1


def test_neo4j_adapter_satisfies_graph_port() -> None:
    graph = FakeNeo4jGraph()
    client = LangChainNeo4jClient(graph)  # type: ignore[arg-type]

    assert isinstance(client, GraphClient)
    assert client.schema == "(:Movie {title: STRING})"
    assert client.query("RETURN $value AS title", {"value": "The Matrix"}) == [
        {"title": "The Matrix"}
    ]
    assert graph.queries == [
        ("RETURN $value AS title", {"value": "The Matrix"})
    ]
    client.query("RETURN $value", params={"value": 1})
    assert graph.queries[-1] == ("RETURN $value", {"value": 1})
    client.refresh_schema()
    assert graph.refreshes == 1


def test_json_profile_store_round_trip_is_atomic(tmp_path) -> None:
    store = JsonProfileStore(tmp_path / "profiles")
    profile = {
        "database": "movies",
        "examples": [{"question": "List movies"}],
    }

    store.save("Movies", profile)

    assert isinstance(store, ProfileStore)
    assert store.exists("movies")
    loaded = store.load("movies")
    assert loaded["database"] == "movies"
    assert loaded["examples"] == profile["examples"]
    assert loaded["profile_version"] == "2.0"
    assert not list((tmp_path / "profiles").glob("*.tmp"))


def test_json_profile_store_rejects_path_traversal(tmp_path) -> None:
    store = JsonProfileStore(tmp_path)

    with pytest.raises(ValueError, match="profile names"):
        store.path_for("../secrets")


def test_trace_sink_adapters_satisfy_port() -> None:
    captured: list[TraceEvent] = []
    callback_sink = CallableTraceSink(captured.append)
    null_sink = NullTraceSink()
    event = TraceEvent(
        trace_id="trace-1",
        stage="GROUND",
        message="Schema selected",
        payload={"labels": ["Movie"]},
    )

    assert isinstance(callback_sink, TraceSink)
    assert isinstance(null_sink, TraceSink)
    callback_sink.emit(event)
    null_sink.emit(event)

    assert captured == [event]
