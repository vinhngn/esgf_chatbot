from __future__ import annotations

import json
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


class SupportingProbeGraph(FakeGraph):
    def query(self, cypher: str, parameters=None) -> list[dict]:
        if cypher.endswith("RETURN 1 AS evidence LIMIT 1"):
            self.queries.append(cypher)
            return [{"evidence": 1}]
        return super().query(cypher, parameters)


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


class PlanningModel:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, input, config=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "semantic_contract": {
                            "target_concepts": ["movie"],
                            "operation": "retrieve",
                            "metric": "",
                            "grouping": [],
                            "projections": ["movie title"],
                            "constraints": [],
                            "order": "none",
                            "limit": None,
                            "ambiguities": [],
                            "confidence": 0.98,
                        },
                        "selected_row": 1,
                        "confidence": 0.97,
                        "reason": "The example has the same target and projection.",
                    }
                )
            )
        return SimpleNamespace(
            content="MATCH (m:Movie) RETURN m.title"
        )


class AmbiguousPlanningModel(PlanningModel):
    def invoke(self, input, config=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "semantic_contract": {
                            "target_concepts": ["movie"],
                            "operation": "retrieve",
                            "metric": "",
                            "grouping": [],
                            "projections": ["title"],
                            "constraints": [],
                            "order": "none",
                            "limit": None,
                            "ambiguities": ["the target mapping needs evidence"],
                            "confidence": 0.3,
                        },
                        "selected_row": 1,
                        "confidence": 0.7,
                        "reason": "The example is plausible but uncertain.",
                    }
                )
            )
        return SimpleNamespace(
            content="MATCH (m:Movie) RETURN m.title"
        )


class ActivePlanningModel(AmbiguousPlanningModel):
    def __init__(self) -> None:
        super().__init__()
        self.inputs = []

    def invoke(self, input, config=None, **kwargs):
        self.inputs.append(input)
        return super().invoke(input, config, **kwargs)


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


class SchemaOnlyProfileStore:
    def exists(self, database: str) -> bool:
        return True

    def load(self, database: str) -> dict:
        return {
            "source_type": "live_neo4j",
            "examples": [],
            "schema_profile": {
                "summary": {
                    "label_count": 2,
                    "relationship_type_count": 1,
                },
                "paths": [],
            },
        }

    def save(self, database: str, profile) -> None:
        raise AssertionError("Pipeline must not write profiles")


class SchemaAdaptingModel:
    def __init__(self) -> None:
        self.calls = 0
        self.inputs = []

    def invoke(self, input, config=None, **kwargs):
        self.calls += 1
        self.inputs.append(input)
        if self.calls == 1:
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "semantic_contract": {
                            "target_concepts": ["lake"],
                            "operation": "retrieve",
                            "metric": "",
                            "relations": ["located within"],
                            "grouping": [],
                            "projections": ["lake name"],
                            "constraints": [
                                {
                                    "subject": "lake",
                                    "attribute": "location",
                                    "operator": "within",
                                    "value": "Armenia",
                                }
                            ],
                            "order": "none",
                            "limit": None,
                            "ambiguities": [],
                            "confidence": 0.86,
                        },
                        "selected_row": None,
                        "confidence": 0.0,
                        "reason": "No learned example is available.",
                    }
                )
            )
        if self.calls == 2:
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "labels": [
                            {
                                "concept": "lake",
                                "label": "Lake",
                                "confidence": 0.98,
                            }
                        ],
                        "properties": [
                            {
                                "concept": "lake name",
                                "label": "Lake",
                                "property_name": "name",
                                "confidence": 0.97,
                            }
                        ],
                        "edges": [
                            {
                                "relation": "located within",
                                "start_label": "Lake",
                                "relationship_type": "locatedIn",
                                "end_label": "Country",
                                "confidence": 0.96,
                            }
                        ],
                        "ambiguities": [],
                        "confidence": 0.96,
                    }
                )
            )
        return SimpleNamespace(
            content=(
                "MATCH (l:Lake)-[:locatedIn]->"
                "(c:Country {name: 'Armenia'}) RETURN l.name"
            )
        )


class RankProfileStore:
    def exists(self, database: str) -> bool:
        return True

    def load(self, database: str) -> dict:
        return {
            "source_type": "benchmark_csv",
            "examples": [
                {
                    "row": 1,
                    "question": "Show the top 3 movies by revenue.",
                    "cypher": (
                        "MATCH (m:Movie) RETURN m.title "
                        "ORDER BY m.revenue DESC LIMIT 3"
                    ),
                    "question_evidence": {
                        "terms": ["top", "movie", "revenue"]
                    },
                    "cypher_shape": {"signature": "rank"},
                }
            ],
            "schema_profile": {},
        }

    def save(self, database: str, profile) -> None:
        raise AssertionError("Pipeline must not write profiles")


