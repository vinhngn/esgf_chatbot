"""
Core utilities for Text2Cypher - shared between Flask and Streamlit apps
No Streamlit dependencies here!
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time

from langchain.schema import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_community.graphs import Neo4jGraph

from templates.entity_definitions import entity_definitions
from templates.match_properties_map import match_properties_map


def normalize_value(value):
    """Normalize datetime values to ISO format strings"""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(normalize_value(v) for v in value)
    return value


def strip_quotes(s: str) -> str:
    return s.strip("'").strip('"')


def parse_schema(schema_text: str) -> tuple[set, set]:
    """Parse Neo4j schema text to extract labels and relationships"""
    labels, relationships = set(), set()
    for line in schema_text.splitlines():
        for label in re.findall(r"\(:([A-Za-z0-9_]+)\)", line):
            labels.add(label)
        for rel in re.findall(r"\[:([A-Za-z0-9_]+)\]", line):
            relationships.add(rel)
    return labels, relationships


def clean_cypher_query(query_raw: str) -> str:
    """Clean up generated Cypher query"""
    query = re.sub(r"```cypher\s*", "", query_raw, flags=re.IGNORECASE)
    query = re.sub(r"```\s*", "", query)
    query = re.sub(r"^\s*cypher\s+", "", query, flags=re.IGNORECASE)
    return query.rstrip(";").strip()


def _parse_triple_response(response: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Parse LLM response to extract rewritten question and triples"""
    rewritten, triples = "", []
    for line in response.splitlines():
        if line.startswith("Rewritten:"):
            rewritten = line.replace("Rewritten:", "").strip()
        elif re.match(r"^\d+\.", line):
            match = re.search(r"\(([^,]+), ([^,]+), ([^)]+)\)", line)
            if match:
                triples.append(tuple(strip_quotes(x.strip()) for x in match.groups()))
    return rewritten, triples


def interpret_question(
    user_question: str,
    interpreter_llm: ChatOpenAI,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]]]:
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

    messages: list = [SystemMessage(content=system_prompt)]
    if conversation_history:
        for turn in conversation_history[-3:]:
            messages.append(HumanMessage(content=turn["input"]))
            messages.append(AIMessage(content=turn["output"]))
    messages.append(HumanMessage(content=user_question))

    return _parse_triple_response(interpreter_llm.invoke(messages).content.strip())


def interpret_question_with_schema(
    user_question: str,
    interpreter_llm: ChatOpenAI,
    schema_labels: set,
    schema_relationships: set,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]]]:
    """Extract semantic triples with schema guidance"""
    labels_str = "\n".join(f"- {l}" for l in sorted(schema_labels))
    rels_str = "\n".join(f"- {r}" for r in sorted(schema_relationships))

    system_prompt = f"""You are a Neo4j graph assistant.

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
{labels_str}

### Allowed Relationship Types:
{rels_str}

Output format:
Rewritten: <clarified question>
Triples:
1. (<subject_label>, <relationship_type>, <object_label>)
2. ...

{entity_definitions}"""

    messages: list = [SystemMessage(content=system_prompt)]
    if conversation_history:
        for turn in conversation_history[-3:]:
            messages.append(HumanMessage(content=turn["input"]))
            messages.append(AIMessage(content=turn["output"]))
    messages.append(HumanMessage(content=user_question))

    return _parse_triple_response(interpreter_llm.invoke(messages).content.strip())


def verify_triples(
    triples: list[tuple[str, str, str]],
    schema_labels: set,
    schema_relationships: set,
    graph: Neo4jGraph,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Verify triples against schema and find instance matches in Neo4j"""
    verified_triples, instance_triples = [], []

    # Collect literals (not labels/relationships)
    literals = set()
    for s, p, o in triples:
        for val in [strip_quotes(s), strip_quotes(o)]:
            if val not in schema_labels and val not in schema_relationships:
                literals.add(val)

    # Match literals to instances
    for literal in literals:
        for label in schema_labels:
            for prop in match_properties_map.get(label, ["name"]):
                try:
                    query = f"MATCH (n:{label}) WHERE toLower(toString(n.{prop})) = toLower($name) RETURN n LIMIT 1"
                    if graph.query(query, {"name": literal}):
                        triple = (literal, "instanceOf", label)
                        if triple not in instance_triples:
                            instance_triples.append(triple)
                            logging.info(f"Found instance: {triple}")
                except Exception as e:
                    logging.warning(f"Error checking {literal} on {label}.{prop}: {e}")

    # Validate schema triples
    for s, p, o in triples:
        if p in schema_relationships and s in schema_labels and o in schema_labels:
            verified_triples.append((s, p, o))

    return verified_triples, instance_triples
