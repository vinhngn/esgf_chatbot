"""Retrieval-grounded Text-to-Cypher execution with universal schema grounding."""
from __future__ import annotations
import logging
import os
import re
from typing import Any
from config import get_settings
from models.graph import get_graph, maybe_refresh_schema
from models.llm import get_cypher_llm, get_grounding_llm
from templates.cypher_templates import get_prompt_sections
from services.profile_analyzer.context import (
    format_profile_context,
    load_profile,
    select_profile_context,
)
from services.profile_analyzer.store import profile_build_command, profile_path
from services.universal.property_context import build_runtime_property_context
from utils.helpers import (
    clean_cypher_query,
    repair_northwind_order_line_properties,
    repair_northwind_projection_and_metrics,
    strip_noisy_return_properties,
    rewrite_bare_node_returns,
)
from utils.pipeline_trace import new_trace_id, trace_event, verbose_trace_enabled

logger = logging.getLogger(__name__)
MAX_CYPHER_RETRIES = 2
DEFAULT_EXECUTION_ROW_CAP = 100


def _execution_row_cap() -> int:
    try:
        return int(os.getenv("T2C_API_EXECUTION_ROW_CAP", str(DEFAULT_EXECUTION_ROW_CAP)))
    except ValueError:
        return DEFAULT_EXECUTION_ROW_CAP


