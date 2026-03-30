"""
Triple extraction and verification service.

Pipeline:
  1. interpret_question()              - rewrite + extract triples (no schema)
  2. interpret_question_with_schema()  - schema-guided triple extraction
  3. verify_triples()                  - structural validation only
  4. extract_triples_with_retry()      - retry loop (up to MAX_ATTEMPTS)
  5. build_enhanced_question()         - format question for Cypher chain
"""

from __future__ import annotations

import logging
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_neo4j import Neo4jGraph
from templates.entity_definitions import get_entity_definitions
from utils.helpers import strip_quotes

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


def _parse_triple_response(response: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Parse LLM response to extract rewritten question and triples."""
    rewritten = ""
    triples: list[tuple[str, str, str]] = []
    for line in response.splitlines():
        if line.startswith("Rewritten:"):
            rewritten = line.replace("Rewritten:", "").strip()
        elif re.match(r"^\d+\.", line):
            match = re.search(r"\(([^,]+),\s*([^,]+),\s*([^)]+)\)", line)
            if match:
                triples.append(
                    tuple(strip_quotes(x.strip()) for x in match.groups())
                )
    return rewritten, triples


def interpret_question(
    user_question: str,
    interpreter_llm,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]]]:
    """First-pass triple extraction — no schema constraints."""
    system_prompt = (
        "You are a Neo4j graph assistant. Your job is to:\n"
        "1. Rewrite vague or unclear user questions into clear, formal English.\n"
        "2. Extract **semantic triples** from the clarified question.\n\n"
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
    for turn in (conversation_history or [])[-3:]:
        messages.append(HumanMessage(content=turn["input"]))
        messages.append(AIMessage(content=turn["output"]))
    messages.append(HumanMessage(content=user_question))
    response = interpreter_llm.invoke(messages).content.strip()
    return _parse_triple_response(response)


def interpret_question_with_schema(
    user_question: str,
    interpreter_llm,
    schema_labels: set[str],
    schema_relationships: set[str],
    database: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]]]:
    """Schema-guided triple extraction."""
    labels_str = "\n".join(f"- {label}" for label in sorted(schema_labels))
    rels_str = "\n".join(f"- {r}" for r in sorted(schema_relationships))
    entity_defs = get_entity_definitions(database)

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

{entity_defs}""".strip()

    messages: list = [SystemMessage(content=system_prompt)]
    for turn in (conversation_history or [])[-3:]:
        messages.append(HumanMessage(content=turn["input"]))
        messages.append(AIMessage(content=turn["output"]))
    messages.append(HumanMessage(content=user_question))
    response = interpreter_llm.invoke(messages).content.strip()
    return _parse_triple_response(response)


def verify_triples(
    triples: list[tuple[str, str, str]],
    schema_labels: set[str],
    schema_relationships: set[str],
    graph: Neo4jGraph,
    database: str,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Structural verification only — no DB queries."""
    verified: list[tuple[str, str, str]] = []
    for s, p, o in triples:
        if p in schema_relationships and s in schema_labels and o in schema_labels:
            verified.append((s, p, o))
    logger.info("[TripleService] verify_triples -> verified=%d", len(verified))
    return verified, []


def extract_triples_with_retry(
    question: str,
    interpreter_llm,
    schema_labels: set[str],
    schema_relationships: set[str],
    graph: Neo4jGraph,
    database: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """
    Attempt 0: no-schema rewrite (fast, simple).
    Attempts 1+: schema-guided rewrite (uses labels/rels).
    Stops early if verified triples found.
    """
    verified_triples: list[tuple[str, str, str]] = []
    instance_triples: list[tuple[str, str, str]] = []
    rewritten = ""
    raw_triples: list[tuple[str, str, str]] = []

    for attempt in range(MAX_ATTEMPTS):
        logger.info("[TripleService] extract attempt %d/%d", attempt + 1, MAX_ATTEMPTS)
        if attempt == 0:
            try:
                rewritten, triples = interpret_question(
                    question, interpreter_llm, conversation_history
                )
            except Exception as e:
                logger.warning("[TripleService] LLM call failed attempt %d: %s", attempt + 1, e)
                continue
        else:
            try:
                rewritten, triples = interpret_question_with_schema(
                    question, interpreter_llm, schema_labels,
                    schema_relationships, database, conversation_history,
                )
            except Exception as e:
                logger.warning("[TripleService] LLM call failed attempt %d: %s", attempt + 1, e)
                continue
        raw_triples = triples
        temp_verified, _ = verify_triples(
            triples, schema_labels, schema_relationships, graph, database
        )
        if temp_verified:
            verified_triples = temp_verified
            logger.info(
                "[TripleService] Got %d verified triple(s) on attempt %d",
                len(verified_triples), attempt + 1,
            )
            break

    if not verified_triples:
        verified_triples = raw_triples

    logger.info(
        "[TripleService] Final -> rewritten=%r verified=%d",
        rewritten, len(verified_triples),
    )
    return rewritten, verified_triples, instance_triples


def build_enhanced_question(
    question: str,
    rewritten: str,
    verified_triples: list[tuple[str, str, str]],
    instance_triples: list[tuple[str, str, str]],
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """Build question string for the Cypher chain."""
    parts: list[str] = []
    if conversation_history:
        conversation_text = "\n".join(
            f"User: {msg['input']}\nBot: {msg['output']}"
            for msg in conversation_history[-3:]
        )
        if conversation_text.strip():
            parts.append(f"Conversation History:\n{conversation_text}")
    parts.append(f"Question: {question}")
    parts.append(f"Rewritten: {rewritten or question}")
    return "\n\n".join(parts)
