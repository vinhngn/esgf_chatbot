"""Public raw Text-to-Cypher service used by the API and benchmark harness."""

from __future__ import annotations

import ast
import logging
import os

from config import get_settings
from models.graph import get_graph
from services.profile_analyzer.store import profile_dir
from services.text2cypher.pipeline import invoke_chain
from services.text2cypher.prompting import build_coder_messages
from services.text2cypher.result_utils import extract_cypher_queries
from templates.cypher_templates import (
    _DOMAIN_CONFIGS,
    get_cypher_template,
)
from templates.entity_definitions import get_entity_definitions
from templates.match_properties_map import get_match_properties_map
from utils.helpers import (
    clean_cypher_query,
    normalize_value,
    strip_noisy_return_properties,
)
from utils.pipeline_trace import new_trace_id, trace_event

logger = logging.getLogger(__name__)


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


def get_raw_results(question: str, schema: str = "") -> dict:
    """Generate Cypher directly from a question and return raw Neo4j rows."""
    db_name = get_settings().profile_database_name
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
        chain_result = invoke_chain(question, schema, trace_id=trace_id)
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
        )
        if fallback_cypher:
            decoded_query = fallback_cypher
            try:
                fallback_result = get_graph().query(fallback_cypher)
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
) -> str:
    """Generate one Cypher query with the compact fallback prompt."""
    from models.llm import get_cypher_llm

    graph = get_graph()
    messages = build_coder_messages(
        schema=graph.get_schema,
        domain_template="",
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
        response = get_cypher_llm().invoke(messages)
        raw_cypher = response.content.strip()
        trace_event(
            logger,
            trace_id,
            "T2C-03",
            "Direct fallback LLM return value: one Cypher text string",
            raw_cypher,
        )
        cleaned = clean_cypher_query(raw_cypher)
        return strip_noisy_return_properties(cleaned)
    except Exception as exc:
        logger.warning("[Text2Cypher] Fallback LLM call failed: %s", exc)
        return ""


def get_available_databases() -> list[str]:
    """Return logical databases known through templates or profiles."""
    databases = set(_DOMAIN_CONFIGS)
    directory = profile_dir()
    if directory.exists():
        suffix = "_profile.json"
        databases.update(path.name[: -len(suffix)] for path in directory.glob(f"*{suffix}"))
    return sorted(databases)


def get_database_info() -> dict:
    """Return template metadata for the active logical database."""
    db_name = get_settings().profile_database_name
    return {
        "database": db_name,
        "cypher_template": get_cypher_template(db_name),
        "entity_definitions": get_entity_definitions(db_name),
        "match_properties": get_match_properties_map(db_name),
    }


def get_schema_info(database: str | None = None) -> dict:
    """Return configured schema hints for one logical database."""
    db_name = database or get_settings().profile_database_name
    return {
        "database": db_name,
        "entity_definitions": get_entity_definitions(db_name),
        "match_properties": get_match_properties_map(db_name),
        "has_cypher_template": db_name in get_available_databases(),
    }
