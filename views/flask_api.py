"""
Flask REST API — View layer only.
Business logic lives in services/rag_service.py.

Endpoints:
    POST /api/text2cypher  — question → triple extraction → Cypher → raw DB results
    POST /api/rag          — question → Cypher → LLM-formatted response
    POST /api/set_database — info endpoint (DB set via .env)
    GET  /api/databases    — list available databases
    GET  /api/schema       — schema info for current or specified DB
    GET  /health           — health check
"""

from __future__ import annotations

import logging
import os
import sys
import threading

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_settings
from flask import Flask, jsonify, request
from models.llm import get_interpreter_llm
from models.graph import get_schema_labels, get_schema_relationships
from services.rag_service import (
    get_available_databases,
    get_raw_results,
    get_results,
    get_schema_info,
)
from werkzeug.serving import WSGIRequestHandler

app = Flask(__name__)
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


class QuietWSGIRequestHandler(WSGIRequestHandler):
    """Suppress noisy probe/TLS-handshake logs on the plain HTTP dev server."""

    _suppressed_error_markers = (
        "Bad request version",
        "Bad request syntax",
        "Bad HTTP/0.9 request type",
    )
    _suppressed_path_markers = (
        "HEAD /.aws/",
        "HEAD /.ada/",
        "HEAD /.ssh/",
        "HEAD /.midway/",
    )

    def log_error(self, format: str, *args) -> None:  # noqa: A002
        message = format % args if args else format
        if any(marker in message for marker in self._suppressed_error_markers):
            return
        super().log_error(format, *args)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        request_line = getattr(self, "requestline", "") or ""
        if any(marker in request_line for marker in self._suppressed_path_markers):
            return
        if str(code) == "400" and "\\x16\\x03" in request_line.encode("unicode_escape").decode():
            return
        super().log_request(code, size)


def _warm_up_runtime() -> None:
    """Preload cached schema and the interpreter LLM to reduce cold-start latency."""
    try:
        get_interpreter_llm()
        get_schema_labels()
        get_schema_relationships()
        logger.info("[FlaskAPI] Warm-up completed.")
    except Exception as exc:
        logger.warning("[FlaskAPI] Warm-up failed: %s", exc)


# ---------------------------------------------------------------------------
# Write endpoints
# ---------------------------------------------------------------------------


@app.post("/api/text2cypher")
def text2cypher():
    """
    Question → triple extraction → Cypher → raw DB results (no LLM formatting).

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

    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        results = get_raw_results(question)

        return jsonify(
            {
                "cypher_query": results.get("cypher_query", ""),
                "result": results.get("result", []),
                "error": results.get("error"),
                "rewritten": results.get("rewritten", ""),
                "verified_triples": results.get("verified_triples", []),
                "instance_triples": results.get("instance_triples", []),
                "intent": results.get("intent", {}),
                "query_plan": results.get("query_plan", {}),
                "return_contract": results.get("return_contract", {}),
                "path_hints": results.get("path_hints", {}),
                "query_constraints": results.get("query_constraints", {}),
            }
        )

    except Exception as e:
        logger.error("Error in /api/text2cypher: %s", e, exc_info=True)
        return jsonify(
            {
                "cypher_query": "",
                "result": [],
                "error": str(e),
                "rewritten": "",
                "verified_triples": [],
                "instance_triples": [],
                "intent": {},
                "query_plan": {},
                "return_contract": {},
                "path_hints": {},
                "query_constraints": {},
            }
        ), 500


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

    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        result = get_results(question)

        return jsonify(
            {
                "input_question": question,
                "output": result.get("output", ""),
                "cypher_query": result.get("cypher_query", ""),
                "intent": result.get("intent", {}),
                "query_plan": result.get("query_plan", {}),
                "return_contract": result.get("return_contract", {}),
                "path_hints": result.get("path_hints", {}),
                "query_constraints": result.get("query_constraints", {}),
                "error": None,
            }
        )

    except Exception as e:
        logger.error("Error in /api/rag: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


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
            "current_database": settings.database_name,
            "available": available,
            "use_generalized_template": settings.USE_GENERALIZED_TEMPLATE,
            "note": "Change NEO4J_DATABASE in .env to switch databases.",
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
                "database": settings.database_name,
                "use_generalized_template": settings.USE_GENERALIZED_TEMPLATE,
                "labels": len(labels),
                "relationships": len(rels),
            }
        )
    except Exception as e:
        logger.error("Health check failed: %s", e)
        return jsonify({"status": "error", "detail": str(e)}), 500


@app.get("/")
def root():
    """Quiet root endpoint for local checks and incidental probes."""
    settings = get_settings()
    return jsonify(
        {
            "status": "ok",
            "service": "esgf_chatbot_flask_api",
            "database": settings.database_name,
        }
    )


@app.get("/api/databases")
def list_databases():
    """List all available databases with their configurations."""
    available = get_available_databases()
    settings = get_settings()
    return jsonify(
        {
            "current": settings.database_name,
            "available": available,
            "use_generalized_template": settings.USE_GENERALIZED_TEMPLATE,
            "note": "Change NEO4J_DATABASE in .env to switch databases.",
        }
    )


@app.get("/api/schema")
def get_schema():
    """Get schema info for the current or a specified database."""
    database = request.args.get("database")
    schema_info = get_schema_info(database)
    return jsonify(schema_info)


# ---------------------------------------------------------------------------
# Entry point (standalone mode)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    settings = get_settings()
    port = settings.FLASK_PORT
    host = settings.FLASK_HOST

    print(f"Flask API | DB: {settings.database_name}")
    print(f"Listening on http://{host}:{port}")
    print("  POST /api/text2cypher  — raw results (for t2c)")
    print("  POST /api/rag          — LLM-formatted response")
    print("  GET  /api/databases    — list databases")
    print("  GET  /api/schema       — schema info")
    print("  GET  /health           — health check")

    threading.Thread(target=_warm_up_runtime, daemon=True).start()
    app.run(
        host=host,
        port=port,
        debug=False,
        threaded=True,
        request_handler=QuietWSGIRequestHandler,
    )
