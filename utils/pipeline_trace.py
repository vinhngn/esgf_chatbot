"""Structured, controllable logging for the Text-to-Cypher pipeline."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any


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
    from services.observability.trace_store import record_trace_event

    record_trace_event(
        trace_id,
        stage,
        title,
        payload,
        verbose_only=verbose_only,
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
