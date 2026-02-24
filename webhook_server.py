"""
Standalone Webhook Server for esgf_chatbot.
Runs on port 8000 to match t2c_eval_framework config.yml endpoint.

Usage:
    cd esgf_chatbot
    .venv/Scripts/python.exe webhook_server.py

Endpoints:
    POST /api/text2cypher  — question → triple extraction → Cypher → raw DB results
    GET  /health           — health check
"""

from __future__ import annotations

import logging
import os
import sys

# Ensure project root (esgf_chatbot/) is on the path so all modules resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

# Load .env before importing anything that reads config
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from config import get_settings
from flask import Flask, jsonify, request
from models.graph import get_schema_labels, get_schema_relationships
from services.rag_service import get_raw_results

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("webhook_server")

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.post("/api/text2cypher")
def text2cypher():
    """
    Main endpoint consumed by t2c_eval_framework.

    Expected request body (JSON):
        {
            "question": "Which models predict precipitation?",
            "schema":   "Node properties: ..."   (optional — esgf uses live schema)
        }

    Response (JSON):
        {
            "cypher_query":      str,
            "result":            list[dict],
            "error":             str | null,
            "rewritten":         str,
            "verified_triples":  list[list],
            "instance_triples":  list[list]
        }
    """
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()

    if not question:
        return jsonify({"error": "question is required"}), 400

    settings = get_settings()
    logger.info("Q: %s... | DB: %s", question[:80], settings.database_name)

    try:
        results = get_raw_results(question)

        logger.info(
            "Cypher: %s...",
            (results.get("cypher_query") or "")[:100],
        )
        logger.info(
            "Rewritten: %s | Verified: %d | Instance: %d",
            results.get("rewritten", ""),
            len(results.get("verified_triples", [])),
            len(results.get("instance_triples", [])),
        )

        return jsonify(
            {
                "cypher_query": results.get("cypher_query", ""),
                "result": results.get("result", []),
                "error": results.get("error"),
                "rewritten": results.get("rewritten", ""),
                "verified_triples": results.get("verified_triples", []),
                "instance_triples": results.get("instance_triples", []),
            }
        )

    except Exception as e:
        logger.error("Unhandled error: %s", e, exc_info=True)
        return jsonify(
            {
                "cypher_query": "",
                "result": [],
                "error": str(e),
                "rewritten": "",
                "verified_triples": [],
                "instance_triples": [],
            }
        ), 500


@app.get("/health")
def health():
    """Health check — confirms server + Neo4j schema are reachable."""
    try:
        settings = get_settings()
        labels = get_schema_labels()
        rels = get_schema_relationships()
        return jsonify(
            {
                "status": "ok",
                "database": settings.database_name,
                "labels": len(labels),
                "relationships": len(rels),
            }
        )
    except Exception as e:
        logger.error("Health check failed: %s", e)
        return jsonify({"status": "error", "detail": str(e)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    settings = get_settings()

    HOST = "127.0.0.1"
    PORT = 8000  # Must match t2c_eval_framework/config/config.yml webhook.endpoint

    print("=" * 60)
    print("  esgf_chatbot — Standalone Webhook Server")
    print(f"  Database : {settings.database_name}")
    print(f"  Listening: http://{HOST}:{PORT}")
    print()
    print("  Endpoints:")
    print(f"    POST http://{HOST}:{PORT}/api/text2cypher")
    print(f"    GET  http://{HOST}:{PORT}/health")
    print()
    print("  t2c config.yml webhook.endpoint must be:")
    print(f"    http://{HOST}:{PORT}/api/text2cypher")
    print("=" * 60)

    app.run(host=HOST, port=PORT, debug=False, threaded=True)
