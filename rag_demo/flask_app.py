from __future__ import annotations

import logging

from flask import Flask, jsonify, request
from graph_cypher_tool import graph_cypher_tool
from rag_agent import (
    _extract_cypher_queries,
    _normalize_value,
    get_schema_str,
    interpret_question,
    interpret_question_with_schema,
    schema_labels,
    schema_relationships,
    verify_triples,
)

app = Flask(__name__)


def get_results(question: str) -> dict:
    # Retry loop for triple extraction
    MAX_ATTEMPTS = 5
    attempt = 0
    verified_triples = []
    instance_triples = []
    triples = []
    rewritten = ""

    while attempt < MAX_ATTEMPTS and not verified_triples:
        if attempt == 0:
            rewritten, triples = interpret_question(question, [])
        else:
            logging.warning(
                f"Retry #{attempt}: no valid triples yet — using schema-enforced mode."
            )
            schema_str = get_schema_str()

            rewritten, triples = interpret_question_with_schema(
                question, [], schema_str
            )

        # Fix: preserve instance_triples across retries
        temp_verified, temp_instance = verify_triples(
            triples, schema_labels, schema_relationships
        )

        for t in temp_instance:
            if t not in instance_triples:
                instance_triples.append(t)

        if temp_verified:
            verified_triples = temp_verified

        attempt += 1

    if not verified_triples:
        if not verified_triples:
            logging.warning(
                f"❌ Still no verified triples after {attempt} attempts — using unverified ones: {triples}"
            )

        verified_triples = triples
        if not instance_triples:
            logging.warning("⚠️ No instance triples found — falling back without them.")

    # Send dict payload to tool
    logging.info(f"💾 FINAL instance_triples passed to LLM: {instance_triples}")
    tool_output = graph_cypher_tool.invoke(
        {
            "question": question,
            "rewritten": rewritten,
            "verified_triples": verified_triples,
            "instance_triples": instance_triples,
            "history": "",
        }
    )

    if isinstance(tool_output, dict):
        result_payload = tool_output
        _, decoded_query = _extract_cypher_queries(tool_output)
    else:
        result_payload = {"result": tool_output}

    decoded_query = decoded_query or ""
    result = (
        _normalize_value(result_payload.get("result"))
        if result_payload.get("result")
        else ""
    )

    return {
        "rewritten_question": rewritten,
        "cypher_query": decoded_query,
        "result": result,
        "verified_triples": verified_triples,
        "instance_triples": instance_triples,
        "error": result_payload.get("error"),
    }

@app.post("/api/text2cypher")
def text2cypher():
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    results = get_results(question=question)
    return jsonify(
        {
            "input_question": question,
            "cypher_query": results.get("cypher_query"),
            "result": results.get("result"),
            "verified_triples": results.get("verified_triples"),
            "instance_triples": results.get("instance_triples"),
            "error": results.get("error"),
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)