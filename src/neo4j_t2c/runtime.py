"""Explicit dependencies for one Text-to-Cypher pipeline instance."""

from __future__ import annotations

from dataclasses import dataclass

from neo4j_t2c.ports import (
    ChatModel,
    GraphClient,
    ProfileStore,
    TraceSink,
)


@dataclass(frozen=True)
class PipelineDependencies:
    """Infrastructure selected by the embedding application."""

    database: str
    physical_database: str
    graph: GraphClient
    cypher_model: ChatModel
    grounding_model: ChatModel | None = None
    profile_store: ProfileStore | None = None
    trace_sink: TraceSink | None = None

    def __post_init__(self) -> None:
        database = self.database.strip().lower()
        physical = self.physical_database.strip().lower()
        if not database:
            raise ValueError("Logical database is required")
        if not physical:
            raise ValueError("Physical database is required")
        object.__setattr__(self, "database", database)
        object.__setattr__(self, "physical_database", physical)

    @property
    def schema_grounding_model(self) -> ChatModel:
        return self.grounding_model or self.cypher_model
