from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

_lock = threading.RLock()
_traces: OrderedDict[str, dict[str, Any]] = OrderedDict()


def _enabled() -> bool:
    return os.getenv("ENABLE_TRACE_CAPTURE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _limit(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _bounded_payload(payload: Any) -> Any:
    if payload is None:
        return None
    try:
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        serialized = repr(payload)

    max_chars = _limit("TRACE_CAPTURE_MAX_CHARS", 50_000, 1_000)
    if len(serialized) <= max_chars:
        try:
            return json.loads(serialized)
        except json.JSONDecodeError:
            return serialized
    return {
        "truncated": True,
        "original_chars": len(serialized),
        "preview": serialized[:max_chars],
    }


def record_trace_event(
    trace_id: str,
    stage: str,
    title: str,
    payload: Any = None,
    *,
    verbose_only: bool = False,
) -> None:
    if not _enabled():
        return

    now = datetime.now(UTC).isoformat()
    event = {
        "stage": stage,
        "title": title,
        "timestamp": now,
        "payload": _bounded_payload(payload),
        "verbose": verbose_only,
    }
    with _lock:
        trace = _traces.setdefault(
            trace_id,
            {
                "trace_id": trace_id,
                "started_at": now,
                "updated_at": now,
                "events": [],
            },
        )
        trace["updated_at"] = now
        trace["events"].append(event)
        max_events = _limit("TRACE_CAPTURE_MAX_EVENTS", 80, 10)
        if len(trace["events"]) > max_events:
            trace["events"] = trace["events"][-max_events:]
        _traces.move_to_end(trace_id)

        max_traces = _limit("TRACE_CAPTURE_MAX_TRACES", 200, 10)
        while len(_traces) > max_traces:
            _traces.popitem(last=False)


def list_traces(limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        traces = list(reversed(_traces.values()))[: max(1, min(limit, 200))]
        return [
            {
                "trace_id": trace["trace_id"],
                "started_at": trace["started_at"],
                "updated_at": trace["updated_at"],
                "event_count": len(trace["events"]),
                "first_stage": trace["events"][0]["stage"] if trace["events"] else "",
                "last_stage": trace["events"][-1]["stage"] if trace["events"] else "",
                "title": trace["events"][0]["title"] if trace["events"] else "",
            }
            for trace in traces
        ]


def get_trace(trace_id: str) -> dict[str, Any] | None:
    with _lock:
        trace = _traces.get(trace_id)
        if trace is None:
            return None
        return json.loads(json.dumps(trace, ensure_ascii=False, default=str))


def clear_traces() -> None:
    with _lock:
        _traces.clear()
