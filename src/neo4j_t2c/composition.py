"""Convenience composition for the dependency-injected runtime."""

from __future__ import annotations

from pathlib import Path

from neo4j_t2c.engine import Text2CypherEngine
from neo4j_t2c.ports import ChatModel, GraphClient, ProfileStore, TraceSink


def create_engine(
    *,
    database: str,
    graph: GraphClient,
    model: ChatModel,
    physical_database: str | None = None,
    grounding_model: ChatModel | None = None,
    profile_store: ProfileStore | None = None,
    profile_directory: str | Path | None = None,
    trace_sink: TraceSink | None = None,
) -> Text2CypherEngine:
    """Compose the production pipeline from explicit infrastructure ports."""
    if profile_store is not None and profile_directory is not None:
        raise ValueError("Pass profile_store or profile_directory, not both")

    from neo4j_t2c.adapters.pipeline import InjectedPipelineBackend
    from neo4j_t2c.adapters.profiles import JsonProfileStore
    from neo4j_t2c.runtime import PipelineDependencies

    resolved_store = profile_store
    if profile_directory is not None:
        resolved_store = JsonProfileStore(profile_directory)

    dependencies = PipelineDependencies(
        database=database,
        physical_database=physical_database or database,
        graph=graph,
        cypher_model=model,
        grounding_model=grounding_model,
        profile_store=resolved_store,
        trace_sink=trace_sink,
    )
    return Text2CypherEngine(InjectedPipelineBackend(dependencies))


__all__ = ["create_engine"]
