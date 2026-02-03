"""
Flask App for Text2Cypher Evaluation
Full RAG pipeline with triple extraction and verification
"""
from __future__ import annotations

import logging
import os
import re
import tomllib
import urllib.parse
from datetime import date, datetime, time

from flask import Flask, jsonify, request
from langchain.chains import GraphCypherQAChain
from langchain.prompts.prompt import PromptTemplate
from langchain.schema import AIMessage, HumanMessage, SystemMessage
from langchain_community.graphs import Neo4jGraph
from langchain_openai import ChatOpenAI

# Load secrets from .streamlit/secrets.toml
_script_dir = os.path.dirname(os.path.abspath(__file__))
_secrets_path = os.path.join(_script_dir, ".streamlit", "secrets.toml")

with open(_secrets_path, "rb") as f:
    secrets = tomllib.load(f)

NEO4J_URI = secrets["NEO4J_URI"]
NEO4J_USERNAME = secrets["NEO4J_USERNAME"]
NEO4J_PASSWORD = secrets["NEO4J_PASSWORD"]
NEO4J_DATABASE = secrets["NEO4J_DATABASE"]
OPENAI_API_KEY = secrets["OPENAI_API_KEY"]

# Import templates based on database
from templates.cypher_climate_template import CYPHER_GENERATION_TEMPLATE
from templates.entity_definitions import entity_definitions
from templates.match_properties_map import match_properties_map

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Initialize Neo4j Graph
graph = Neo4jGraph(
    url=NEO4J_URI,
    username=NEO4J_USERNAME,
    password=NEO4J_PASSWORD,
    database=NEO4J_DATABASE,
    sanitize=True,
)

# Parse schema
def parse_schema(schema_text: str):
    labels = set()
    relationships = set()
    for line in schema_text.splitlines():
        line = line.strip()
        label_matches = re.findall(r"\(:([A-Za-z0-9_]+)\)", line)
        for label in label_matches:
            labels.add(label)
        rel_matches = re.findall(r"\[:([A-Za-z0-9_]+)\]", line)
        for rel in rel_matches:
            relationships.add(rel)
    return labels, relationships

# Fetch schema once at startup
graph.refresh_schema()
schema_text = graph.get_schema
schema_labels, schema_relationships = parse_schema(schema_text)
schema_labels_str = "\n".join(f"- {label}" for label in sorted(schema_labels))
schema_rels_str = "\n".join(f"- {rel}" for rel in sorted(schema_relationships))

# LLM for triple extraction
interpreter_llm = ChatOpenAI(
    api_key=OPENAI_API_KEY,
    temperature=0.3,
    model="gpt-4o-mini",
)

# Create prompt template
# Escape curly braces in template (except {schema} and {question})
# LangChain treats {word} as variables, but our templates have Cypher syntax like {name: "value"}
_escaped_template = CYPHER_GENERATION_TEMPLATE.replace("{schema}", "<<SCHEMA>>").replace("{question}", "<<QUESTION>>")
_escaped_template = _escaped_template.replace("{", "{{").replace("}", "}}")
_escaped_template = _escaped_template.replace("<<SCHEMA>>", "{schema}").replace("<<QUESTION>>", "{question}")

CYPHER_GENERATION_PROMPT = PromptTemplate(
    input_variables=["schema", "question"],
    template=_escaped_template,
)

# Initialize GraphCypherQAChain
graph_chain = GraphCypherQAChain.from_llm(
    cypher_llm=ChatOpenAI(
        api_key=OPENAI_API_KEY,
        temperature=0.3,
        model="gpt-4o-mini",
    ),
    qa_llm=ChatOpenAI(
        api_key=OPENAI_API_KEY,
        temperature=0.7,
        model="gpt-4o-mini",
    ),
    graph=graph,
    cypher_prompt=CYPHER_GENERATION_PROMPT,
    validate_cypher=True,
    return_direct=True,
    verbose=True,
    allow_dangerous_requests=True,
    return_intermediate_steps=True,
    top_k=100,
)


def _normalize_value(value):
    """Normalize datetime values to ISO format strings"""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_normalize_value(v) for v in value)
    return value


def strip_quotes(s):
    return s.strip("'").strip('"')


def get_schema_str():
    return (
        "Available Labels:\n"
        + "\n".join(f"- {label}" for label in sorted(schema_labels))
        + "\n\nAvailable Relationships:\n"
        + "\n".join(f"- {rel}" for rel in sorted(schema_relationships))
        + "\n"
    )


# ============== TRIPLE EXTRACTION ==============

