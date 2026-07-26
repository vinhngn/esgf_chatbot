from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
from openai import APIConnectionError

from services.control_center.connection_service import suggested_database_password
from services.control_center.model_settings import (
    LOCAL_API_KEY_SECRET,
    MODEL_SECRET_ID,
)
from services.control_center.models import (
    ConnectionKind,
    DatabaseConnection,
    LlmProvider,
    ModelConfiguration,
    SshTunnel,
)
from services.control_center.presets import built_in_connections
from services.control_center.profile_service import build_connection_profile
from services.control_center.runtime import RuntimeManager, is_t2c_api_health
from services.control_center.secrets import MemorySecretStore
from services.control_center.store import ConnectionStore
from services.observability.trace_store import (
    clear_traces,
    get_trace,
    list_traces,
    record_trace_event,
)


def test_presets_cover_demo_and_cypherbench_databases() -> None:
    presets = built_in_connections()

    assert len(presets) == 9
    assert {item.profile_name for item in presets} == {
        "movies",
        "northwind",
        "recommendations",
        "twitter",
        "stackoverflow",
        "company",
        "fictional_character",
        "flight_accident",
        "geography",
    }


def test_tunnel_connection_uses_local_bolt_uri() -> None:
    connection = DatabaseConnection(
        name="Remote graph",
        profile_name="remote",
        kind=ConnectionKind.SSH_TUNNEL,
        uri="bolt://127.0.0.1:19001",
        database="neo4j",
        username="neo4j",
        ssh=SshTunnel(
            jump_host="jump.example.com",
            target_host="graph.example.com",
            remote_port=7687,
            local_port=19001,
        ),
    )

    assert connection.runtime_uri == "bolt://127.0.0.1:19001"


def test_connection_store_persists_custom_connection_and_selection(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "client.sqlite3")
    custom = DatabaseConnection(
        name="Research DB",
        profile_name="research",
        uri="neo4j://localhost:7687",
        database="neo4j",
        username="neo4j",
    )

    store.save(custom)
    store.active_connection_id = custom.id

    reopened = ConnectionStore(tmp_path / "client.sqlite3")
    assert reopened.get(custom.id) == custom
    assert reopened.active_connection_id == custom.id
    assert len(reopened.list()) == 10


def test_secrets_are_kept_out_of_connection_payload(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "client.sqlite3")
    secrets = MemorySecretStore()
    connection = store.get("preset-movies")
    assert connection is not None

    secrets.set(connection.id, "database_password", "private-value")
    store.save(connection)

    assert b"private-value" not in (tmp_path / "client.sqlite3").read_bytes()
    assert secrets.get(connection.id, "database_password") == "private-value"


def test_runtime_environment_is_scoped_to_selected_connection(tmp_path: Path) -> None:
    connection = next(
        item for item in built_in_connections() if item.profile_name == "northwind"
    )
    runtime = RuntimeManager(tmp_path, MemorySecretStore())

    environment = runtime._environment(connection)

    assert environment["NEO4J_DATABASE"] == "northwind"
    assert environment["T2C_PROFILE_DATABASE"] == "northwind"
    assert environment["ENABLE_TRACE_CAPTURE"] == "1"
    assert environment["ENABLE_TRACE_API"] == "1"


def test_t2c_health_signature_rejects_unknown_port_services() -> None:
    assert is_t2c_api_health(
        {
            "status": "ok",
            "database": "movies",
            "physical_database": "movies",
        }
    )
    assert not is_t2c_api_health({"status": "ok"})
    assert not is_t2c_api_health("ok")


def test_runtime_health_must_match_the_selected_connection() -> None:
    from views.studio.state import runtime_matches_connection

    recommendations = next(
        item
        for item in built_in_connections()
        if item.profile_name == "recommendations"
    )

    assert runtime_matches_connection(
        {
            "database": "recommendations",
            "physical_database": "recommendations",
        },
        recommendations,
    )
    assert not runtime_matches_connection(
        {
            "database": "northwind",
            "physical_database": "northwind",
        },
        recommendations,
    )


def test_runtime_environment_uses_saved_model_selection(tmp_path: Path) -> None:
    connection = next(
        item for item in built_in_connections() if item.profile_name == "movies"
    )
    secrets = MemorySecretStore()
    secrets.set(MODEL_SECRET_ID, LOCAL_API_KEY_SECRET, "local-secret")
    runtime = RuntimeManager(tmp_path, secrets)
    model = ModelConfiguration(
        primary_provider=LlmProvider.LOCAL,
        fallback_enabled=False,
        local_model="qwen2.5:7b",
        local_base_url="http://127.0.0.1:11434/v1",
    )

    environment = runtime._environment(connection, model)

    assert environment["LLM_LOCAL_FIRST"] == "true"
    assert environment["LLM_FALLBACK_ENABLED"] == "false"
    assert environment["LOCAL_LLM_MODEL"] == "qwen2.5:7b"
    assert environment["LOCAL_LLM_API_KEY"] == "local-secret"


def test_model_configuration_is_persisted_without_secrets(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "client.sqlite3")
    configuration = ModelConfiguration(
        primary_provider=LlmProvider.LOCAL,
        local_model="local-research-model",
    )

    store.save_model_configuration(configuration)

    assert store.get_model_configuration() == configuration
    assert b"local-research-model" in (tmp_path / "client.sqlite3").read_bytes()
    assert b"api_key" not in (tmp_path / "client.sqlite3").read_bytes()


def test_public_presets_have_convenience_password_hints() -> None:
    presets = {item.profile_name: item for item in built_in_connections()}

    assert suggested_database_password(presets["movies"]) == "movies"
    assert suggested_database_password(presets["company"]) == "cypherbench"


