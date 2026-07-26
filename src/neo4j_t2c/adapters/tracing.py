"""Trace sink adapters."""

from __future__ import annotations

from collections.abc import Callable

from neo4j_t2c.contracts import TraceEvent


class NullTraceSink:
    """Discard events when observability is not configured."""

    def emit(self, event: TraceEvent) -> None:
        del event


class CallableTraceSink:
    """Forward events to an application callback."""

    def __init__(self, callback: Callable[[TraceEvent], None]) -> None:
        self._callback = callback

    def emit(self, event: TraceEvent) -> None:
        self._callback(event)
