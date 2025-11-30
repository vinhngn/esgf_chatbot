from __future__ import annotations

from urllib.parse import unquote

from flask import Flask, jsonify, request

from graph_cypher_chain import get_results

app = Flask(__name__)


def _extract_cypher_queries(chain_result):
    steps = chain_result.get("intermediate_steps", [])
    if not isinstance(steps, list):
        return None, None

    for step in steps:
        if isinstance(step, dict):
            encoded = step.get("query")
            if encoded:
                return encoded, unquote(encoded)
    return None, None


@app.post("/api/text2cypher")
def text2cypher():
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    chain_result = get_results(question=question)
    if isinstance(chain_result, str):
        return jsonify(
            {
                "input_question": question,
                "cypher_query": None,
                "result": chain_result,
                "error": chain_result,
            }
        )

    encoded_query, decoded_query = _extract_cypher_queries(chain_result)
    return jsonify(
        {
            "input_question": question,
            "cypher_query": decoded_query,
            "result": chain_result.get("result"),
            "error": chain_result.get("error"),
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