class RankRepairModel:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, input, config=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "semantic_contract": {
                            "target_concepts": ["movie"],
                            "operation": "rank",
                            "metric": "revenue",
                            "relations": [],
                            "grouping": [],
                            "projections": ["movie title"],
                            "constraints": [],
                            "order": "descending",
                            "limit": 3,
                            "ambiguities": [],
                            "confidence": 0.97,
                        },
                        "selected_row": 1,
                        "confidence": 0.98,
                        "reason": "Exact ranking structure.",
                    }
                )
            )
        if self.calls == 2:
            return SimpleNamespace(
                content="MATCH (m:Movie) RETURN m.title LIMIT 3"
            )
        return SimpleNamespace(
            content=(
                "MATCH (m:Movie) RETURN m.title "
                "ORDER BY m.revenue DESC LIMIT 3"
            )
        )


class RankGraph:
    schema = "(:Movie {title: STRING, revenue: FLOAT})"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.rows = [
            {"m.title": "A"},
            {"m.title": "B"},
            {"m.title": "C"},
            {"m.title": "D"},
        ]

    def query(self, cypher: str, parameters=None) -> list[dict]:
        self.queries.append(cypher)
        if cypher.startswith("EXPLAIN "):
            return []
        if "ORDER BY m.revenue DESC LIMIT 4" in cypher:
            return self.rows
        if "MATCH (m:Movie) RETURN m.title" in cypher:
            return self.rows[:3]
        return []

    def close(self) -> None:
        pass


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


def test_active_generate_only_uses_planning_but_skips_candidate_execution(
    monkeypatch,
) -> None:
    monkeypatch.setenv("T2C_ENTITY_RESOLVER_ENABLED", "false")
    monkeypatch.setenv("T2C_ADAPTIVE_PLANNING_MODE", "active")
    graph = SupportingProbeGraph()
    model = ActivePlanningModel()
    dependencies = PipelineDependencies(
        database="custom_graph",
        physical_database="neo4j",
        graph=graph,
        cypher_model=model,
        profile_store=ExampleProfileStore(),
    )

    result = pipeline.invoke_chain(
        "Show me the movie titles.",
        graph.schema,
        dependencies=dependencies,
        execute=False,
        max_retries=0,
    )

    assert result["query"] == "MATCH (m:Movie) RETURN m.title"
    assert model.calls == 2
    assert (
        "MATCH (n:`Movie`) RETURN 1 AS evidence LIMIT 1"
        in graph.queries
    )
    assert all(
        not query.startswith("EXPLAIN ")
        for query in graph.queries
    )
    assert (
        "MATCH (m:Movie) RETURN m.title"
        not in graph.queries
    )


def test_interactive_pipeline_records_adaptive_plan_in_shadow_mode(
    monkeypatch,
) -> None:
    monkeypatch.setenv("T2C_ENTITY_RESOLVER_ENABLED", "false")
    monkeypatch.setenv("T2C_CYPHER_RETRIES", "0")
    monkeypatch.setenv("T2C_GENERATE_ONLY", "false")
    graph = FakeGraph()
    model = PlanningModel()
    events = []
    dependencies = PipelineDependencies(
        database="custom_graph",
        physical_database="neo4j",
        graph=graph,
        cypher_model=model,
        profile_store=ExampleProfileStore(),
        trace_sink=CallableTraceSink(events.append),
    )

    result = Text2CypherEngine(
        InjectedPipelineBackend(dependencies)
    ).generate(
        "Show me the movie titles.",
        schema=graph.schema,
    )

    planning_event = next(
        event for event in events if event.stage == "CHAIN-04A"
    )
    assert result.cypher == "MATCH (m:Movie) RETURN m.title"
    assert result.rows == [{"m.title": "The Matrix"}]
    assert model.calls == 2
    assert planning_event.payload["mode"] == "shadow"
    assert planning_event.payload["semantic_contract"]["target_concepts"] == [
        "movie"
    ]
    assert planning_event.payload["uncertainty"]["should_expand"] is False


def test_ambiguous_interactive_plan_executes_one_bounded_evidence_probe(
    monkeypatch,
) -> None:
    monkeypatch.setenv("T2C_ENTITY_RESOLVER_ENABLED", "false")
    monkeypatch.setenv("T2C_CYPHER_RETRIES", "0")
    monkeypatch.setenv("T2C_GENERATE_ONLY", "false")
    graph = FakeGraph()
    model = AmbiguousPlanningModel()
    events = []
    dependencies = PipelineDependencies(
        database="custom_graph",
        physical_database="neo4j",
        graph=graph,
        cypher_model=model,
        profile_store=ExampleProfileStore(),
        trace_sink=CallableTraceSink(events.append),
    )

    result = Text2CypherEngine(
        InjectedPipelineBackend(dependencies)
    ).generate(
        "Show me the movie titles.",
        schema=graph.schema,
    )

    planning_event = next(
        event for event in events if event.stage == "CHAIN-04A"
    )
    evidence_query = (
        "MATCH (n:`Movie`) RETURN 1 AS evidence LIMIT 1"
    )
    assert result.succeeded
    assert graph.queries.count(evidence_query) == 1
    assert planning_event.payload["uncertainty"]["should_expand"] is True
    assert planning_event.payload["selected_action"]["cypher"] == evidence_query
    assert planning_event.payload["observation"]["supported"] is False


