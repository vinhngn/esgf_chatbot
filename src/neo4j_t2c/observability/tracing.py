"""Structured, request-scoped tracing for the Text-to-Cypher pipeline."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from neo4j_t2c.ports import TraceSink

_TRACE_SINK: ContextVar[TraceSink | None] = ContextVar(
    "neo4j_t2c_trace_sink",
    default=None,
)


def new_trace_id() -> str:
    """Return a short correlation ID for one end-to-end request."""
    return uuid.uuid4().hex[:10]


def verbose_trace_enabled() -> bool:
    """Enable full prompts and LLM payloads with PIPELINE_TRACE=1."""
    return os.getenv("PIPELINE_TRACE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _render(value: Any) -> str:
    if isinstance(value, str):
        rendered = value
    else:
        try:
            rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        except Exception:
            rendered = repr(value)

    try:
        max_chars = max(1000, int(os.getenv("PIPELINE_TRACE_MAX_CHARS", "30000")))
    except ValueError:
        max_chars = 30000
    if len(rendered) <= max_chars:
        return rendered
    omitted = len(rendered) - max_chars
    return f"{rendered[:max_chars]}\n... <truncated {omitted} chars>"


@contextmanager
def trace_sink_scope(
    sink: TraceSink | None,
) -> Iterator[None]:
    """Bind a request-local trace sink and restore the previous scope."""
    token = _TRACE_SINK.set(sink)
    try:
        yield
    finally:
        _TRACE_SINK.reset(token)


def trace_event(
    logger,
    trace_id: str,
    stage: str,
    title: str,
    payload: Any | None = None,
    *,
    verbose_only: bool = False,
) -> None:
    """Log one pipeline checkpoint with a stable, grep-friendly prefix."""
    from neo4j_t2c.observability.store import record_trace_event

    record_trace_event(
        trace_id,
        stage,
        title,
        payload,
        verbose_only=verbose_only,
    )
    sink = _TRACE_SINK.get()
    if sink is not None:
        from neo4j_t2c.contracts import TraceEvent

        try:
            sink.emit(
                TraceEvent(
                    trace_id=trace_id,
                    stage=stage,
                    message=title,
                    payload=payload,
                    verbose=verbose_only,
                )
            )
        except Exception:
            logger.debug(
                "Trace sink failed for %s/%s",
                trace_id,
                stage,
                exc_info=True,
            )
    prefix = f"[PipelineTrace:{trace_id}] [{stage}] {title}"
    if not verbose_trace_enabled():
        if not verbose_only:
            logger.debug(prefix)
        return
    if payload is None:
        logger.info(prefix)
        return
    logger.info("%s\n%s", prefix, _render(payload))
