from __future__ import annotations

from types import SimpleNamespace

from neo4j_t2c import (
    PipelineDependencies,
    Text2CypherEngine,
    create_engine,
    pipeline,
)
from neo4j_t2c import service as runtime_service
from neo4j_t2c.adapters import CallableTraceSink, InjectedPipelineBackend
from neo4j_t2c.grounding import context as context_builder


class FakeGraph:
    schema = "(:Movie {title: STRING})"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def refresh_schema(self) -> None:
        raise AssertionError("Injected graph schema must not refresh implicitly")

    def query(self, cypher: str, parameters=None) -> list[dict]:
        self.queries.append(cypher)
        if cypher.startswith("EXPLAIN "):
            return []
        if cypher.strip().startswith(
            "MATCH (m:Movie) RETURN m.title"
        ):
            return [{"m.title": "The Matrix"}]
        return []

    def close(self) -> None:
        pass


class FakeModel:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, input, config=None, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            content="MATCH (m:Movie) RETURN m.title"
        )


class RepairModel:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, input, config=None, **kwargs):
        self.calls += 1
        query = (
            "MATCH (m:Movie)<-[:FEATURES]-(x:Unknown) RETURN m"
            if self.calls == 1
            else "MATCH (m:Movie) RETURN m.title"
        )
        return SimpleNamespace(content=query)


class DirectionRepairModel:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, input, config=None, **kwargs):
        self.calls += 1
        query = (
            "MATCH (l:Lake)<-[:locatedIn]-(c:Country {name: 'Armenia'}) "
            "RETURN l.name"
            if self.calls == 1
            else "MATCH (l:Lake)-[:locatedIn]->(c:Country {name: 'Armenia'}) "
            "RETURN l.name"
        )
        return SimpleNamespace(content=query)


class DirectionGraph:
    schema = (
        "Node properties:\n"
        "Lake {name: STRING}\n"
        "Country {name: STRING}\n"
        "Relationship properties:\n\n"
        "The relationships:\n"
        "(:Lake)-[:locatedIn]->(:Country)"
    )

    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, cypher: str, parameters=None) -> list[dict]:
        self.queries.append(cypher)
        if cypher.startswith("EXPLAIN "):
            return []
        return [{"l.name": "Lake Sevan"}]

    def close(self) -> None:
        pass


class EmptyProfileStore:
    def exists(self, database: str) -> bool:
        return False

    def load(self, database: str) -> dict:
        raise FileNotFoundError(database)

    def save(self, database: str, profile) -> None:
        raise AssertionError("Pipeline must not write profiles")


class ExampleProfileStore:
    def exists(self, database: str) -> bool:
        return True

    def load(self, database: str) -> dict:
        return {
            "source_type": "benchmark_csv",
            "examples": [
                {
                    "row": 1,
                    "question": "List movie titles",
                    "cypher": "MATCH (m:Movie) RETURN m.title",
                    "question_evidence": {"terms": ["list", "movie", "title"]},
                    "cypher_shape": {"signature": "retrieve"},
                }
            ],
            "schema_profile": {},
        }

    def save(self, database: str, profile) -> None:
        raise AssertionError("Pipeline must not write profiles")


def _unexpected_global(*args, **kwargs):
    raise AssertionError("Injected pipeline touched legacy global state")


def test_injected_pipeline_avoids_settings_graph_and_llm_singletons(
    monkeypatch,
) -> None:
    monkeypatch.setenv("T2C_ENTITY_RESOLVER_ENABLED", "false")
    monkeypatch.setenv("T2C_CYPHER_RETRIES", "0")
    monkeypatch.setenv("T2C_GENERATE_ONLY", "false")
    monkeypatch.setattr(pipeline, "legacy_database_names", _unexpected_global)
    monkeypatch.setattr(context_builder, "legacy_database_names", _unexpected_global)
    monkeypatch.setattr(runtime_service, "legacy_database_names", _unexpected_global)
    monkeypatch.setattr(runtime_service, "_legacy_graph", _unexpected_global)
    graph = FakeGraph()
    model = FakeModel()
    events = []
    dependencies = PipelineDependencies(
        database="custom_graph",
        physical_database="neo4j",
        graph=graph,
        cypher_model=model,
        profile_store=EmptyProfileStore(),
        trace_sink=CallableTraceSink(events.append),
    )

    result = Text2CypherEngine(
        InjectedPipelineBackend(dependencies)
    ).generate(
        "List movie titles",
        schema=graph.schema,
    )

    assert result.cypher == "MATCH (m:Movie) RETURN m.title"
    assert result.rows == [{"m.title": "The Matrix"}]
    assert result.succeeded
    assert model.calls == 1
    assert graph.queries[-2:] == [
        "EXPLAIN MATCH (m:Movie) RETURN m.title",
        "MATCH (m:Movie) RETURN m.title LIMIT 100",
    ]
    assert events
    assert events[0].stage == "T2C-01"
    assert any(event.stage == "CHAIN-09" for event in events)


