"""
Knowledge-Augmented Cypher Generation with Self-Correction.

Hybrid approach: LLM sinh Cypher trực tiếp (pre-trained strength)
+ CODE intelligence xung quanh (validate, fix, correct).

Flow:
  1. Schema Linker    — CODE: crop schema → giảm noise
  2. Knowledge Format  — CODE: inject ground truth vào prompt
  3. Cypher Generator  — LLM: sinh Cypher trực tiếp (1 call)
  4. Cypher Validator   — CODE: parse + check + auto-fix Cypher string
  5. Execute           — Neo4j
  6. Error Correction   — LLM: fix Cypher nếu execution fail (1 call)

LLM calls: 1-2 | Code intelligence: 4 steps
"""

from __future__ import annotations

import logging
import urllib.parse

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_neo4j import Neo4jGraph

from services import schema_linker, cypher_validator, error_taxonomy
from services.knowledge_base import load_knowledge, format_for_cypher_prompt
from templates.cypher_templates import get_cypher_template
from templates.entity_definitions import get_entity_definitions
from utils.helpers import clean_cypher_query

logger = logging.getLogger(__name__)


def run(
    question: str,
    graph: Neo4jGraph,
    schema_labels: set[str],
    schema_relationships: set[str],
    full_schema: str,
    database: str,
    llm,
) -> dict:
    knowledge = load_knowledge(database)
    entity_defs = get_entity_definitions(database)

    # --- Step 1: Schema Linking (CODE) ---
    link_result = schema_linker.link_schema(question, knowledge, full_schema)
    cropped_schema = link_result.cropped_schema or full_schema

    # --- Step 2: Build prompt with knowledge (CODE) ---
    template = get_cypher_template(database)
    knowledge_text = format_for_cypher_prompt(database)
    # Escape braces for the template
    knowledge_escaped = knowledge_text.replace("{", "{{").replace("}", "}}")
    prompt_text = template.replace("{schema}", cropped_schema)
    prompt_text = prompt_text.replace("{knowledge}", knowledge_escaped)
    # Unescape for final prompt (PromptTemplate already handled)
    prompt_text = prompt_text.replace("{{", "{").replace("}}", "}")
    prompt_text = prompt_text.replace("{question}", question)

    # --- Step 3: LLM generates Cypher directly (1 call) ---
    logger.info("[Pipeline] Generating Cypher for: %s", question[:60])
    try:
        response = llm.invoke([
            SystemMessage(content=prompt_text),
        ])
        raw_cypher = response.content.strip() if hasattr(response, 'content') else str(response).strip()
        cypher = clean_cypher_query(raw_cypher)
    except Exception as e:
        logger.error("[Pipeline] LLM generation failed: %s", e)
        return _error_result(str(e))

    if not cypher:
        return _error_result("LLM produced empty Cypher.")

    logger.info("[Pipeline] Generated: %s", cypher[:100])

    # --- Step 4: Validate + auto-fix Cypher (CODE) ---
    cypher, fixes = cypher_validator.validate_and_fix(cypher, knowledge)

    # --- Step 5: Execute ---
    result, error = _execute(graph, cypher)

    # --- Step 6: Self-correction if execution failed (1 LLM call) ---
    if error:
        logger.info("[Pipeline] Execution failed, attempting correction")
        classified = error_taxonomy.classify_neo4j_error(error)
        error_text = error_taxonomy.format_errors_for_llm(classified)

        try:
            fix_prompt = (
                f"The following Cypher query failed. Fix it.\n\n"
                f"Question: {question}\n\n"
                f"Schema:\n{cropped_schema}\n\n"
                f"Failed Cypher:\n{cypher}\n\n"
                f"Error:\n{error}\n\n"
                f"Error Classification:\n{error_text}\n\n"
                f"Output ONLY the fixed Cypher query. No explanation."
            )
            fix_response = llm.invoke([SystemMessage(content=fix_prompt)])
            fixed_cypher = clean_cypher_query(
                fix_response.content.strip() if hasattr(fix_response, 'content') else str(fix_response).strip()
            )
            if fixed_cypher:
                fixed_cypher, fix2 = cypher_validator.validate_and_fix(fixed_cypher, knowledge)
                fixes.extend(fix2)
                cypher = fixed_cypher
                result, error = _execute(graph, cypher)
        except Exception as e:
            logger.error("[Pipeline] Correction failed: %s", e)

    encoded = urllib.parse.quote(cypher) if cypher else ""

    logger.info(
        "[Pipeline] Done: rows=%d, fixes=%d, error=%s",
        len(result) if result else 0, len(fixes),
        error[:60] if error else "none",
    )

    return {
        "cypher_query": cypher,
        "encoded_query": encoded,
        "result": result or [],
        "error": error,
        "fixes": fixes,
    }


def _execute(graph: Neo4jGraph, cypher: str) -> tuple[list | None, str | None]:
    try:
        result = graph.query(cypher)
        return result if result else [], None
    except Exception as e:
        return None, str(e)


def _error_result(msg: str) -> dict:
    return {"cypher_query": "", "encoded_query": "", "result": [], "error": msg, "fixes": []}
