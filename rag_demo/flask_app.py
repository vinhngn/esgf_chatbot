from __future__ import annotations

from urllib.parse import unquote
from typing import Any

from flask import Flask, jsonify, request

from .graph_cypher_chain import get_results

app = Flask(__name__)


def _extract_generated_cypher(chain_result: dict[str, Any]) -> str | None:
    """Return the decoded Cypher query from the LangChain intermediate steps."""
    steps = chain_result.get("intermediate_steps", [])
    if not isinstance(steps, list):
        return None

    for step in steps:
        if isinstance(step, dict):
            query = step.get("query")
            if query:
                return unquote(query)
    return None


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

    generated_cypher = _extract_generated_cypher(chain_result)
    result_text = chain_result.get("result")

    return jsonify(
        {
            "input_question": question,
            "cypher_query": generated_cypher,
            "result": result_text,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)