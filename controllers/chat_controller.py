from __future__ import annotations

import streamlit as st

from services.rag_service import get_results
from services.analytics_service import track, generate_session_id


def get_session_id() -> str:
    """Get or create session ID for analytics."""
    if "SESSION_ID" not in st.session_state:
        st.session_state["SESSION_ID"] = generate_session_id()
    return st.session_state["SESSION_ID"]


def _build_conversation_history() -> list[dict[str, str]]:
    """Build conversation history from Streamlit session state."""
    history: list[dict[str, str]] = []
    for msg in st.session_state.get("messages", []):
        if msg["role"] == "user":
            history.append({"input": msg["content"], "output": ""})
        elif msg["role"] == "ai" and history:
            history[-1]["output"] = msg["content"]
    return history


def handle_user_message(user_input: str) -> dict:
    """
    Process a user message through the RAG pipeline.
    Returns the full result dict from rag_service.
    """
    session_id = get_session_id()
    track("rag_demo", "question_submitted", {"question": user_input}, session_id)

    conversation_history = _build_conversation_history()
    result = get_results(user_input, conversation_history)

    track(
        "rag_demo",
        "ai_response",
        {"type": "rag_agent", "answer": result["output"]},
        session_id,
    )

    return result


def handle_feedback(score: str) -> None:
    """Track user feedback."""
    session_id = get_session_id()
    last_bot_message = ""
    messages = st.session_state.get("messages", [])
    if messages:
        last_bot_message = messages[-1].get("content", "")

    track(
        "rag_demo",
        "feedback_submitted",
        {"score": score, "bot_message": last_bot_message},
        session_id,
    )
