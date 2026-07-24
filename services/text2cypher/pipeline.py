"""Retrieval-grounded Text-to-Cypher execution with universal schema grounding."""

from __future__ import annotations

import logging
import os

from config import get_settings
from models.graph import get_graph, maybe_refresh_schema
from services.profile_analyzer.store import profile_path
from services.text2cypher.context_builder import (
    get_entity_resolution_context as _get_entity_resolution_context,
)
from services.text2cypher.context_builder import (
    get_grounded_schema as _get_grounded_schema,
)
from services.text2cypher.context_builder import (
    get_learned_profile_context as _get_learned_profile_context,
)
from services.text2cypher.context_builder import (
    get_runtime_property_context as _get_runtime_property_context,
)
from services.text2cypher.context_builder import (
    has_profile_for_current_database as _has_profile_for_current_database,
)
from services.text2cypher.context_builder import (
    schema_grounding_mode as _schema_grounding_mode,
)
from services.text2cypher.contract_repair import (
    _ensure_primary_target_return,
    _ensure_rank_entity_projection,
    _return_shape_from_plan,
)
from services.text2cypher.domain_repair import apply_domain_repairs
from services.text2cypher.execution import (
    cap_execution_cypher as _cap_execution_cypher,
)
from services.text2cypher.execution import (
    execution_row_cap as _execution_row_cap,
)
from services.text2cypher.execution import (
    result_quality_feedback as _result_quality_feedback,
)
from services.text2cypher.execution import (
    result_summary as _result_summary,
)
from services.text2cypher.postprocessing import (
    ensure_rank_order as _ensure_rank_order,
)
from services.text2cypher.postprocessing import (
    ensure_requested_limit as _ensure_requested_limit,
)
from services.text2cypher.postprocessing import (
    repair_backticked_label_with_inline_map as _repair_backticked_label_with_inline_map,
)
from services.text2cypher.profile_recipes import (
    _exact_profile_cypher,
    _profile_first_cypher,
    _profile_first_enabled,
)
from services.text2cypher.prompting import build_coder_messages as _build_coder_messages
from services.text2cypher.structure_repair import (
    _ensure_ordered_property_not_null,
    _preserve_same_entity_scaffold,
)
from templates.cypher_templates import get_prompt_sections
from utils.helpers import (
    clean_cypher_query,
    rewrite_bare_node_returns,
    strip_noisy_return_properties,
)
from utils.pipeline_trace import new_trace_id, trace_event, verbose_trace_enabled

logger = logging.getLogger(__name__)
DEFAULT_CYPHER_RETRIES = 2


def _cypher_retries() -> int:
    try:
        return max(0, int(os.getenv("T2C_CYPHER_RETRIES", str(DEFAULT_CYPHER_RETRIES))))
    except ValueError:
        return DEFAULT_CYPHER_RETRIES


