from __future__ import annotations

import streamlit as st

from services.control_center.models import DatabaseConnection
from views.studio import connection_page, model_page, profile_page
from views.studio.state import StudioState, runtime_api_ready, select_connection


def _add_connection(state: StudioState) -> None:
    item = DatabaseConnection(
        name="New database",
        profile_name="new_database",
        uri="neo4j://localhost:7687",
        database="neo4j",
    )
    state.store.save(item)
    select_connection(state, item.id)


def render(state: StudioState) -> None:
    st.title("Database connection")
    connections = state.store.list()
    ids = [item.id for item in connections]
    names = {item.id: item.name for item in connections}
    active_id = state.active_connection.id

    selector, add = st.columns([4, 1])
    selected_id = selector.selectbox(
        "Database",
        ids,
        index=ids.index(active_id) if active_id in ids else 0,
        format_func=names.__getitem__,
    )
    if selected_id != active_id:
        select_connection(state, selected_id)
        st.rerun()
    if add.button("Add database", width="stretch"):
        _add_connection(state)
        st.rerun()

    runtime_connection = state.runtime.connection
    api_port_in_use = bool(
        getattr(state.runtime, "api_port_in_use", False)
    )
    status, action = st.columns([4, 1])
    stop_clicked = action.button(
        "Stop T2C API",
        width="stretch",
        disabled=not api_port_in_use,
    )
    if runtime_api_ready(state) and runtime_connection:
        status.success(
            f"API ready | {runtime_connection.profile_name} | "
            f"{state.runtime.api_url}/api/text2cypher"
        )
    elif api_port_in_use:
        health_reader = getattr(state.runtime, "api_health_on_port", None)
        health = health_reader() if health_reader else {}
        if health:
            status.warning(
                f"Detached T2C API detected on port {state.runtime.api_port} | "
                f"{health.get('database', 'unknown')}"
            )
        else:
            status.error(
                f"Port {state.runtime.api_port} is used by another service."
            )
    elif runtime_connection:
        status.info(f"Connected | {runtime_connection.profile_name}")
    else:
        status.caption("Disconnected")

    if stop_clicked:
        try:
            with st.spinner("Stopping T2C API..."):
                stop_api = getattr(
                    state.runtime,
                    "stop_api_on_port",
                    state.runtime.stop_api,
                )
                stop_api()
        except Exception as exc:
            st.error(str(exc))
        else:
            st.rerun()

    connection_tab, model_tab, profile_tab = st.tabs(
        ("Connection", "AI model", "Profile")
    )
    with connection_tab:
        connection_page.render(state)
    with model_tab:
        model_page.render(state)
    with profile_tab:
        profile_page.render(state)
