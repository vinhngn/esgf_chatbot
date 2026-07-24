from __future__ import annotations

import json
from typing import Any

import streamlit as st

from views.studio.api_client import ApiError, request_json
from views.studio.state import StudioState, runtime_api_ready


def _render_payload(payload: Any) -> None:
    if isinstance(payload, (dict, list)):
        st.json(payload)
    elif payload is None:
        st.caption("No payload")
    else:
        st.code(str(payload), language=None, wrap_lines=True)


def _render_runtime_logs(state: StudioState) -> None:
    with st.expander("Runtime logs"):
        st.code("\n".join(state.runtime.logs) or "No runtime logs.", language="text")


def _show_api_unavailable(state: StudioState, error: ApiError) -> None:
    if error.connection_failure:
        state.runtime.stop_api()
        st.error("The T2C API is no longer available. Start it again from Connection.")
    else:
        st.error(str(error))
    with st.expander("Technical details"):
        st.code(str(error), language="text")


def render(state: StudioState) -> None:
    st.header("Pipeline Inspector")
    if not runtime_api_ready(state):
        st.warning("Start the T2C API to inspect requests.")
        return

    refresh, clear = st.columns(2)
    refresh.button("Refresh", use_container_width=True)
    if clear.button("Clear traces", use_container_width=True):
        try:
            request_json(state.runtime.api_url, "DELETE", "/api/traces")
        except ApiError as exc:
            _show_api_unavailable(state, exc)
        else:
            st.rerun()

    try:
        trace_list = request_json(
            state.runtime.api_url,
            "GET",
            "/api/traces?limit=100",
            timeout=10,
        ).get("traces", [])
    except ApiError as exc:
        _show_api_unavailable(state, exc)
        return

    if not trace_list:
        st.info("No captured requests.")
        _render_runtime_logs(state)
        return

    labels = {
        item["trace_id"]: (
            f"{item['started_at']} | {item['first_stage']} → "
            f"{item['last_stage']} | {item['trace_id']}"
        )
        for item in trace_list
    }
    selected_id = st.selectbox(
        "Request",
        list(labels),
        format_func=labels.__getitem__,
    )
    try:
        trace = request_json(
            state.runtime.api_url,
            "GET",
            f"/api/traces/{selected_id}",
            timeout=10,
        )
    except ApiError as exc:
        _show_api_unavailable(state, exc)
        return

    events = trace.get("events", [])
    one, two = st.columns(2)
    one.metric("Events", len(events))
    two.metric("Started", trace.get("started_at", "")[11:19])
    st.code(trace.get("trace_id", ""), language=None)

    stages = sorted({event.get("stage", "") for event in events})
    selected_stages = st.multiselect("Stages", stages, default=stages)
    for event in events:
        if event.get("stage") not in selected_stages:
            continue
        label = f"{event.get('stage')} · {event.get('title')}"
        expanded = event.get("stage") in {"CHAIN-05", "CHAIN-09"}
        with st.expander(label, expanded=expanded):
            st.caption(event.get("timestamp", ""))
            _render_payload(event.get("payload"))

    st.download_button(
        "Download trace JSON",
        data=json.dumps(trace, ensure_ascii=False, indent=2),
        file_name=f"trace-{selected_id}.json",
        mime="application/json",
    )
    _render_runtime_logs(state)
