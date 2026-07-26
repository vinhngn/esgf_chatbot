"""Compatibility imports for the canonical tracing API."""

from neo4j_t2c.observability.tracing import (
    new_trace_id,
    trace_event,
    trace_sink_scope,
    verbose_trace_enabled,
)

__all__ = [
    "new_trace_id",
    "trace_event",
    "trace_sink_scope",
    "verbose_trace_enabled",
]
