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
from services.rag_service import get_results

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


@app.post("/api/text2cypher")
def text2cypher():
    """Generate Cypher query from natural language (simple webhook mode)."""
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    schema = payload.get("schema", "")
    database = payload.get("database")

    if not question:
        return jsonify({"cypher_query": "", "result": [], "error": "question is required"}), 400

    cypher_query = generate_cypher(question, schema, database)

    return jsonify({
        "input_question": question,
        "cypher_query": cypher_query,
        "result": [],
        "error": None,
    })


@app.post("/api/rag")
def rag_endpoint():
    """Full RAG pipeline endpoint (triple extraction + chain)."""
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()

    if not question:
        return jsonify({"error": "question is required"}), 400

    result = get_results(question)

    return jsonify({
        "input_question": question,
        "output": result["output"],
        "cypher_query": result.get("cypher_query", ""),
        "verified_triples": result.get("verified_triples", []),
        "instance_triples": result.get("instance_triples", []),
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
    print(f"Text2Cypher Flask API | DB: {settings.database_name}")
    print("http://127.0.0.1:8000")
    print("  POST /api/text2cypher - Simple Cypher generation")
    print("  POST /api/rag         - Full RAG pipeline")
    print("  GET  /health          - Health check")
    app.run(host="127.0.0.1", port=8000, debug=False, threaded=True)
