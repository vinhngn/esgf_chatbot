"""
Triple extraction and verification service.

Pipeline:
  1. interpret_question_with_schema()  - schema-guided rewrite + triple extraction
  2. verify_triples()                  - structural validation (label + rel checks)
  3. extract_triples_with_retry()      - retry loop (up to MAX_ATTEMPTS)
  4. build_enhanced_question()         - format question for Cypher chain
"""

from __future__ import annotations

import logging
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_neo4j import Neo4jGraph
from templates.entity_definitions import get_entity_definitions
from templates.match_properties_map import get_match_properties_map
from utils.helpers import strip_quotes

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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
                    tuple(strip_quotes(x.strip()) for x in match.groups())  # type: ignore[return-value]
                )

    return rewritten, triples


# ---------------------------------------------------------------------------
# Public extraction functions
# ---------------------------------------------------------------------------


def interpret_question_with_schema(
    user_question: str,
    interpreter_llm,
    schema_labels: set[str],
    schema_relationships: set[str],
    database: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]]]:
    """
    Schema-guided triple extraction.
    Only allows labels and relationship types that exist in Neo4j.

    Returns:
        (rewritten_question, triples)
    """
    labels_str = "\n".join(f"- {label}" for label in sorted(schema_labels))
    rels_str = "\n".join(f"- {r}" for r in sorted(schema_relationships))
    entity_defs = get_entity_definitions(database)

    system_prompt = f"""You are a Neo4j graph assistant.

Your job is to:
1. Rewrite the user question into **clear, formal English** using the schema terms below.
2. Extract semantic triples using **only the approved node labels and relationship types**.

### REWRITING RULES ###
- KEEP all specific names, values, numbers, and quoted strings EXACTLY as written.
  GOOD: "List the 5 most recent User nodes that FOLLOWS the User named 'Neo4j'"
  BAD:  "List the 5 most recent User nodes that FOLLOWS User" (lost 'Neo4j')
- Replace generic words with schema labels where appropriate.
- Do NOT change the meaning or add/remove conditions.

### TRIPLE RULES ###
- Format: (SubjectLabel, RELATIONSHIP_TYPE, ObjectLabel)
- Subject and Object MUST be from the node labels below.
- Relationship MUST be from the relationship types below.
- DO NOT invent labels or relationships.
- If no valid triple can be made, output only the rewritten question.

### Allowed Node Labels:
{labels_str}

### Allowed Relationship Types:
{rels_str}

Output format:
Rewritten: <rewritten question>
Triples:
1. (SubjectLabel, RELATIONSHIP_TYPE, ObjectLabel)

{entity_defs}""".strip()

    messages: list = [SystemMessage(content=system_prompt)]

    for turn in (conversation_history or [])[-3:]:
        messages.append(HumanMessage(content=turn["input"]))
        messages.append(AIMessage(content=turn["output"]))

    messages.append(HumanMessage(content=user_question))

    response = interpreter_llm.invoke(messages).content.strip()
    logger.debug(
        "[TripleService] interpret_question_with_schema raw response:\n%s", response
    )

    return _parse_triple_response(response)


def verify_triples(
    triples: list[tuple[str, str, str]],
    schema_labels: set[str],
    schema_relationships: set[str],
    graph: Neo4jGraph,
    database: str,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """
    Verify triples against the Neo4j schema.

    Only does structural validation (label + relationship checks).
    Instance matching (DB lookups) is skipped — it's expensive and
    the results don't affect Cypher generation.

    Returns:
        (verified_triples, instance_triples)
    """
    verified_triples: list[tuple[str, str, str]] = []
    instance_triples: list[tuple[str, str, str]] = []

    for s, p, o in triples:
        if p in schema_relationships and s in schema_labels and o in schema_labels:
            verified_triples.append((s, p, o))

    logger.info(
        "[TripleService] verify_triples -> verified=%d",
        len(verified_triples),
    )
    return verified_triples, instance_triples


# ---------------------------------------------------------------------------
# Full retry pipeline (public entry point)
# ---------------------------------------------------------------------------


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
    Triple extraction with retry (up to MAX_ATTEMPTS).
    Always uses schema-guided extraction.

    Returns:
        (rewritten, verified_triples, instance_triples)
    """
    verified_triples: list[tuple[str, str, str]] = []
    instance_triples: list[tuple[str, str, str]] = []
    rewritten = ""
    raw_triples: list[tuple[str, str, str]] = []

    for attempt in range(MAX_ATTEMPTS):
        logger.info("[TripleService] extract attempt %d/%d", attempt + 1, MAX_ATTEMPTS)

        rewritten, triples = interpret_question_with_schema(
            question,
            interpreter_llm,
            schema_labels,
            schema_relationships,
            database,
            conversation_history,
        )

        raw_triples = triples  # keep latest for fallback

        temp_verified, temp_instance = verify_triples(
            triples, schema_labels, schema_relationships, graph, database
        )

        if temp_verified:
            verified_triples = temp_verified
            instance_triples = temp_instance
            logger.info(
                "[TripleService] Got %d verified triple(s) on attempt %d",
                len(verified_triples),
                attempt + 1,
            )
            break

    # Fallback: use raw triples if verification never succeeded
    if not verified_triples:
        logger.warning(
            "[TripleService] No verified triples after %d attempts, "
            "using raw triples as fallback",
            MAX_ATTEMPTS,
        )
        verified_triples = raw_triples

    logger.info(
        "[TripleService] Final -> rewritten=%r verified=%d instance=%d",
        rewritten,
        len(verified_triples),
        len(instance_triples),
    )
    return rewritten, verified_triples, instance_triples


def build_enhanced_question(
    question: str,
    rewritten: str,
    verified_triples: list[tuple[str, str, str]],
    instance_triples: list[tuple[str, str, str]],
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """
    Build the enriched query string passed to the Cypher chain.

    Includes:
      - Recent conversation history (last 3 turns)
      - Original question
      - Rewritten (clarified) question

    Note:
      Triples are still extracted/returned for debugging and API consumers,
      but they are intentionally NOT injected into the chain question payload.
      This keeps the final Cypher prompt's {question} field free of triplet
      context for the T2C flow.
    """
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
