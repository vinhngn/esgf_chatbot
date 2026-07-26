"""Injected backend over the current pipeline during package migration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from neo4j_t2c.observability.tracing import trace_sink_scope
from neo4j_t2c.runtime import PipelineDependencies


class InjectedPipelineBackend:
    """Run the existing pipeline with explicit graph, model, and profile ports."""

    def __init__(self, dependencies: PipelineDependencies) -> None:
        self.dependencies = dependencies

    def __call__(
        self,
        question: str,
        schema: str,
    ) -> Mapping[str, Any]:
        from neo4j_t2c.service import get_raw_results

        with trace_sink_scope(self.dependencies.trace_sink):
            return get_raw_results(
                question,
                schema,
                dependencies=self.dependencies,
            )
