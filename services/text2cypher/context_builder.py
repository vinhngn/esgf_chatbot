"""Build profile, entity, property, and schema context for Cypher generation."""

from __future__ import annotations

import logging
import os
from typing import Any

from config import get_settings
from services.profile_analyzer.context import (
    format_profile_context,
    load_profile,
    select_profile_context,
)
from services.profile_analyzer.store import profile_build_command, profile_path
from services.universal.entity_resolver import (
    format_entity_resolution_context,
    resolve_question_entities,
)
from services.universal.property_context import build_runtime_property_context
from utils.pipeline_trace import trace_event

logger = logging.getLogger(__name__)


def get_learned_profile_context(question: str, trace_id: str) -> str:
    """Load the generated query/data profile for the active logical database."""
    settings = get_settings()
    path = profile_path(settings.profile_database_name)
    if not path.exists():
        build_command = profile_build_command(settings.profile_database_name)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04B",
            "Learned profile context unavailable; continue without it",
            {
                "database": settings.profile_database_name,
                "profile_path": str(path),
                "build_command": build_command,
            },
        )
        return (
            "=== LEARNED DATA/QUERY PROFILE CONTEXT ===\n"
            "No learned profile is available for this database.\n"
            f"Profile expected at: {path}\n"
            f"Build command: {build_command}"
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
                "selected_path_motifs": context.get(
                    "selected_path_motifs",
                    [],
                ),
                "selected_shape_signatures": context.get(
                    "selected_shape_signatures",
                    [],
                ),
                "selected_example_rows": [
                    example.get("row") for example in context.get("selected_examples", [])
                ],
            },
        )
        return formatted
    except Exception as exc:
        logger.warning("[Context] Failed to load learned profile: %s", exc)
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


def get_runtime_property_context(
    *,
    question: str,
    runtime_schema: str,
    learned_context: str,
    graph: Any,
    trace_id: str,
) -> str:
    """Collect live evidence about which candidate properties contain values."""
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
        logger.warning("[Context] Runtime property evidence failed: %s", exc)
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


def get_entity_resolution_context(
    *,
    question: str,
    graph: Any,
    trace_id: str,
) -> str:
    """Resolve question literals against indexed Neo4j properties."""
    try:
        resolution = resolve_question_entities(question, graph)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04E",
            "Indexed entity/literal resolver completed",
            {
                "literals": resolution.get("literals", []),
                "anchors": resolution.get("anchors", []),
            },
        )
        return format_entity_resolution_context(resolution)
    except Exception as exc:
        logger.warning("[Context] Indexed entity resolver failed: %s", exc)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04E",
            "Indexed entity/literal resolver unavailable; continue without it",
            {"error": str(exc)},
        )
        return (
            "=== INDEXED ENTITY/LITERAL RESOLUTION ===\n"
            "Entity resolver unavailable; rely on schema and profile context."
        )


def get_grounded_schema(
    question: str,
    runtime_schema: str,
    trace_id: str,
) -> tuple[str, dict]:
    """Select a compact schema with an LLM, falling back to the full schema."""
    from models.llm import get_grounding_llm
    from services.universal.schema_grounder import build_grounded_schema

    try:
        result = build_grounded_schema(
            question=question,
            runtime_schema=runtime_schema,
            db_name=get_settings().profile_database_name,
            llm=get_grounding_llm(),
            trace_id=trace_id,
        )
        grounded_text = result.get("schema_text", "")
        debug = result.get("debug", {})
        if grounded_text and len(grounded_text) > 50:
            logger.info(
                "[Context] Using grounded subschema (%d chars)",
                len(grounded_text),
            )
            trace_event(
                logger,
                trace_id,
                "CHAIN-03",
                "Grounding decision: use compact grounded schema",
                {"schema_chars": len(grounded_text)},
            )
            return grounded_text, debug

        trace_event(
            logger,
            trace_id,
            "CHAIN-03",
            "Grounding decision: grounded text too short, use full runtime schema",
            {"runtime_schema_chars": len(runtime_schema)},
        )
        return runtime_schema, debug
    except Exception as exc:
        logger.warning("[Context] Schema grounding failed: %s", exc)
        trace_event(
            logger,
            trace_id,
            "CHAIN-03",
            "Grounding decision: exception occurred, use full runtime schema",
            {"error": str(exc)},
        )
        return runtime_schema, {}


def schema_grounding_mode() -> str:
    return os.getenv("T2C_SCHEMA_GROUNDING_MODE", "profile").strip().lower()


def has_profile_for_current_database() -> bool:
    try:
        return profile_path(get_settings().profile_database_name).exists()
    except Exception:
        return False
