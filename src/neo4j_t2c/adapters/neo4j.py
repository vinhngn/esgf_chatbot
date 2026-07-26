"""Neo4j adapter backed by ``langchain_neo4j.Neo4jGraph``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_neo4j import Neo4jGraph


class LangChainNeo4jClient:
    """Expose a small graph contract without leaking LangChain into the core."""

    def __init__(self, graph: Neo4jGraph) -> None:
        self._graph = graph

    @classmethod
    def connect(
        cls,
        *,
        uri: str,
        username: str,
        password: str,
        database: str = "neo4j",
        sanitize: bool = True,
        timeout: float | None = None,
    ) -> LangChainNeo4jClient:
        graph = Neo4jGraph(
            url=uri,
            username=username,
            password=password,
            database=database,
            sanitize=sanitize,
            timeout=timeout,
        )
        return cls(graph)

    @property
    def schema(self) -> str:
        return str(self._graph.get_schema or "")

    def refresh_schema(self) -> None:
        self._graph.refresh_schema()

    def query(
        self,
        cypher: str,
        parameters: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        legacy_params = kwargs.pop("params", None)
        if kwargs:
            unknown = ", ".join(sorted(kwargs))
            raise TypeError(f"Unsupported query arguments: {unknown}")
        if parameters is not None and legacy_params is not None:
            raise TypeError("Pass parameters or params, not both")
        parameters = parameters if parameters is not None else legacy_params
        return self._graph.query(cypher, params=dict(parameters or {}))

    def close(self) -> None:
        driver = getattr(self._graph, "_driver", None)
        close = getattr(driver, "close", None)
        if callable(close):
            close()