def _limit_value(cypher: str) -> int | None:
    match = re.search(r"(?is)\bLIMIT\s+(\d+)\s*$", cypher or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _cap_execution_cypher(cypher: str) -> tuple[str, bool]:
    """Cap API-side execution rows without changing the generated Cypher."""
    cap = _execution_row_cap()
    if cap <= 0 or not cypher:
        return cypher, False
    existing_limit = _limit_value(cypher)
    if existing_limit is not None and existing_limit <= cap:
        return cypher, False
    if existing_limit is not None:
        capped = re.sub(r"(?is)\bLIMIT\s+\d+\s*$", f"LIMIT {cap}", cypher.strip())
        return capped, capped != cypher
    return f"{cypher.rstrip()} LIMIT {cap}", True


def _requested_limit(question: str) -> int | None:
    patterns = [
        r"\b(?:top|first|last|list|show|return)\s+(\d+)\b",
        r"\b(\d+)\s+(?:nodes?|rows?|items?|records?|tweets?|movies?|users?|products?|orders?|customers?|suppliers?|hashtags?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, question or "", flags=re.IGNORECASE)
        if match:
            try:
                value = int(match.group(1))
            except ValueError:
                continue
            if 0 < value <= 1000:
                return value
    return None


def _ensure_requested_limit(cypher: str, question: str) -> str:
    if not cypher or re.search(r"(?i)\bLIMIT\s+\d+\b", cypher):
        return cypher
    limit = _requested_limit(question)
    if limit is None:
        return cypher
    return f"{cypher.rstrip()} LIMIT {limit}"


def _ensure_rank_order(cypher: str, question: str) -> str:
    if not cypher or re.search(r"(?i)\bORDER\s+BY\b", cypher):
        return cypher
    lowered = (question or "").lower()
    if not re.search(r"\b(top|highest|largest|most|lowest|least|smallest)\b", lowered):
        return cypher

    direction = "ASC" if re.search(r"\b(lowest|least|smallest)\b", lowered) else "DESC"
    return_body = re.search(r"(?is)\bRETURN\b\s+(.*?)(?:\bLIMIT\b|$)", cypher)
    if not return_body:
        return cypher
    candidates = re.findall(
        r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b",
        return_body.group(1),
    )
    if not candidates:
        return cypher

    question_tokens = set(re.findall(r"[a-z0-9]+", lowered))
    selected = ""
    for candidate in candidates:
        prop = candidate.rsplit(".", 1)[1].lower()
        if prop in question_tokens:
            selected = candidate
            break
    if not selected and len(candidates) > 1:
        selected = candidates[-1]
    if not selected:
        return cypher

    limit_match = re.search(r"(?is)\s+LIMIT\s+\d+\s*$", cypher)
    if limit_match:
        prefix = cypher[: limit_match.start()].rstrip()
        suffix = cypher[limit_match.start():]
        return f"{prefix} ORDER BY {selected} {direction}{suffix}"
    return f"{cypher.rstrip()} ORDER BY {selected} {direction}"


def _ensure_ordered_property_not_null(cypher: str) -> str:
    if not cypher or not re.search(r"(?i)\bORDER\s+BY\b", cypher):
        return cypher
    ordered_props = [
        item
        for item in re.findall(
            r"(?is)\bORDER\s+BY\b\s+(.*?)(?:\bSKIP\b|\bLIMIT\b|$)",
            cypher,
        )
        for item in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b", item)
    ]
    if not ordered_props:
        return cypher
    guards = [
        f"{prop} IS NOT NULL"
        for prop in dict.fromkeys(ordered_props)
        if not re.search(rf"(?i)\b{re.escape(prop)}\s+IS\s+NOT\s+NULL\b", cypher)
    ]
    if not guards:
        return cypher

    return_match = re.search(r"(?i)\bRETURN\b", cypher)
    if not return_match:
        return cypher
    before_return = cypher[: return_match.start()].rstrip()
    after_return = cypher[return_match.start():]
    where_matches = list(re.finditer(r"(?i)\bWHERE\b", before_return))
    guard_text = " AND ".join(guards)
    if where_matches:
        last_where = where_matches[-1]
        before_return = (
            before_return[: last_where.end()]
            + " "
            + before_return[last_where.end():].strip()
            + f" AND {guard_text}"
        )
    else:
        before_return = f"{before_return} WHERE {guard_text}"
    return f"{before_return} {after_return}"


def _build_coder_prompt(
    *,
    schema: str,
    domain_template: str,
    learned_context: str,
    question: str,
    current_cypher: str,
    last_error: str | None,
) -> str:
    prompt = (
        "Output only one raw Cypher query.\n\n"
        f"=== SCHEMA ===\n{schema}\n\n"
        f"{domain_template}\n\n"
        f"{learned_context}\n\n"
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


def _get_learned_profile_context(question: str, trace_id: str) -> str:
    """Load auto-generated query/data profile context when available."""
    settings = get_settings()
    path = profile_path(settings.database_name)
    if not path.exists():
        trace_event(
            logger,
            trace_id,
            "CHAIN-04B",
            "Learned profile context unavailable; continue without it",
            {
                "database": settings.database_name,
                "profile_path": str(path),
                "build_command": profile_build_command(settings.database_name),
            },
        )
        return (
            "=== LEARNED DATA/QUERY PROFILE CONTEXT ===\n"
            "No learned profile is available for this database.\n"
            f"Profile expected at: {path}\n"
            f"Build command: {profile_build_command(settings.database_name)}"
        )
    try:
        profile = load_profile(path)
        context = select_profile_context(question, profile, top_k=5)
        formatted = format_profile_context(context)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04B",
            "Learned profile context selected from analyzed benchmark/query data",
            {
                "profile_path": str(path),
                "selected_path_motifs": context.get("selected_path_motifs", []),
                "selected_shape_signatures": context.get("selected_shape_signatures", []),
                "selected_example_rows": [
                    example.get("row")
                    for example in context.get("selected_examples", [])
                ],
            },
        )
        return formatted
    except Exception as exc:
        logger.warning("[Chain] Failed to load learned profile context: %s", exc)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04B",
            "Learned profile context failed to load; continue without it",
            {"error": str(exc), "profile_path": str(path)},
        )
        return (
            "=== LEARNED DATA/QUERY PROFILE CONTEXT ===\n"
            "Profile loading failed; rely on schema, domain facts, and examples."
        )


def _get_runtime_property_context(
    *,
    question: str,
    runtime_schema: str,
    learned_context: str,
    graph: Any,
    trace_id: str,
) -> str:
    try:
        property_context, debug = build_runtime_property_context(
            question=question,
            runtime_schema=runtime_schema,
            learned_context=learned_context,
            graph=graph,
        )
        trace_event(
            logger,
            trace_id,
            "CHAIN-04D",
            "Runtime property evidence selected from schema and live Neo4j counts",
            debug,
        )
        return property_context
    except Exception as exc:
        logger.warning("[Chain] Failed to build runtime property context: %s", exc)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04D",
            "Runtime property evidence unavailable; continue without it",
            {"error": str(exc)},
        )
        return (
            "=== RUNTIME PROPERTY EVIDENCE ===\n"
            "Property evidence unavailable; rely on schema and learned examples."
        )


def _get_grounded_schema(
    question: str,
    runtime_schema: str,
    trace_id: str,
) -> tuple[str, dict]:
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
            trace_id=trace_id,
        )
        grounded_text = result.get("schema_text", "")
        debug = result.get("debug", {})
        if grounded_text and len(grounded_text) > 50:
            logger.info("[Chain] Using grounded subschema (%d chars)", len(grounded_text))
            trace_event(
                logger,
                trace_id,
                "CHAIN-03",
                "Grounding decision: use compact grounded schema",
                {"schema_chars": len(grounded_text)},
            )
            return grounded_text, debug
        logger.warning("[Chain] Grounded schema too short, falling back to full schema")
        trace_event(
            logger,
            trace_id,
            "CHAIN-03",
            "Grounding decision: grounded text too short, use full runtime schema",
            {"runtime_schema_chars": len(runtime_schema)},
        )
        return runtime_schema, debug
    except Exception as exc:
        logger.warning("[Chain] Schema grounding failed, using full schema: %s", exc)
        trace_event(
            logger,
            trace_id,
            "CHAIN-03",
            "Grounding decision: exception occurred, use full runtime schema",
            {"error": str(exc)},
        )
        return runtime_schema, {}