def test_profile_build_composes_matching_csv_with_live_schema(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from services.control_center import profile_service

    input_directory = tmp_path / "inputs"
    input_directory.mkdir()
    (input_directory / "research.csv").write_text(
        "question,schema,cypher\n"
        '"Which records are newest?","","'
        'MATCH (n:Record) RETURN n.name ORDER BY n.createdAt DESC LIMIT 5"\n',
        encoding="utf-8",
    )
    connection = DatabaseConnection(
        name="Research",
        profile_name="research",
        uri="neo4j://localhost:7687",
        database="neo4j",
        username="neo4j",
    )

    monkeypatch.setattr(
        profile_service,
        "build_profile_from_neo4j",
        lambda **_: {
            "source": "neo4j://localhost:7687",
            "source_type": "neo4j_live",
            "database": "research",
            "physical_database": "neo4j",
            "row_count": 1,
            "examples": [
                {
                    "row": 1,
                    "question": "List Record nodes.",
                    "cypher": "MATCH (n:Record) RETURN n",
                }
            ],
            "schema_profile": {
                "labels": [
                    {
                        "label": "Record",
                        "count": 10,
                        "properties": [
                            {"name": "name", "types": ["String"], "role": "text"}
                        ],
                    }
                ],
                "relationships": [],
                "paths": [],
                "vector_indexes": [],
                "summary": {
                    "label_count": 1,
                    "relationship_type_count": 0,
                    "schema_path_count": 0,
                    "vector_index_count": 0,
                },
            },
        },
    )

    _, profile = build_connection_profile(
        project_root=tmp_path,
        connection=connection,
        secrets=MemorySecretStore(),
        session=SimpleNamespace(database_password="password"),
        dataset_directory=input_directory,
    )

    assert profile["source_type"] == "hybrid"
    assert profile["row_count"] == 1
    assert profile["examples"][0]["question"] == "Which records are newest?"
    assert profile["schema_profile"]["labels"][0]["label"] == "Record"


def test_trace_store_keeps_bounded_structured_events(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_TRACE_CAPTURE", "1")
    clear_traces()

    record_trace_event("trace-1", "CHAIN-01", "Input", {"question": "hello"})
    record_trace_event("trace-1", "CHAIN-09", "Output", [{"value": 1}])

    summaries = list_traces()
    detail = get_trace("trace-1")
    assert summaries[0]["event_count"] == 2
    assert detail is not None
    assert detail["events"][0]["payload"]["question"] == "hello"
    assert detail["events"][1]["stage"] == "CHAIN-09"
    clear_traces()


def test_trace_api_exposes_captured_data(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_TRACE_CAPTURE", "1")
    monkeypatch.setenv("ENABLE_TRACE_API", "1")
    clear_traces()
    record_trace_event("trace-api", "T2C-01", "Input", {"question": "hello"})

    from views.flask_api import app

    client = app.test_client()
    listing = client.get("/api/traces")
    detail = client.get("/api/traces/trace-api")

    assert listing.status_code == 200
    assert listing.get_json()["traces"][0]["trace_id"] == "trace-api"
    assert detail.status_code == 200
    assert detail.get_json()["events"][0]["payload"]["question"] == "hello"
    clear_traces()


def test_runtime_api_exposes_model_and_prompt_without_secrets() -> None:
    from views.flask_api import app

    response = app.test_client().get("/api/runtime")
    body = response.get_json()

    assert response.status_code == 200
    assert body["llm"]["primary"] in {"openai", "local"}
    assert body["prompt"]["strategy"] == "schema-profile-evidence-v3"
    assert body["prompt"]["legacy_domain_context"] is False
    assert "api_key" not in response.get_data(as_text=True).lower()


def test_rag_api_forwards_conversation_history(monkeypatch) -> None:
    from views import flask_api

    captured: dict = {}

    def fake_get_results(question, conversation_history):
        captured["question"] = question
        captured["history"] = conversation_history
        return {
            "output": "ok",
            "cypher_query": "RETURN 1",
            "rewritten": "hello",
            "verified_triples": [],
            "instance_triples": [],
            "trace_id": "trace-rag",
            "question_tag": "GRAPH_FOLLOW_UP",
            "answer_source": "NEO4J",
            "route_confidence": 0.94,
            "referenced_turn_ids": ["turn-1"],
        }

    monkeypatch.setattr(flask_api, "get_results", fake_get_results)
    response = flask_api.app.test_client().post(
        "/api/rag",
        json={
            "question": "hello",
            "conversation_history": [{"input": "before", "output": "answer"}],
        },
    )

    assert response.status_code == 200
    assert captured["question"] == "hello"
    assert captured["history"][0]["input"] == "before"
    assert response.get_json()["rewritten"] == "hello"
    assert response.get_json()["question_tag"] == "GRAPH_FOLLOW_UP"
    assert response.get_json()["answer_source"] == "NEO4J"


def test_text2cypher_api_returns_concise_provider_error(monkeypatch) -> None:
    from views import flask_api

    provider_error = APIConnectionError(
        request=httpx.Request("POST", "http://127.0.0.1:20128/v1/chat/completions")
    )
    monkeypatch.setattr(
        flask_api,
        "get_raw_results",
        lambda question, schema: (_ for _ in ()).throw(provider_error),
    )

    response = flask_api.app.test_client().post(
        "/api/text2cypher",
        json={"question": "hello"},
    )
    body = response.get_json()

    assert response.status_code == 503
    assert body["error_code"] == "llm_unavailable"
    assert body["retryable"] is True
    assert "Connection error" not in body["error"]