def invoke_chain(
    question: str,
    schema: str = "",
    trace_id: str | None = None,
) -> dict | str:
    from models.llm import get_cypher_llm

    trace_id = trace_id or new_trace_id()
    if schema and _profile_first_enabled():
        profile_cypher = _exact_profile_cypher(question)
        if not profile_cypher:
            learned_context = _get_learned_profile_context(question, trace_id)
            profile_cypher = _profile_first_cypher(question, learned_context)
        if profile_cypher:
            trace_event(
                logger,
                trace_id,
                "CHAIN-00",
                "Profile-first exact recipe hit; skip graph/LLM synthesis for evaluator request",
                {"cypher": profile_cypher},
            )
            return {
                "query": profile_cypher,
                "result": [],
                "trace_id": trace_id,
                "intermediate_steps": [
                    {
                        "query": profile_cypher,
                        "grounding": {"mode": "profile_first_exact"},
                        "trace_id": trace_id,
                    }
                ],
            }

    maybe_refresh_schema()
    graph = get_graph()
    llm = get_cypher_llm()

    full_schema = schema or graph.get_schema
    grounding_debug = {}

    trace_event(
        logger,
        trace_id,
        "CHAIN-01",
        "Text-to-Cypher request received",
        {
            "database": get_settings().profile_database_name,
            "physical_database": get_settings().database_name,
            "question": question,
            "schema_source": "request/CSV" if schema else "Neo4j runtime",
            "schema_chars": len(full_schema),
            "verbose_trace": verbose_trace_enabled(),
        },
    )

    # Use grounded schema when no explicit schema is provided. If an analyzed
    # profile exists, skip the extra grounding LLM by default; the profile
    # already carries schema paths, recipes, examples, and value hints.
    profile_available = _has_profile_for_current_database()
    if not schema and _schema_grounding_mode() != "llm" and profile_available:
        db_schema = full_schema
        trace_event(
            logger,
            trace_id,
            "CHAIN-02",
            "Profile exists: skip schema-grounding LLM and use runtime schema plus profile context",
            {
                "profile_path": str(profile_path(get_settings().profile_database_name)),
                "schema_grounding_mode": _schema_grounding_mode(),
            },
        )
    elif not schema:
        trace_event(
            logger,
            trace_id,
            "CHAIN-02",
            "No explicit schema supplied: call the schema-grounding LLM",
        )
        db_schema, grounding_debug = _get_grounded_schema(
            question,
            full_schema,
            trace_id,
        )
    else:
        db_schema = full_schema
        trace_event(
            logger,
            trace_id,
            "CHAIN-02",
            "Explicit schema supplied: schema-grounding LLM is bypassed",
            {
                "reason": "The evaluator/request already supplied schema text",
                "schema_chars": len(schema),
            },
        )

    domain_template = (
        ""
        if profile_available
        else get_prompt_sections(
            question=question,
            original_question=question,
        )
    )
    trace_event(
        logger,
        trace_id,
        "CHAIN-04",
        (
            "Profile available: omit legacy domain prompt"
            if profile_available
            else "No profile available: assemble fallback domain context"
        ),
        domain_template or {"profile_controls_context": True},
        verbose_only=True,
    )
    learned_context = _get_learned_profile_context(question, trace_id)
    trace_event(
        logger,
        trace_id,
        "CHAIN-04C",
        "Learned profile context inserted before user input",
        learned_context,
        verbose_only=True,
    )
    entity_context = _get_entity_resolution_context(
        question=question,
        graph=graph,
        trace_id=trace_id,
    )
    trace_event(
        logger,
        trace_id,
        "CHAIN-04F",
        "Indexed entity/literal context inserted before user input",
        entity_context,
        verbose_only=True,
    )
    property_context = _get_runtime_property_context(
        question=question,
        runtime_schema=full_schema,
        learned_context=f"{learned_context}\n\n{entity_context}",
        graph=graph,
        trace_id=trace_id,
    )

    current_cypher = ""
    last_error: str | None = None

    max_cypher_retries = _cypher_retries()
    for attempt in range(1 + max_cypher_retries):
        logger.info("[Synthesizer] Attempt %d/%d", attempt + 1, 1 + max_cypher_retries)
        messages = _build_coder_messages(
            schema=db_schema,
            domain_template=domain_template,
            learned_context=learned_context,
            evidence_context=f"{entity_context}\n\n{property_context}",
            question=question,
            current_cypher=current_cypher,
            last_error=last_error,
        )
        trace_event(
            logger,
            trace_id,
            "CHAIN-05",
            f"Role-separated prompt sent to the Cypher LLM (attempt {attempt + 1})",
            {
                "system": messages[0].content,
                "user": messages[1].content,
            },
            verbose_only=True,
        )
        try:
            response = llm.invoke(messages)
        except Exception as exc:
            trace_event(
                logger,
                trace_id,
                "CHAIN-ERROR",
                "Cypher LLM request failed",
                {
                    "attempt": attempt + 1,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise
        raw_cypher_response = response.content.strip()
        trace_event(
            logger,
            trace_id,
            "CHAIN-06",
            "Raw return from the Cypher LLM: one text string",
            {
                "return_type": type(response.content).__name__,
                "raw_text": raw_cypher_response,
            },
        )
        current_cypher = clean_cypher_query(raw_cypher_response)
        current_cypher = _repair_backticked_label_with_inline_map(current_cypher)
        current_cypher = rewrite_bare_node_returns(current_cypher, question=question)
        current_cypher = apply_domain_repairs(
            current_cypher,
            question=question,
            database=get_settings().profile_database_name,
        )
        current_cypher = _ensure_rank_order(current_cypher, question)
        current_cypher = _ensure_requested_limit(current_cypher, question)
        current_cypher = _ensure_ordered_property_not_null(current_cypher)
        current_cypher = _ensure_rank_entity_projection(current_cypher, question, full_schema)
        before_target_return_repair = current_cypher
        current_cypher = _ensure_primary_target_return(current_cypher, learned_context, full_schema)
        if current_cypher != before_target_return_repair:
            trace_event(
                logger,
                trace_id,
                "CHAIN-07B",
                "Return contract checker aligned projection with primary target label",
                {
                    "before": before_target_return_repair,
                    "after": current_cypher,
                },
            )
        before_return_shape_repair = current_cypher
        current_cypher = _return_shape_from_plan(current_cypher, learned_context)
        if current_cypher != before_return_shape_repair:
            trace_event(
                logger,
                trace_id,
                "CHAIN-07C",
                "Return shape contract aligned projection with selected scaffold",
                {
                    "before": before_return_shape_repair,
                    "after": current_cypher,
                },
            )
        before_same_entity_repair = current_cypher
        current_cypher = _preserve_same_entity_scaffold(current_cypher, learned_context, question)
        if current_cypher != before_same_entity_repair:
            trace_event(
                logger,
                trace_id,
                "CHAIN-07D",
                "Same-entity graph pattern preserved selected multi-MATCH scaffold",
                {
                    "before": before_same_entity_repair,
                    "after": current_cypher,
                },
            )
        current_cypher = strip_noisy_return_properties(current_cypher)
        trace_event(
            logger,
            trace_id,
            "CHAIN-07",
            "Final cleaned Cypher before Neo4j validation",
            current_cypher,
        )

        if os.getenv("T2C_GENERATE_ONLY", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            trace_event(
                logger,
                trace_id,
                "CHAIN-08",
                "Generate-only mode enabled; skip Neo4j EXPLAIN/execution and let evaluator execute",
            )
            return {
                "query": current_cypher,
                "result": [],
                "trace_id": trace_id,
                "intermediate_steps": [
                    {
                        "query": current_cypher,
                        "grounding": grounding_debug,
                        "trace_id": trace_id,
                    }
                ],
            }

        try:
            logger.info("[Cypher] Validating syntax via EXPLAIN...")
            graph.query(f"EXPLAIN {current_cypher}")
            trace_event(
                logger,
                trace_id,
                "CHAIN-08",
                "Neo4j EXPLAIN accepted the Cypher syntax",
            )

            execution_cypher, execution_was_capped = _cap_execution_cypher(current_cypher)
            if execution_was_capped:
                trace_event(
                    logger,
                    trace_id,
                    "CHAIN-08B",
                    "API-side execution was capped to protect memory; generated Cypher is unchanged",
                    {
                        "generated_cypher": current_cypher,
                        "execution_cypher": execution_cypher,
                        "row_cap": _execution_row_cap(),
                    },
                )

            logger.info("[Cypher] Executing: %s", execution_cypher)
            raw_result = graph.query(execution_cypher)
            trace_event(
                logger,
                trace_id,
                "CHAIN-09",
                "Neo4j execution completed",
                _result_summary(raw_result),
            )

            quality_feedback = _result_quality_feedback(raw_result)
            if quality_feedback and attempt < max_cypher_retries:
                trace_event(
                    logger,
                    trace_id,
                    "CHAIN-10",
                    "Neo4j returned low-quality projected rows; retry with feedback",
                    {
                        "feedback": quality_feedback,
                        "sample": _result_summary(raw_result),
                    },
                )
                last_error = quality_feedback
                continue

            return {
                "query": current_cypher,
                "result": raw_result,
                "trace_id": trace_id,
                "intermediate_steps": [
                    {
                        "query": current_cypher,
                        "grounding": grounding_debug,
                        "trace_id": trace_id,
                    }
                ],
            }
        except Exception as exc:
            error_msg = str(exc)
            logger.warning("[Cypher] Validation/execution error: %s", error_msg)
            trace_event(
                logger,
                trace_id,
                "CHAIN-08",
                f"Neo4j validation/execution failed on attempt {attempt + 1}",
                {
                    "error": error_msg,
                    "will_retry": attempt < max_cypher_retries,
                },
            )
            if attempt < max_cypher_retries:
                last_error = f"Neo4j validation/execution error: {error_msg}"
                continue
            return f"CYPHER_ERROR: {error_msg}"

    return {
        "query": current_cypher,
        "result": [],
        "trace_id": trace_id,
        "intermediate_steps": [
            {
                "query": current_cypher,
                "grounding": grounding_debug,
                "trace_id": trace_id,
            }
        ],
    }
