"""
Flask REST API — View layer only.
Business logic lives in services/rag_service.py.

Endpoints:
    POST /api/text2cypher  — question → Cypher → raw DB results
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

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_settings
from flask import Flask, jsonify, request
from models.graph import get_schema_labels, get_schema_relationships
from services.rag_service import (
    get_available_databases,
    get_raw_results,
    get_results,
    get_schema_info,
)

app = Flask(__name__)
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


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
                "trace_id": "",
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
                "trace_id": result.get("trace_id", ""),
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

    print(
        f"Flask API | profile={settings.profile_database_name} "
        f"physical_db={settings.database_name}"
    )
    print(f"Listening on http://{host}:{port}")
    print("  POST /api/text2cypher  — raw results (for t2c)")
    print("  POST /api/rag          — LLM-formatted response")
    print("  GET  /api/databases    — list databases")
    print("  GET  /api/schema       — schema info")
    print("  GET  /health           — health check")

    app.run(host=host, port=port, debug=False, threaded=True)