def interpret_question(user_question: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Extract semantic triples from question (first attempt without schema)"""
    system_prompt = (
        "You are a Neo4j graph assistant. Your job is to: \n"
        "1. Rewrite vague or unclear user questions into clear, formal English.\n"
        "2. Extract **semantic triples** from the clarified question using Neo4j schema terms.\n\n"
        "Each triple must be in the format: (subject, predicate, object)\n"
        "- Use `?` for the variable being asked about.\n"
        "- Use `UNKNOWN` if an entity isn't specified explicitly.\n\n"
        "Output format MUST be:\n"
        "Rewritten: <clarified question>\n"
        "Triples:\n"
        "1. (subject, predicate, object)\n"
        "2. ...\n\n"
        "Be concise. Do NOT add explanation or extra commentary.\n"
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_question),
    ]

    response = interpreter_llm.invoke(messages).content.strip()

    lines = response.splitlines()
    rewritten = ""
    triples = []
    for line in lines:
        if line.startswith("Rewritten:"):
            rewritten = line.replace("Rewritten:", "").strip()
        elif re.match(r"^\d+\.", line):
            match = re.search(r"\(([^,]+), ([^,]+), ([^)]+)\)", line)
            if match:
                triples.append(tuple(strip_quotes(x.strip()) for x in match.groups()))
    return rewritten, triples


def interpret_question_with_schema(user_question: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Extract semantic triples with schema guidance"""
    system_prompt = f"""
You are a Neo4j graph assistant.

Your job is to:
1. Rewrite vague or ambiguous user questions into **clear, formal English**.
2. Extract semantic triples using **only the approved node labels and relationship types** below.

### STRICT INSTRUCTIONS ###
- All triples must follow the format: (SubjectLabel, RELATIONSHIP_TYPE, ObjectLabel)
- Subject and Object MUST be one of the valid node labels listed below.
- Relationship MUST be from the allowed relationship types.
- DO NOT use `?`, `UNKNOWN`, or invent new labels or relationships.
- If a required element is missing, leave out the triple entirely.
- If no valid triple can be made, just say: `Rewritten: <clarified question>` and no triples.

### Allowed Node Labels:
{schema_labels_str}

### Allowed Relationship Types:
{schema_rels_str}

Output format:
Rewritten: <clarified question>
Triples:
1. (<subject_label>, <relationship_type>, <object_label>)
2. ...

{entity_definitions}
""".strip()

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_question),
    ]

    response = interpreter_llm.invoke(messages).content.strip()

    lines = response.splitlines()
    rewritten = ""
    triples = []
    for line in lines:
        if line.startswith("Rewritten:"):
            rewritten = line.replace("Rewritten:", "").strip()
        elif re.match(r"^\d+\.", line):
            match = re.search(r"\(([^,]+), ([^,]+), ([^)]+)\)", line)
            if match:
                triples.append(tuple(strip_quotes(x.strip()) for x in match.groups()))
    return rewritten, triples


# ============== TRIPLE VERIFICATION ==============

def verify_triples(triples):
    """Verify triples against schema and find instance matches in Neo4j"""
    verified_triples = []
    instance_triples = []

    # Collect all literals from subject/object that are NOT labels or relationships
    literals = set()
    for s, p, o in triples:
        s_clean = strip_quotes(s)
        o_clean = strip_quotes(o)
        if s_clean not in schema_labels and s_clean not in schema_relationships:
            literals.add(s_clean)
        if o_clean not in schema_labels and o_clean not in schema_relationships:
            literals.add(o_clean)

    # Try matching each literal across schema labels + their properties
    for literal in literals:
        for label in schema_labels:
            properties_to_try = match_properties_map.get(label, ["name"])
            for prop in properties_to_try:
                try:
                    query = f"""
                    MATCH (n:{label})
                    WHERE toLower(toString(n.{prop})) = toLower(toString($name))
                    RETURN n LIMIT 1
                    """
                    result = graph.query(query, {"name": literal})
                    if result and result[0].get("n"):
                        triple = (literal, "instanceOf", label)
                        if triple not in instance_triples:
                            instance_triples.append(triple)
                            logging.info(f"Found instance: {triple}")
                except Exception as e:
                    logging.warning(f"Error checking {literal} on {label}.{prop}: {e}")

    # Validate triples against schema relationships
    for s, p, o in triples:
        if p in schema_relationships and s in schema_labels and o in schema_labels:
            verified_triples.append((s, p, o))

    return verified_triples, instance_triples


# ============== MAIN PIPELINE ==============