def _result_summary(result: Any) -> dict:
    if isinstance(result, list):
        return {
            "type": "list",
            "row_count": len(result),
            "sample_rows": result[:2],
        }
    return {"type": type(result).__name__, "preview": str(result)[:1000]}


def _projected_rows_are_all_null(result: Any) -> bool:
    """Detect syntactically valid queries that only project null values."""
    if not isinstance(result, list) or not result:
        return False
    saw_projected_value = False
    for row in result[:10]:
        if not isinstance(row, dict) or not row:
            continue
        saw_projected_value = True
        if any(value is not None for value in row.values()):
            return False
    return saw_projected_value


def _result_quality_feedback(result: Any) -> str | None:
    if _projected_rows_are_all_null(result):
        return (
            "Neo4j execution returned rows, but every projected value in the "
            "sample is null. Repair the query by avoiding null projections: "
            "use properties that actually have values, add IS NOT NULL filters "
            "for returned or ordered properties, and keep the user's requested "
            "output shape."
        )
    return None


def invoke_chain(
    question: str,
    schema: str = "",
    trace_id: str | None = None,
) -> dict | str:
    trace_id = trace_id or new_trace_id()
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
            "database": get_settings().database_name,
            "question": question,
            "schema_source": "request/CSV" if schema else "Neo4j runtime",
            "schema_chars": len(full_schema),
            "verbose_trace": verbose_trace_enabled(),
        },
    )

    # Use grounded schema when no explicit schema is provided
    if not schema:
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

    domain_template = get_prompt_sections(
        question=question,
        original_question=question,
    )
    trace_event(
        logger,
        trace_id,
        "CHAIN-04",
        "Domain context assembled: shared rules + facts + hints + selected examples",
        domain_template,
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
    property_context = _get_runtime_property_context(
        question=question,
        runtime_schema=full_schema,
        learned_context=learned_context,
        graph=graph,
        trace_id=trace_id,
    )

    current_cypher = ""
    last_error: str | None = None

    for attempt in range(1 + MAX_CYPHER_RETRIES):
        logger.info("[Synthesizer] Attempt %d/%d", attempt + 1, 1 + MAX_CYPHER_RETRIES)
        prompt = _build_coder_prompt(
            schema=db_schema,
            domain_template=domain_template,
            learned_context=f"{learned_context}\n\n{property_context}",
            question=question,
            current_cypher=current_cypher,
            last_error=last_error,
        )
        trace_event(
            logger,
            trace_id,
            "CHAIN-05",
            f"Full final prompt sent to the Cypher LLM (attempt {attempt + 1})",
            prompt,
            verbose_only=True,
        )
        response = llm.invoke(prompt)
        database_name = get_settings().database_name
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
        current_cypher = repair_northwind_order_line_properties(current_cypher)
        current_cypher = rewrite_bare_node_returns(current_cypher, question=question)
        current_cypher = repair_northwind_projection_and_metrics(current_cypher, question=question)
        current_cypher = _ensure_rank_order(current_cypher, question)
        current_cypher = _ensure_requested_limit(current_cypher, question)
        current_cypher = _ensure_ordered_property_not_null(current_cypher)
        current_cypher = strip_noisy_return_properties(current_cypher)
        trace_event(
            logger,
            trace_id,
            "CHAIN-07",
            "Final cleaned Cypher before Neo4j validation",
            current_cypher,
        )

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
            if quality_feedback and attempt < MAX_CYPHER_RETRIES:
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
                "intermediate_steps": [{
                    "query": current_cypher,
                    "grounding": grounding_debug,
                    "trace_id": trace_id,
                }],
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
                    "will_retry": attempt < MAX_CYPHER_RETRIES,
                },
            )
            if attempt < MAX_CYPHER_RETRIES:
                last_error = f"Neo4j validation/execution error: {error_msg}"
                continue
            return f"CYPHER_ERROR: {error_msg}"

    return {
        "query": current_cypher,
        "result": [],
        "trace_id": trace_id,
        "intermediate_steps": [{
            "query": current_cypher,
            "grounding": grounding_debug,
            "trace_id": trace_id,
        }],
    }