def test_active_mode_exposes_only_supported_probe_to_renderer(
    monkeypatch,
) -> None:
    monkeypatch.setenv("T2C_ENTITY_RESOLVER_ENABLED", "false")
    monkeypatch.setenv("T2C_CYPHER_RETRIES", "0")
    monkeypatch.setenv("T2C_GENERATE_ONLY", "false")
    monkeypatch.setenv("T2C_ADAPTIVE_PLANNING_MODE", "active")
    graph = SupportingProbeGraph()
    model = ActivePlanningModel()
    dependencies = PipelineDependencies(
        database="custom_graph",
        physical_database="neo4j",
        graph=graph,
        cypher_model=model,
        profile_store=ExampleProfileStore(),
    )

    result = Text2CypherEngine(
        InjectedPipelineBackend(dependencies)
    ).generate(
        "Show me the movie titles.",
        schema=graph.schema,
    )

    renderer_messages = model.inputs[-1]
    renderer_prompt = renderer_messages[1].content
    assert result.succeeded
    assert "=== DATA-BACKED GRAPH EVIDENCE ===" in renderer_prompt
    assert "Label :Movie contains data." in renderer_prompt


def test_schema_only_profile_maps_and_probes_before_rendering(
    monkeypatch,
) -> None:
    monkeypatch.setenv("T2C_ENTITY_RESOLVER_ENABLED", "false")
    monkeypatch.setenv("T2C_CYPHER_RETRIES", "0")
    monkeypatch.setenv("T2C_GENERATE_ONLY", "false")
    monkeypatch.setenv("T2C_ADAPTIVE_PLANNING_MODE", "active")
    graph = DirectionGraph()
    model = SchemaAdaptingModel()
    events = []
    dependencies = PipelineDependencies(
        database="geography",
        physical_database="neo4j",
        graph=graph,
        cypher_model=model,
        profile_store=SchemaOnlyProfileStore(),
        trace_sink=CallableTraceSink(events.append),
    )

    result = Text2CypherEngine(
        InjectedPipelineBackend(dependencies)
    ).generate(
        "Show every lake found within Armenia.",
        schema=graph.schema,
    )

    planning_event = next(
        event for event in events if event.stage == "CHAIN-04A"
    )
    renderer_prompt = model.inputs[-1][1].content
    search = planning_event.payload["search"]
    assert result.succeeded
    assert model.calls == 3
    assert search["mapping"]["labels"][0]["label"] == "Lake"
    assert search["mapping"]["edges"][0]["relationship_type"] == "locatedIn"
    assert len(search["observations"]) == 2
    assert "Label :Lake contains data." in renderer_prompt
    assert (
        "(:Lake)-[:locatedIn]->(:Country) contains data."
        in renderer_prompt
    )


def test_active_contract_verifier_repairs_missing_ranking_clause(
    monkeypatch,
) -> None:
    monkeypatch.setenv("T2C_ENTITY_RESOLVER_ENABLED", "false")
    monkeypatch.setenv("T2C_GENERATE_ONLY", "false")
    monkeypatch.setenv("T2C_CYPHER_RETRIES", "1")
    monkeypatch.setenv("T2C_ADAPTIVE_PLANNING_MODE", "active")
    graph = RankGraph()
    model = RankRepairModel()
    events = []
    dependencies = PipelineDependencies(
        database="movies",
        physical_database="neo4j",
        graph=graph,
        cypher_model=model,
        profile_store=RankProfileStore(),
        trace_sink=CallableTraceSink(events.append),
    )

    result = Text2CypherEngine(
        InjectedPipelineBackend(dependencies)
    ).generate(
        "Show the top 3 movies by revenue.",
        schema=graph.schema,
    )

    verification_events = [
        event for event in events if event.stage == "CHAIN-09V"
    ]
    assert model.calls == 3
    assert result.cypher.endswith(
        "ORDER BY m.revenue DESC LIMIT 3"
    )
    assert verification_events[0].payload["passed"] is False
    assert verification_events[-1].payload["passed"] is True
    assert any(
        "ORDER BY m.revenue DESC LIMIT 4" in query
        for query in graph.queries
    )


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