def get_results(question: str) -> dict:
    """Full RAG pipeline: extract triples, verify, generate Cypher, execute"""
    try:
        # Refresh schema
        graph.refresh_schema()

        # Triple extraction with retry
        MAX_ATTEMPTS = 3
        attempt = 0
        verified_triples = []
        instance_triples = []
        triples = []
        rewritten = ""

        while attempt < MAX_ATTEMPTS and not verified_triples:
            if attempt == 0:
                rewritten, triples = interpret_question(question)
            else:
                rewritten, triples = interpret_question_with_schema(question)

            # Verify triples
            temp_verified, temp_instance = verify_triples(triples)

            # Preserve instance_triples across retries
            for t in temp_instance:
                if t not in instance_triples:
                    instance_triples.append(t)

            if temp_verified:
                verified_triples = temp_verified

            attempt += 1

        if not verified_triples:
            verified_triples = triples

        logging.info(f"Rewritten: {rewritten}")
        logging.info(f"Verified Triples: {verified_triples}")
        logging.info(f"Instance Triples: {instance_triples}")

        # Build enhanced question with triples context
        triples_text = "\n".join([f"({s}, {r}, {o})" for (s, r, o) in verified_triples]) or "None"
        instance_text = "\n".join([f"({s}, {r}, {o})" for (s, r, o) in instance_triples]) or "None"

        enhanced_question = f"""
{question}

Rewritten Question:
{rewritten or question}

Verified Triples:
{triples_text}

Instance Triples:
{instance_text}
""".strip()

        # Invoke the chain
        chain_result = graph_chain.invoke(
            {"query": enhanced_question},
            return_only_outputs=True,
        )

        if chain_result is None:
            return {
                "cypher_query": "",
                "result": [],
                "verified_triples": verified_triples,
                "instance_triples": instance_triples,
                "error": "No result generated",
            }

        # Extract and clean Cypher query
        cypher_query = ""
        try:
            intermediate = chain_result.get("intermediate_steps", [{}])
            if intermediate:
                query_raw = intermediate[-1].get("query", "")
                if query_raw:
                    query = query_raw
                    query = re.sub(r"```cypher\s*", "", query, flags=re.IGNORECASE)
                    query = re.sub(r"```\s*", "", query)
                    query = re.sub(r"^\s*cypher\s+", "", query, flags=re.IGNORECASE)
                    query = query.rstrip(";").strip()
                    cypher_query = query
        except Exception as e:
            logging.warning(f"Failed to extract Cypher query: {e}")

        # Get result
        result = chain_result.get("result", [])
        result = _normalize_value(result)

        return {
            "cypher_query": cypher_query,
            "result": result if result else [],
            "verified_triples": verified_triples,
            "instance_triples": instance_triples,
            "error": None,
        }

    except Exception as e:
        logging.error(f"Error in get_results: {e}")
        return {
            "cypher_query": "",
            "result": [],
            "verified_triples": [],
            "instance_triples": [],
            "error": str(e),
        }


@app.post("/api/text2cypher")
def text2cypher():
    """Main endpoint for generating Cypher queries"""
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()

    if not question:
        return jsonify({"error": "question is required"}), 400

    print(f"\n[Flask] Question: {question[:80]}...")
    print(f"[Flask] Database: {NEO4J_DATABASE}")

    results = get_results(question=question)

    print(f"[Flask] Verified Triples: {results.get('verified_triples')}")
    print(f"[Flask] Instance Triples: {results.get('instance_triples')}")
    print(f"[Flask] Generated: {results.get('cypher_query', '')[:100]}...")

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


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    return jsonify(
        {
            "status": "ok",
            "database": NEO4J_DATABASE,
            "neo4j_uri": NEO4J_URI,
            "schema_labels": list(schema_labels),
            "schema_relationships": list(schema_relationships),
        }
    )


if __name__ == "__main__":
    print("=" * 60)
    print("Text2Cypher Flask App (with Triple Extraction)")
    print(f"Database: {NEO4J_DATABASE}")
    print(f"Neo4j URI: {NEO4J_URI}")
    print(f"Schema Labels: {len(schema_labels)}")
    print(f"Schema Relationships: {len(schema_relationships)}")
    print("=" * 60)
    print("\nStarting server on http://127.0.0.1:8000")
    print("Endpoints:")
    print("  POST /api/text2cypher - Generate Cypher with triples")
    print("  GET  /health - Health check")
    print("=" * 60)

    app.run(host="127.0.0.1", port=8000, debug=False)