def test_create_engine_uses_the_injected_runtime(monkeypatch) -> None:
    monkeypatch.setenv("T2C_ENTITY_RESOLVER_ENABLED", "false")
    monkeypatch.setenv("T2C_CYPHER_RETRIES", "0")
    monkeypatch.setenv("T2C_GENERATE_ONLY", "false")
    graph = FakeGraph()
    model = FakeModel()

    engine = create_engine(
        database="custom_graph",
        graph=graph,
        model=model,
        profile_store=EmptyProfileStore(),
    )
    result = engine.generate("List movie titles", schema=graph.schema)

    assert result.succeeded
    assert result.cypher == "MATCH (m:Movie) RETURN m.title"
    assert result.rows == [{"m.title": "The Matrix"}]


def test_explicit_execution_overrides_generate_only_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("T2C_ENTITY_RESOLVER_ENABLED", "false")
    monkeypatch.setenv("T2C_CYPHER_RETRIES", "0")
    monkeypatch.setenv("T2C_GENERATE_ONLY", "true")
    graph = FakeGraph()
    dependencies = PipelineDependencies(
        database="custom_graph",
        physical_database="neo4j",
        graph=graph,
        cypher_model=FakeModel(),
        profile_store=EmptyProfileStore(),
    )

    result = pipeline.invoke_chain(
        "List movie titles",
        graph.schema,
        dependencies=dependencies,
        execute=True,
    )

    assert result["result"] == [{"m.title": "The Matrix"}]
    assert graph.queries[-2:] == [
        "EXPLAIN MATCH (m:Movie) RETURN m.title",
        "MATCH (m:Movie) RETURN m.title LIMIT 100",
    ]


def test_generate_only_benchmark_does_not_add_an_llm_rerank_call(
    monkeypatch,
) -> None:
    monkeypatch.setenv("T2C_ENTITY_RESOLVER_ENABLED", "false")
    graph = FakeGraph()
    model = FakeModel()
    dependencies = PipelineDependencies(
        database="custom_graph",
        physical_database="neo4j",
        graph=graph,
        cypher_model=model,
        profile_store=ExampleProfileStore(),
    )

    result = pipeline.invoke_chain(
        "List movie titles",
        graph.schema,
        dependencies=dependencies,
        execute=False,
        max_retries=0,
    )

    assert result["query"] == "MATCH (m:Movie) RETURN m.title"
    assert model.calls == 1
    assert graph.queries == []


def test_interactive_retry_budget_overrides_benchmark_zero_retries(
    monkeypatch,
) -> None:
    monkeypatch.setenv("T2C_ENTITY_RESOLVER_ENABLED", "false")
    monkeypatch.setenv("T2C_CYPHER_RETRIES", "0")
    monkeypatch.setenv("T2C_GENERATE_ONLY", "true")
    graph = FakeGraph()
    model = RepairModel()
    dependencies = PipelineDependencies(
        database="custom_graph",
        physical_database="neo4j",
        graph=graph,
        cypher_model=model,
        profile_store=EmptyProfileStore(),
    )

    result = pipeline.invoke_chain(
        "List movie titles",
        graph.schema,
        dependencies=dependencies,
        execute=True,
        max_retries=1,
    )

    assert model.calls == 2
    assert result["result"] == [{"m.title": "The Matrix"}]
    assert result["query"] == "MATCH (m:Movie) RETURN m.title"


def test_schema_direction_contradiction_is_repaired_before_execution(
    monkeypatch,
) -> None:
    monkeypatch.setenv("T2C_ENTITY_RESOLVER_ENABLED", "false")
    graph = DirectionGraph()
    model = DirectionRepairModel()
    dependencies = PipelineDependencies(
        database="geography",
        physical_database="neo4j",
        graph=graph,
        cypher_model=model,
        profile_store=EmptyProfileStore(),
    )

    result = pipeline.invoke_chain(
        "Show every lake found within Armenia.",
        graph.schema,
        dependencies=dependencies,
        execute=True,
        max_retries=1,
    )

    assert model.calls == 2
    assert "Lake)-[:locatedIn]->(c:Country" in result["query"]
    assert result["result"] == [{"l.name": "Lake Sevan"}]
    assert all(
        "Lake)<-[:locatedIn]-(c:Country" not in query
        for query in graph.queries
    )
