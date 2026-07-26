"""Dependency contracts used by the library core."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from neo4j_t2c.contracts import TraceEvent


@runtime_checkable
class GraphClient(Protocol):
    """Minimal graph operations required by Text-to-Cypher."""

    @property
    def schema(self) -> str:
        """Return the current Neo4j schema text."""

    def refresh_schema(self) -> None:
        """Refresh the client's schema snapshot."""

    def query(
        self,
        cypher: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute one Cypher statement."""

    def close(self) -> None:
        """Release graph resources."""


@runtime_checkable
class ChatModel(Protocol):
    """Provider-neutral chat model operation used by generation."""

    def invoke(
        self,
        input: Any,
        config: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Return a provider response exposing generated content."""


@runtime_checkable
class ProfileStore(Protocol):
    """Persistence boundary for database profiles."""

    def exists(self, database: str) -> bool:
        """Return whether a profile is available."""

    def load(self, database: str) -> dict[str, Any]:
        """Load one profile."""

    def save(self, database: str, profile: Mapping[str, Any]) -> None:
        """Persist one profile atomically."""


@runtime_checkable
class TraceSink(Protocol):
    """Destination for structured pipeline observations."""

    def emit(self, event: TraceEvent) -> None:
        """Record one trace event."""


@runtime_checkable
class Text2CypherBackend(Protocol):
    """Temporary compatibility boundary around the current service payload."""

    def __call__(
        self,
        question: str,
        schema: str,
    ) -> Mapping[str, Any]:
        """Run one request and return the legacy mapping contract."""
