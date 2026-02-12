"""
Triple extraction and verification service.
No Streamlit dependency.

FIX: Consolidated duplicate code from rag_agent.py and core.py into one place.
FIX: _parse_triple_response is now a single shared function.
"""
from __future__ import annotations

import logging
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from langchain_community.graphs import Neo4jGraph

from templates.entity_definitions import get_entity_definitions
from templates.match_properties_map import get_match_properties_map
from utils.helpers import strip_quotes

logger = logging.getLogger(__name__)


def _parse_triple_response(response: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Parse LLM response to extract rewritten question and triples."""
    rewritten, triples = "", []
    for line in response.splitlines():
        if line.startswith("Rewritten:"):
            rewritten = line.replace("Rewritten:", "").strip()
        elif re.match(r"^\d+\.", line):
            match = re.search(r"\(([^,]+), ([^,]+), ([^)]+)\)", line)
            if match:
                triples.append(tuple(strip_quotes(x.strip()) for x in match.groups()))
    return rewritten, triples


def _build_messages(
    system_prompt: str,
    user_question: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> list:
    """Build LangChain message list with optional conversation history."""
    messages: list = [SystemMessage(content=system_prompt)]
    if conversation_history:
        for turn in conversation_history[-3:]:
            messages.append(HumanMessage(content=turn["input"]))
            messages.append(AIMessage(content=turn["output"]))
    messages.append(HumanMessage(content=user_question))
    return messages


def interpret_question(
    user_question: str,
    interpreter_llm: ChatOpenAI,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]]]:
    """Extract semantic triples from question (first attempt without schema)."""
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
    messages = _build_messages(system_prompt, user_question, conversation_history)
    response = interpreter_llm.invoke(messages).content.strip()
    return _parse_triple_response(response)


def interpret_question_with_schema(
    user_question: str,
    interpreter_llm: ChatOpenAI,
    schema_labels: set[str],
    schema_relationships: set[str],
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]]]:
    """Extract semantic triples with schema guidance (retry with more context)."""
    labels_str = "\n".join(f"- {l}" for l in sorted(schema_labels))
    rels_str = "\n".join(f"- {r}" for r in sorted(schema_relationships))
    entity_defs = get_entity_definitions()

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

{entity_defs}"""

    messages = _build_messages(system_prompt, user_question, conversation_history)
    response = interpreter_llm.invoke(messages).content.strip()
    return _parse_triple_response(response)


def verify_triples(
    triples: list[tuple[str, str, str]],
    schema_labels: set[str],
    schema_relationships: set[str],
    graph: Neo4jGraph,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """
    Verify triples against schema and find instance matches in Neo4j.
    Returns (verified_triples, instance_triples).
    """
    verified_triples, instance_triples = [], []
    properties_map = get_match_properties_map()

    # Collect literals (values that are not labels or relationships)
    literals = set()
    for s, p, o in triples:
        for val in [strip_quotes(s), strip_quotes(o)]:
            if val not in schema_labels and val not in schema_relationships:
                literals.add(val)

    # Batch match literals to Neo4j instances
    if literals:
        names_lower = [lit.lower() for lit in literals]
        names_map = {lit.lower(): lit for lit in literals}

        for label in schema_labels:
            for prop in properties_map.get(label, ["name"]):
                try:
                    query = (
                        f"MATCH (n:{label}) "
                        f"WHERE toLower(toString(n.{prop})) IN $names "
                        f"RETURN toLower(toString(n.{prop})) AS matched"
                    )
                    results = graph.query(query, {"names": names_lower})
                    for row in results:
                        original = names_map.get(row["matched"])
                        if original:
                            triple = (original, "instanceOf", label)
                            if triple not in instance_triples:
                                instance_triples.append(triple)
                                logger.info(f"Found instance: {triple}")
                except Exception as e:
                    logger.warning(f"Error checking {label}.{prop}: {e}")

    # Validate schema triples
    for s, p, o in triples:
        if p in schema_relationships and s in schema_labels and o in schema_labels:
            verified_triples.append((s, p, o))

    return verified_triples, instance_triples
