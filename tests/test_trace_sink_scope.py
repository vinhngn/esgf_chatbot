from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from neo4j_t2c.adapters import CallableTraceSink
from utils.pipeline_trace import trace_event, trace_sink_scope

logger = logging.getLogger(__name__)


def _emit_in_scope(trace_id: str) -> list[str]:
    stages: list[str] = []
    sink = CallableTraceSink(lambda event: stages.append(event.stage))
    with trace_sink_scope(sink):
        trace_event(
            logger,
            trace_id,
            f"STAGE-{trace_id}",
            "request event",
        )
    trace_event(logger, trace_id, "OUTSIDE", "outside event")
    return stages


def test_trace_sink_scope_is_isolated_across_concurrent_requests() -> None:
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_emit_in_scope, "one")
        second = executor.submit(_emit_in_scope, "two")

    assert first.result() == ["STAGE-one"]
    assert second.result() == ["STAGE-two"]
