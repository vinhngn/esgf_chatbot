"""Compatibility imports for the canonical trace store."""

from neo4j_t2c.observability.store import (
    clear_traces,
    get_trace,
    list_traces,
    record_trace_event,
)

__all__ = [
    "clear_traces",
    "get_trace",
    "list_traces",
    "record_trace_event",
]
