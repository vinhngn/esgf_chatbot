"""
Flask REST API — View layer only.
Business logic lives in services/rag_service.py.

Endpoints:
    POST /api/text2cypher  — question → Cypher → raw DB results
    POST /api/rag          — question → Cypher → LLM-formatted response
    POST /api/set_database — info endpoint (DB set via .env)
    GET  /api/databases    — list available databases
    GET  /api/runtime      — effective non-secret runtime configuration
    GET  /api/schema       — schema info for current or specified DB
    GET  /health           — health check
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable, SessionExpired
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from config import get_settings
from models.graph import get_schema_labels, get_schema_relationships
from services.observability import clear_traces, get_trace, list_traces
from services.profile_analyzer.store import profile_exists
from services.rag_service import (
    get_available_databases,
    get_raw_results,
    get_results,
    get_schema_info,
)

app = Flask(__name__)
logger = logging.getLogger(__name__)


def _log_level() -> int:
    name = os.getenv("API_LOG_LEVEL", "ERROR").strip().upper()
    return getattr(logging, name, logging.ERROR)


logging.basicConfig(
    level=_log_level(),
    format="%(levelname)s: %(message)s",
    force=True,
)
logging.getLogger("werkzeug").setLevel(logging.ERROR)


@dataclass(frozen=True)
class _ApiFailure:
    code: str
    message: str
    status: int
    retryable: bool


def _classify_failure(exc: Exception) -> _ApiFailure:
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return _ApiFailure(
            "llm_unavailable",
            "The configured LLM provider is unavailable. Check its endpoint or enable provider fallback.",
            503,
            True,
        )
    if isinstance(exc, RateLimitError):
        return _ApiFailure(
            "llm_rate_limited",
            "The LLM provider rate limit was reached. Retry later or use another provider.",
            429,
            True,
        )
    if isinstance(exc, AuthenticationError):
        return _ApiFailure(
            "llm_authentication_failed",
            "The LLM provider rejected the configured credentials.",
            502,
            False,
        )
    if isinstance(exc, BadRequestError):
        return _ApiFailure(
            "llm_request_rejected",
            "The LLM provider rejected the generated request.",
            502,
            False,
        )
    if isinstance(exc, AuthError):
        return _ApiFailure(
            "neo4j_authentication_failed",
            "Neo4j rejected the configured credentials.",
            502,
            False,
        )
    if isinstance(exc, (ServiceUnavailable, SessionExpired)):
        return _ApiFailure(
            "neo4j_unavailable",
            "Neo4j is unavailable. Check the database connection.",
            503,
            True,
        )
    if isinstance(exc, Neo4jError):
        return _ApiFailure(
            "neo4j_query_failed",
            "Neo4j rejected the generated query.",
            422,
            False,
        )
    return _ApiFailure(
        "internal_error",
        "The request failed unexpectedly.",
        500,
        False,
    )


def _failure_response(
    endpoint: str,
    exc: Exception,
    *,
    payload: dict | None = None,
):
    failure = _classify_failure(exc)
    expected = failure.code != "internal_error"
    log = logger.warning if expected else logger.error
    log(
        "%s failed [%s]: %s",
        endpoint,
        failure.code,
        type(exc).__name__,
        exc_info=not expected,
    )
    body = {
        "error": failure.message,
        "error_code": failure.code,
        "retryable": failure.retryable,
    }
    if payload:
        body.update(payload)
    return jsonify(body), failure.status


def _trace_api_enabled() -> bool:
    return os.getenv("ENABLE_TRACE_API", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _trace_api_disabled():
    return jsonify({"error": "trace API is disabled"}), 404


# ---------------------------------------------------------------------------
# Write endpoints
# ---------------------------------------------------------------------------


@app.post("/api/text2cypher")
def text2cypher():
    """
    Question → Cypher → raw DB results (no triple extraction or answer formatting).

    Expected by t2c_eval_framework.

    Request JSON:
        { "question": "...", "schema": "..." }   (schema is optional)

    Response JSON:
        {
            "cypher_query":     str,
            "result":           list[dict],
            "error":            str | null,
            "rewritten":        str,
            "verified_triples": list[list],
            "instance_triples": list[list]
        }
    """
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    schema = (payload.get("schema") or "").strip()

    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        results = get_raw_results(question, schema)

        return jsonify(
            {
                "cypher_query": results.get("cypher_query", ""),
                "result": results.get("result", []),
                "error": results.get("error"),
                "rewritten": results.get("rewritten", ""),
                "verified_triples": results.get("verified_triples", []),
                "instance_triples": results.get("instance_triples", []),
                "trace_id": results.get("trace_id", ""),
            }
        )

    except Exception as exc:
        return _failure_response(
            "/api/text2cypher",
            exc,
            payload={
                "cypher_query": "",
                "result": [],
                "rewritten": "",
                "verified_triples": [],
                "instance_triples": [],
                "trace_id": "",
            },
        )


@app.post("/api/rag")
def rag_endpoint():
    """
    Question → triple extraction → Cypher → LLM-formatted response.

    Request JSON:
        { "question": "..." }

    Response JSON:
        {
            "input_question": str,
            "output":         str,
            "cypher_query":   str,
            "error":          null
        }
    """
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    conversation_history = payload.get("conversation_history") or []

    if not question:
        return jsonify({"error": "question is required"}), 400
    if not isinstance(conversation_history, list):
        return jsonify({"error": "conversation_history must be a list"}), 400

    try:
        result = get_results(question, conversation_history)

        return jsonify(
            {
                "input_question": question,
                "output": result.get("output", ""),
                "cypher_query": result.get("cypher_query", ""),
                "rewritten": result.get("rewritten", ""),
                "verified_triples": result.get("verified_triples", []),
                "instance_triples": result.get("instance_triples", []),
                "trace_id": result.get("trace_id", ""),
                "question_tag": result.get("question_tag", ""),
                "answer_source": result.get("answer_source", ""),
                "route_confidence": result.get("route_confidence", 0),
                "referenced_turn_ids": result.get(
                    "referenced_turn_ids",
                    [],
                ),
                "error": None,
            }
        )

    except Exception as exc:
        return _failure_response("/api/rag", exc)


@app.post("/api/set_database")
def set_database():
    """
    Info endpoint — databases are configured via .env, not at runtime.
    Returns current database and available options.
    """
    available = get_available_databases()
    settings = get_settings()
    return jsonify(
        {
            "current_database": settings.profile_database_name,
            "physical_database": settings.database_name,
            "available": available,
            "note": "Set NEO4J_DATABASE for the physical DB and T2C_PROFILE_DATABASE for its logical profile.",
        }
    )


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    """Health check — confirms Flask + Neo4j schema are reachable."""
    try:
        settings = get_settings()
        labels = get_schema_labels()
        rels = get_schema_relationships()
        return jsonify(
            {
                "status": "ok",
                "database": settings.profile_database_name,
                "physical_database": settings.database_name,
                "labels": len(labels),
                "relationships": len(rels),
            }
        )
    except Exception as e:
        logger.error("Health check failed: %s", e)
        return jsonify({"status": "error", "detail": str(e)}), 500


@app.get("/api/databases")
def list_databases():
    """List all available databases with their configurations."""
    available = get_available_databases()
    settings = get_settings()
    return jsonify(
        {
            "current": settings.profile_database_name,
            "physical_database": settings.database_name,
            "available": available,
            "note": "Set NEO4J_DATABASE for the physical DB and T2C_PROFILE_DATABASE for its logical profile.",
        }
    )


@app.get("/api/runtime")
def runtime_info():
    """Expose effective non-secret runtime configuration for fast diagnostics."""
    settings = get_settings()
    primary = "local" if settings.LLM_LOCAL_FIRST else "openai"
    secondary = "openai" if primary == "local" else "local"
    return jsonify(
        {
            "status": "ok",
            "database": settings.profile_database_name,
            "physical_database": settings.database_name,
            "llm": {
                "primary": primary,
                "fallback": secondary if settings.LLM_FALLBACK_ENABLED else None,
                "openai_model": settings.OPENAI_MODEL,
                "local_model": settings.LOCAL_LLM_MODEL,
                "provider_retries": settings.LLM_PROVIDER_MAX_RETRIES,
            },
            "prompt": {
                "strategy": "schema-profile-evidence-v3",
                "profile_available": profile_exists(
                    settings.profile_database_name,
                ),
                "legacy_domain_context": False,
            },
        }
    )


@app.get("/api/schema")
def get_schema():
    """Get schema info for the current or a specified database."""
    database = request.args.get("database")
    schema_info = get_schema_info(database)
    return jsonify(schema_info)


@app.get("/api/traces")
def traces():
    if not _trace_api_enabled():
        return _trace_api_disabled()
    try:
        limit = max(1, min(int(request.args.get("limit", "50")), 200))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    return jsonify({"traces": list_traces(limit)})


@app.get("/api/traces/<trace_id>")
def trace_detail(trace_id: str):
    if not _trace_api_enabled():
        return _trace_api_disabled()
    trace = get_trace(trace_id)
    if trace is None:
        return jsonify({"error": "trace not found"}), 404
    return jsonify(trace)


@app.delete("/api/traces")
def delete_traces():
    if not _trace_api_enabled():
        return _trace_api_disabled()
    clear_traces()
    return jsonify({"status": "cleared"})


# ---------------------------------------------------------------------------
# Entry point (standalone mode)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import flask.cli

    settings = get_settings()
    port = settings.FLASK_PORT
    host = settings.FLASK_HOST

    flask.cli.show_server_banner = lambda *args, **kwargs: None
    app.run(host=host, port=port, debug=False, threaded=True)
