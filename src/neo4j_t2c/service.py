"""Generate, execute, and normalize one Text-to-Cypher request."""

from __future__ import annotations

import ast
import logging
import os

from neo4j_t2c.adapters.legacy import legacy_database_names
from neo4j_t2c.execution.results import extract_cypher_queries
from neo4j_t2c.execution.values import normalize_value
from neo4j_t2c.generation.cleanup import clean_cypher_query
from neo4j_t2c.generation.prompts import build_coder_messages
from neo4j_t2c.observability.tracing import new_trace_id, trace_event
from neo4j_t2c.pipeline import invoke_chain
from neo4j_t2c.runtime import PipelineDependencies

logger = logging.getLogger(__name__)


def _legacy_graph():
    from models.graph import get_graph

    return get_graph()


def _direct_fallback_enabled() -> bool:
    return os.getenv(
        "T2C_DIRECT_FALLBACK_ENABLED",
        "true",
    ).strip().lower() not in {"0", "false", "no", "off"}


def _normalize_rows(raw_result: object) -> list:
    if isinstance(raw_result, list):
        normalized = normalize_value(raw_result)
        return normalized if isinstance(normalized, list) else []
    if not isinstance(raw_result, str):
        return []

    value = raw_result.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return []
    try:
        normalized = normalize_value(ast.literal_eval(value))
    except (SyntaxError, ValueError):
        return []
    return normalized if isinstance(normalized, list) else []


def get_raw_results(
    question: str,
    schema: str = "",
    *,
    dependencies: PipelineDependencies | None = None,
) -> dict:
    """Generate Cypher directly from a question and return raw Neo4j rows."""
    db_name = (
        dependencies.database
        if dependencies is not None
        else legacy_database_names()[0]
    )
    trace_id = new_trace_id()
    trace_event(
        logger,
        trace_id,
        "T2C-01",
        "Raw Text-to-Cypher endpoint skips triple preprocessing",
        {
            "question_passed_to_chain": question,
            "database": db_name,
            "explicit_schema_supplied": bool(schema),
        },
    )

    try:
        chain_result = invoke_chain(
            question,
            schema,
            trace_id=trace_id,
            dependencies=dependencies,
        )
    except Exception as exc:
        trace_event(
            logger,
            trace_id,
            "T2C-ERROR",
            "Text-to-Cypher pipeline failed before producing a query",
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise
    decoded_query = ""
    result: list = []
    error: str | None = None

    if isinstance(chain_result, dict):
        _, extracted_query = extract_cypher_queries(chain_result)
        decoded_query = extracted_query or ""
        result = _normalize_rows(chain_result.get("result"))
        error = chain_result.get("error")
    else:
        error = str(chain_result) if chain_result else None

    if not decoded_query and _direct_fallback_enabled():
        logger.info("[Text2Cypher] Primary chain returned no query; using fallback.")
        trace_event(
            logger,
            trace_id,
            "T2C-02",
            "Primary chain returned no Cypher; invoke direct fallback LLM",
        )
        fallback_cypher = _direct_cypher_fallback(
            question,
            trace_id=trace_id,
            dependencies=dependencies,
        )
        if fallback_cypher:
            decoded_query = fallback_cypher
            try:
                graph = (
                    dependencies.graph
                    if dependencies is not None
                    else _legacy_graph()
                )
                fallback_result = graph.query(fallback_cypher)
                result = _normalize_rows(fallback_result)
                error = None
                trace_event(
                    logger,
                    trace_id,
                    "T2C-04",
                    "Fallback Cypher executed successfully",
                    {
                        "row_count": len(result),
                        "sample_rows": result[:2],
                    },
                )
            except Exception as exc:
                logger.warning(
                    "[Text2Cypher] Fallback execution failed: %s",
                    exc,
                )
    elif not decoded_query:
        logger.info("[Text2Cypher] Primary chain returned no query; fallback disabled.")

    return {
        "cypher_query": decoded_query,
        "result": result,
        "error": error,
        "rewritten": "",
        "verified_triples": [],
        "instance_triples": [],
        "trace_id": trace_id,
    }


def _direct_cypher_fallback(
    question: str,
    trace_id: str = "unknown",
    *,
    dependencies: PipelineDependencies | None = None,
) -> str:
    """Generate one Cypher query with the compact fallback prompt."""
    graph = (
        dependencies.graph
        if dependencies is not None
        else _legacy_graph()
    )
    schema = getattr(graph, "schema", None)
    if not isinstance(schema, str):
        schema = str(getattr(graph, "get_schema", "") or "")
    messages = build_coder_messages(
        schema=schema,
        learned_context="",
        evidence_context="",
        question=question,
        current_cypher="",
        last_error=None,
    )
    trace_event(
        logger,
        trace_id,
        "T2C-03",
        "Full prompt sent to the direct fallback Cypher LLM",
        {
            "system": messages[0].content,
            "user": messages[1].content,
        },
        verbose_only=True,
    )

    try:
        if dependencies is not None:
            llm = dependencies.cypher_model
        else:
            from models.llm import get_cypher_llm

            llm = get_cypher_llm()
        response = llm.invoke(messages)
        raw_cypher = response.content.strip()
        trace_event(
            logger,
            trace_id,
            "T2C-03",
            "Direct fallback LLM return value: one Cypher text string",
            raw_cypher,
        )
        cleaned = clean_cypher_query(raw_cypher)
        return cleaned
    except Exception as exc:
        logger.warning("[Text2Cypher] Fallback LLM call failed: %s", exc)
        return ""
