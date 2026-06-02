"""Retrieval-grounded Text-to-Cypher execution with universal schema grounding."""
from __future__ import annotations
import logging
from config import get_settings
from models.graph import get_graph, maybe_refresh_schema
from models.llm import get_cypher_llm, get_grounding_llm
from templates.cypher_templates import get_prompt_sections
from utils.helpers import (
    clean_cypher_query,
    repair_northwind_order_line_properties,
    repair_northwind_projection_and_metrics,
    strip_noisy_return_properties,
    rewrite_bare_node_returns,
)

logger = logging.getLogger(__name__)
MAX_CYPHER_RETRIES = 2


def _build_coder_prompt(
    *,
    schema: str,
    domain_template: str,
    question: str,
    current_cypher: str,
    last_error: str | None,
) -> str:
    prompt = (
        "Output only one raw Cypher query.\n\n"
        f"=== SCHEMA ===\n{schema}\n\n"
        f"{domain_template}\n\n"
        "=== USER INPUT ===\n"
        f"{question}\n"
    )
    if last_error and current_cypher:
        prompt += (
            f"\nPrevious query:\n{current_cypher}\n"
            f"Error/feedback:\n{last_error}\n"
            "Fix the query while preserving the closest retrieved example's structure."
        )
    return prompt


def _get_grounded_schema(question: str, runtime_schema: str) -> tuple[str, dict]:
    """Get grounded schema via LLM schema linker. Falls back to full schema on failure."""
    try:
        from services.universal.schema_grounder import build_grounded_schema
        grounding_llm = get_grounding_llm()
        db_name = get_settings().database_name
        result = build_grounded_schema(
            question=question,
            runtime_schema=runtime_schema,
            db_name=db_name,
            llm=grounding_llm,
        )
        grounded_text = result.get("schema_text", "")
        debug = result.get("debug", {})
        if grounded_text and len(grounded_text) > 50:
            logger.info("[Chain] Using grounded subschema (%d chars)", len(grounded_text))
            return grounded_text, debug
        logger.warning("[Chain] Grounded schema too short, falling back to full schema")
        return runtime_schema, debug
    except Exception as exc:
        logger.warning("[Chain] Schema grounding failed, using full schema: %s", exc)
        return runtime_schema, {}


def invoke_chain(question: str, schema: str = "") -> dict | str:
    maybe_refresh_schema()
    graph = get_graph()
    llm = get_cypher_llm()

    full_schema = schema or graph.get_schema
    grounding_debug = {}

    # Use grounded schema when no explicit schema is provided
    if not schema:
        db_schema, grounding_debug = _get_grounded_schema(question, full_schema)
    else:
        db_schema = full_schema

    domain_template = get_prompt_sections(
        question=question,
        original_question=question,
    )

    current_cypher = ""
    last_error: str | None = None

    for attempt in range(1 + MAX_CYPHER_RETRIES):
        logger.info("[Synthesizer] Attempt %d/%d", attempt + 1, 1 + MAX_CYPHER_RETRIES)
        prompt = _build_coder_prompt(
            schema=db_schema,
            domain_template=domain_template,
            question=question,
            current_cypher=current_cypher,
            last_error=last_error,
        )
        response = llm.invoke(prompt)
        database_name = get_settings().database_name
        current_cypher = clean_cypher_query(response.content.strip())
        current_cypher = repair_northwind_order_line_properties(current_cypher)
        if database_name == "northwind":
            current_cypher = rewrite_bare_node_returns(current_cypher, question=question)
        current_cypher = repair_northwind_projection_and_metrics(current_cypher, question=question)
        current_cypher = strip_noisy_return_properties(current_cypher)

        try:
            logger.info("[Cypher] Validating syntax via EXPLAIN...")
            graph.query(f"EXPLAIN {current_cypher}")

            logger.info("[Cypher] Executing: %s", current_cypher)
            raw_result = graph.query(current_cypher)

            return {
                "query": current_cypher,
                "result": raw_result,
                "intermediate_steps": [{"query": current_cypher, "grounding": grounding_debug}],
            }
        except Exception as exc:
            error_msg = str(exc)
            logger.warning("[Cypher] Validation/execution error: %s", error_msg)
            if attempt < MAX_CYPHER_RETRIES:
                last_error = f"Neo4j validation/execution error: {error_msg}"
                continue
            return f"CYPHER_ERROR: {error_msg}"

    return {
        "query": current_cypher,
        "result": [],
        "intermediate_steps": [{"query": current_cypher, "grounding": grounding_debug}],
    }
