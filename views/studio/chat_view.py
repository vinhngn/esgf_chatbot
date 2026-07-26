from __future__ import annotations

import streamlit as st

from constants import TITLE
from views.sidebar import sidebar
from views.studio.api_client import ApiError, request_json
from views.studio.state import StudioState, runtime_api_ready


def _conversation_history() -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for message in st.session_state.get("messages", []):
        if message["role"] == "user":
            history.append({"input": message["content"], "output": ""})
        elif message["role"] == "ai" and history:
            history[-1]["output"] = message["content"]
    return history[-3:]


def _pipeline_details(result: dict) -> dict:
    return {
        "trace_id": result.get("trace_id", ""),
        "rewritten": result.get("rewritten", ""),
        "cypher_query": result.get("cypher_query", ""),
        "verified_triples": result.get("verified_triples", []),
        "instance_triples": result.get("instance_triples", []),
        "question_tag": result.get("question_tag", ""),
        "answer_source": result.get("answer_source", ""),
        "route_confidence": result.get("route_confidence", 0),
        "referenced_turn_ids": result.get("referenced_turn_ids", []),
    }


def _render_pipeline_details(details: dict) -> None:
    route = details.get("question_tag", "")
    if route:
        confidence = float(details.get("route_confidence") or 0)
        st.markdown(
            f"**Question route:** `{route}` "
            f"· source `{details.get('answer_source') or 'NONE'}` "
            f"· confidence `{confidence:.2f}`"
        )
        references = details.get("referenced_turn_ids", [])
        if references:
            st.markdown(
                "**Referenced turns:** "
                + ", ".join(f"`{turn_id}`" for turn_id in references)
            )
    st.markdown(f"**Rewritten question:** {details.get('rewritten') or '—'}")
    verified = details.get("verified_triples", [])
    st.markdown("**Verified triples:**")
    if verified:
        for subject, predicate, object_ in verified:
            st.markdown(f"- `({subject}, {predicate}, {object_})`")
    else:
        st.markdown("—")

    instances = details.get("instance_triples", [])
    st.markdown("**Instance triples:**")
    if instances:
        for subject, predicate, object_ in instances:
            st.markdown(f"- `({subject}, {predicate}, {object_})`")
    else:
        st.markdown("—")

    cypher = details.get("cypher_query", "")
    st.markdown("**Generated Cypher:**")
    if cypher:
        st.code(cypher, language="cypher")
    else:
        st.markdown("—")
    if details.get("trace_id"):
        st.caption(f"Trace: {details['trace_id']}")


def render(state: StudioState) -> None:
    connection = state.active_connection
    sidebar(connection.profile_name)
    st.markdown(TITLE, unsafe_allow_html=True)

    runtime_connection = state.runtime.connection
    runtime_matches = bool(
        runtime_api_ready(state)
        and runtime_connection
        and runtime_connection.id == connection.id
    )
    if runtime_matches:
        st.sidebar.markdown("---")
        st.sidebar.caption(
            f"Flask API: {state.runtime.api_url} · {connection.profile_name}"
        )

    if "messages" not in st.session_state:
        st.session_state["messages"] = [
            {
                "role": "ai",
                "content": (
                    "This is a Proof of Concept application which shows how GenAI "
                    "can be used with Neo4j to build and consume Knowledge Graphs "
                    "using cypher query.\nSee the sidebar for more information!"
                ),
            }
        ]

    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"], unsafe_allow_html=True)
            if message.get("details"):
                with st.expander("Pipeline debug", expanded=False):
                    _render_pipeline_details(message["details"])

    if not runtime_matches:
        st.warning(f"Database '{connection.profile_name}' is disconnected.")
        return

    sample = st.session_state.pop("sample", None)
    user_input = sample or st.chat_input("Ask a question", key="user_input")
    if not user_input:
        return

    history = _conversation_history()
    st.session_state["messages"].append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("ai"):
        try:
            with st.spinner("Thinking..."):
                result = request_json(
                    state.runtime.api_url,
                    "POST",
                    "/api/rag",
                    payload={
                        "question": user_input,
                        "conversation_history": history,
                    },
                )
        except ApiError as exc:
            content = f"Request failed: {exc}"
            details = {
                "error": str(exc),
                "error_code": exc.code,
                "retryable": exc.retryable,
            }
            if exc.connection_failure:
                state.runtime.stop_api()
                st.error("The T2C API stopped before returning a response.")
            else:
                st.error(str(exc))
        else:
            content = result.get("output") or "No answer returned."
            details = _pipeline_details(result)
            with st.expander("Pipeline debug", expanded=False):
                _render_pipeline_details(details)
            st.markdown(content, unsafe_allow_html=True)

    st.session_state["messages"].append(
        {
            "role": "ai",
            "content": content,
            "details": details,
        }
    )
