"""Build profile, entity, property, and schema context for Cypher generation."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from neo4j_t2c.adapters.legacy import legacy_database_names
from neo4j_t2c.grounding.entities import (
    format_entity_resolution_context,
    resolve_question_entities,
)
from neo4j_t2c.grounding.properties import build_runtime_property_context
from neo4j_t2c.grounding.vectors import (
    format_vector_context,
    resolve_vector_neighbors,
)
from neo4j_t2c.observability.tracing import trace_event
from neo4j_t2c.ports import ChatModel, ProfileStore
from neo4j_t2c.profiles.paths import profile_build_command, profile_path
from neo4j_t2c.profiles.retrieval import (
    format_profile_context,
    load_profile,
    rerank_profile_context,
    select_profile_context,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntityEvidence:
    context: str
    entity_resolution: dict
    vector_resolution: dict

    @property
    def vector_active(self) -> bool:
        return bool(self.vector_resolution.get("active"))


@dataclass(frozen=True)
class LearnedProfileEvidence:
    context: str
    structured: dict


def get_learned_profile_evidence(
    question: str,
    trace_id: str,
    *,
    database: str | None = None,
    profile_store: ProfileStore | None = None,
    reranker_model: ChatModel | None = None,
) -> LearnedProfileEvidence:
    """Load the generated query/data profile for the active logical database."""
    db_name = database or legacy_database_names()[0]
    path = profile_path(db_name) if profile_store is None else None
    profile_available = (
        profile_store.exists(db_name)
        if profile_store is not None
        else bool(path and path.exists())
    )
    location = (
        f"{type(profile_store).__name__}:{db_name}"
        if profile_store is not None
        else str(path)
    )
    if not profile_available:
        build_command = (
            profile_build_command(db_name)
            if profile_store is None
            else "Build and save a profile through the configured ProfileStore."
        )
        trace_event(
            logger,
            trace_id,
            "CHAIN-04B",
            "Learned profile context unavailable; continue without it",
            {
                "database": db_name,
                "profile_location": location,
                "build_command": build_command,
            },
        )
        return LearnedProfileEvidence(
            context=(
                "=== LEARNED DATA/QUERY PROFILE CONTEXT ===\n"
                "No learned profile is available for this database.\n"
                f"Profile expected at: {location}\n"
                f"Build command: {build_command}"
            ),
            structured={},
        )

    try:
        profile = (
            profile_store.load(db_name)
            if profile_store is not None
            else load_profile(path)
        )
        context = select_profile_context(question, profile, top_k=8)
        if reranker_model is not None:
            context = rerank_profile_context(
                question,
                context,
                reranker_model,
            )
        formatted = format_profile_context(context)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04B",
            "Learned profile context selected from analyzed benchmark/query data",
            {
                "profile_location": location,
                "selected_schema_paths": [
                    path.get("signature")
                    for path in context.get("schema_paths", [])
                ],
                "selected_example_rows": [
                    example.get("row") for example in context.get("selected_examples", [])
                ],
                "reranking": context.get("reranking", {}),
            },
        )
        return LearnedProfileEvidence(context=formatted, structured=context)
    except Exception as exc:
        logger.warning("[Context] Failed to load learned profile: %s", exc)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04B",
            "Learned profile context failed to load; continue without it",
            {"error": str(exc), "profile_location": location},
        )
        return LearnedProfileEvidence(
            context=(
                "=== LEARNED DATA/QUERY PROFILE CONTEXT ===\n"
                "Profile loading failed; rely on runtime schema and live evidence."
            ),
            structured={},
        )


def get_learned_profile_context(
    question: str,
    trace_id: str,
    *,
    database: str | None = None,
    profile_store: ProfileStore | None = None,
    reranker_model: ChatModel | None = None,
) -> str:
    """Compatibility wrapper returning only prompt-ready profile context."""
    return get_learned_profile_evidence(
        question,
        trace_id,
        database=database,
        profile_store=profile_store,
        reranker_model=reranker_model,
    ).context


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
) -> EntityEvidence:
    """Resolve question literals against indexed Neo4j properties."""
    try:
        resolution = resolve_question_entities(question, graph)
        vector_resolution = resolve_vector_neighbors(
            question,
            graph,
            resolution,
        )
        trace_event(
            logger,
            trace_id,
            "CHAIN-04E",
            "Indexed entity/literal resolver completed",
            {
                "literals": resolution.get("literals", []),
                "anchors": resolution.get("anchors", []),
                "vector": vector_resolution,
            },
        )
        return EntityEvidence(
            context="\n\n".join(
                part
                for part in (
                    format_entity_resolution_context(resolution),
                    format_vector_context(vector_resolution),
                )
                if part
            ),
            entity_resolution=resolution,
            vector_resolution=vector_resolution,
        )
    except Exception as exc:
        logger.warning("[Context] Indexed entity resolver failed: %s", exc)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04E",
            "Indexed entity/literal resolver unavailable; continue without it",
            {"error": str(exc)},
        )
        return EntityEvidence(
            context=(
                "=== INDEXED ENTITY/LITERAL RESOLUTION ===\n"
                "Entity resolver unavailable; rely on schema and profile context."
            ),
            entity_resolution={},
            vector_resolution={
                "enabled": True,
                "active": False,
                "reason": "entity_resolver_failed",
            },
        )


def get_grounded_schema(
    question: str,
    runtime_schema: str,
    trace_id: str,
    *,
    database: str | None = None,
    grounding_model: ChatModel | None = None,
) -> tuple[str, dict]:
    """Select a compact schema with an LLM, falling back to the full schema."""
    from neo4j_t2c.grounding.schema import build_grounded_schema

    try:
        if grounding_model is None:
            from models.llm import get_grounding_llm

            grounding_model = get_grounding_llm()
        result = build_grounded_schema(
            question=question,
            runtime_schema=runtime_schema,
            db_name=database or legacy_database_names()[0],
            llm=grounding_model,
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


def has_profile_for_current_database(
    *,
    database: str | None = None,
    profile_store: ProfileStore | None = None,
) -> bool:
    try:
        db_name = database or legacy_database_names()[0]
        if profile_store is not None:
            return profile_store.exists(db_name)
        return profile_path(db_name).exists()
    except Exception:
        return False
