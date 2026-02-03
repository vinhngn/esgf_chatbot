"""
Flask App for Text2Cypher Evaluation
Full RAG pipeline with triple extraction and verification
"""
from __future__ import annotations

import logging
import os
import tomllib

from flask import Flask, jsonify, request
from langchain.chains import GraphCypherQAChain
from langchain.prompts.prompt import PromptTemplate
from langchain_community.graphs import Neo4jGraph
from langchain_openai import ChatOpenAI

# Import shared utilities from core module
from core import (
    normalize_value,
    parse_schema,
    interpret_question,
    interpret_question_with_schema,
    verify_triples,
    clean_cypher_query,
)

# Import templates (auto-selected based on database in secrets.toml)
from templates.cypher_climate_template import CYPHER_GENERATION_TEMPLATE

# Load secrets
_script_dir = os.path.dirname(os.path.abspath(__file__))
_secrets_path = os.path.join(_script_dir, ".streamlit", "secrets.toml")

with open(_secrets_path, "rb") as f:
    secrets = tomllib.load(f)

NEO4J_URI = secrets["NEO4J_URI"]
NEO4J_USERNAME = secrets["NEO4J_USERNAME"]
NEO4J_PASSWORD = secrets["NEO4J_PASSWORD"]
NEO4J_DATABASE = secrets["NEO4J_DATABASE"]
OPENAI_API_KEY = secrets["OPENAI_API_KEY"]

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Initialize Neo4j Graph
graph = Neo4jGraph(
    url=NEO4J_URI,
    username=NEO4J_USERNAME,
    password=NEO4J_PASSWORD,
    database=NEO4J_DATABASE,
    sanitize=True,
)

# Fetch schema once at startup
graph.refresh_schema()
schema_labels, schema_relationships = parse_schema(graph.get_schema)

# LLM for triple extraction
interpreter_llm = ChatOpenAI(api_key=OPENAI_API_KEY, temperature=0.3, model="gpt-4o-mini")

# Escape curly braces in template for LangChain
_escaped = CYPHER_GENERATION_TEMPLATE.replace("{schema}", "<<S>>").replace("{question}", "<<Q>>")
_escaped = _escaped.replace("{", "{{").replace("}", "}}").replace("<<S>>", "{schema}").replace("<<Q>>", "{question}")

CYPHER_PROMPT = PromptTemplate(input_variables=["schema", "question"], template=_escaped)

# Initialize GraphCypherQAChain
graph_chain = GraphCypherQAChain.from_llm(
    cypher_llm=ChatOpenAI(api_key=OPENAI_API_KEY, temperature=0.3, model="gpt-4o-mini"),
    qa_llm=ChatOpenAI(api_key=OPENAI_API_KEY, temperature=0.7, model="gpt-4o-mini"),
    graph=graph,
    cypher_prompt=CYPHER_PROMPT,
    validate_cypher=True,
    return_direct=True,
    verbose=True,
    allow_dangerous_requests=True,
    return_intermediate_steps=True,
    top_k=100,
)


def get_results(question: str) -> dict:
    """Full RAG pipeline: extract triples, verify, generate Cypher, execute"""
    try:
        graph.refresh_schema()

        # Triple extraction with retry
        MAX_ATTEMPTS = 3
        verified_triples, instance_triples, rewritten = [], [], ""

        for attempt in range(MAX_ATTEMPTS):
            if attempt == 0:
                rewritten, triples = interpret_question(question, interpreter_llm)
            else:
                rewritten, triples = interpret_question_with_schema(
                    question, interpreter_llm, schema_labels, schema_relationships
                )

            temp_verified, temp_instance = verify_triples(
                triples, schema_labels, schema_relationships, graph
            )

            for t in temp_instance:
                if t not in instance_triples:
                    instance_triples.append(t)

            if temp_verified:
                verified_triples = temp_verified
                break

        if not verified_triples:
            verified_triples = triples

        logging.info(f"Rewritten: {rewritten}")
        logging.info(f"Verified: {verified_triples}, Instance: {instance_triples}")

        # Build enhanced question
        triples_text = "\n".join(f"({s}, {r}, {o})" for s, r, o in verified_triples) or "None"
        instance_text = "\n".join(f"({s}, {r}, {o})" for s, r, o in instance_triples) or "None"

        enhanced = f"{question}\n\nRewritten: {rewritten or question}\n\nVerified Triples:\n{triples_text}\n\nInstance Triples:\n{instance_text}"

        # Invoke chain
        result = graph_chain.invoke({"query": enhanced}, return_only_outputs=True)

        if not result:
            return {"cypher_query": "", "result": [], "verified_triples": verified_triples,
                    "instance_triples": instance_triples, "error": "No result"}

        # Extract Cypher
        cypher = ""
        try:
            steps = result.get("intermediate_steps", [{}])
            if steps and steps[-1].get("query"):
                cypher = clean_cypher_query(steps[-1]["query"])
        except Exception as e:
            logging.warning(f"Failed to extract Cypher: {e}")

        return {
            "cypher_query": cypher,
            "result": normalize_value(result.get("result", [])),
            "verified_triples": verified_triples,
            "instance_triples": instance_triples,
            "error": None,
        }

    except Exception as e:
        logging.error(f"Error: {e}")
        return {"cypher_query": "", "result": [], "verified_triples": [], "instance_triples": [], "error": str(e)}


@app.post("/api/text2cypher")
def text2cypher():
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()

    if not question:
        return jsonify({"error": "question is required"}), 400

    print(f"\n[Flask] Q: {question[:80]}... | DB: {NEO4J_DATABASE}")
    results = get_results(question)
    print(f"[Flask] Cypher: {results.get('cypher_query', '')[:100]}...")

    return jsonify({
        "input_question": question,
        "cypher_query": results["cypher_query"],
        "result": results["result"],
        "verified_triples": results["verified_triples"],
        "instance_triples": results["instance_triples"],
        "error": results["error"],
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "database": NEO4J_DATABASE,
        "labels": len(schema_labels),
        "relationships": len(schema_relationships),
    })


if __name__ == "__main__":
    print(f"Text2Cypher Flask | DB: {NEO4J_DATABASE} | Labels: {len(schema_labels)} | Rels: {len(schema_relationships)}")
    print("http://127.0.0.1:8000 | POST /api/text2cypher | GET /health")
    app.run(host="127.0.0.1", port=8000, debug=False)
