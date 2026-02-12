from __future__ import annotations

import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

from flask import Flask, jsonify, request

from config import get_settings
from controllers.webhook_controller import generate_cypher, get_available_databases
from models.graph import get_schema_labels, get_schema_relationships
from services.rag_service import get_results, get_raw_results

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


@app.post("/api/text2cypher")
def text2cypher():
    """Question → Cypher → raw DB results (no LLM formatting)."""
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()

    if not question:
        return jsonify({"error": "question is required"}), 400

    results = get_raw_results(question)

    return jsonify({
        "input_question": question,
        "cypher_query": results.get("cypher_query"),
        "result": results.get("result"),
        "error": results.get("error"),
    })


@app.post("/api/rag")
def rag_endpoint():
    """Question → Cypher → LLM-formatted response."""
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()

    if not question:
        return jsonify({"error": "question is required"}), 400

    result = get_results(question)

    return jsonify({
        "input_question": question,
        "output": result["output"],
        "cypher_query": result.get("cypher_query", ""),
        "error": None,
    })


@app.post("/api/set_database")
def set_database():
    """Info endpoint - databases are set via .env, not at runtime."""
    available = get_available_databases()
    settings = get_settings()
    return jsonify({
        "current_database": settings.database_name,
        "available": available,
        "note": "Change NEO4J_DATABASE in .env to switch databases.",
    })


@app.get("/health")
def health():
    settings = get_settings()
    return jsonify({
        "status": "ok",
        "database": settings.database_name,
        "labels": len(get_schema_labels()),
        "relationships": len(get_schema_relationships()),
    })


if __name__ == "__main__":
    settings = get_settings()
    port = settings.FLASK_PORT
    host = settings.FLASK_HOST
    print(f"Text2Cypher Flask API | DB: {settings.database_name}")
    print(f"http://{host}:{port}")
    print("  POST /api/text2cypher - Raw results")
    print("  POST /api/rag         - LLM formatted")
    print("  GET  /health          - Health check")
    app.run(host=host, port=port, debug=False, threaded=True)
