"""
Streamlit main app - View layer only.
All business logic is in controllers/services.
"""

from __future__ import annotations

import os
import sys

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import threading

import streamlit as st
from config import get_settings
from constants import TITLE
from controllers.chat_controller import (
    get_session_id,
    handle_feedback,
    handle_user_message,
)
from langchain_community.cache import InMemoryCache
from langchain_core.globals import set_llm_cache
from services.analytics_service import track
from streamlit_feedback import streamlit_feedback

from views.sidebar import sidebar

logger = logging.getLogger(__name__)

# --- Start Flask API in background thread (once per process) ---
_flask_launched = False


def _ensure_flask_started():
    global _flask_launched
    if _flask_launched:
        return
    _flask_launched = True

    _settings = get_settings()
    _port = _settings.FLASK_PORT
    _host = _settings.FLASK_HOST

    def _run_flask():
        try:
            from views.flask_api import app

            logger.info("Flask API starting on %s:%s", _host, _port)
            app.run(host=_host, port=_port, debug=False, threaded=True)
        except OSError as e:
            logger.error(
                "Flask API failed to start on %s:%s — port may already be in use. Error: %s",
                _host,
                _port,
                e,
            )
        except Exception as e:
            logger.error("Flask API unexpected error: %s", e)

    flask_thread = threading.Thread(target=_run_flask, daemon=True, name="flask-api")
    flask_thread.start()
    logger.info("Flask API thread launched on %s:%s", _host, _port)


_ensure_flask_started()

# LangChain caching
set_llm_cache(InMemoryCache())

# Track app start
if "SESSION_ID" not in st.session_state:
    session_id = get_session_id()
    track("rag_demo", "appStarted", {}, session_id)

# Page layout
settings = get_settings()
st.markdown(TITLE, unsafe_allow_html=True)
sidebar()

# Show Flask API info in sidebar
flask_url = f"{settings.SERVER_URL}:{settings.FLASK_PORT}"
st.sidebar.markdown("---")
st.sidebar.caption(f"Flask API: {flask_url}")

placeholder = st.empty()
emoji_feedback = st.empty()
user_placeholder = st.empty()

# Initialize message history
if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "ai",
            "content": (
                "This is a Proof of Concept application which shows how GenAI "
                "can be used with Neo4j to build and consume Knowledge Graphs "
                "using cypher query.\nSee the sidebar for more information!"
            ),
        },
    ]

# Display chat messages from history
with placeholder.container():
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"], unsafe_allow_html=True)

# Free questions check
if "FREE_QUESTIONS_REMAINING" not in st.session_state:
    st.session_state["FREE_QUESTIONS_REMAINING"] = settings.FREE_QUESTIONS_PER_SESSION

if st.session_state["FREE_QUESTIONS_REMAINING"] <= 0:
    st.warning("Thank you for trying out the demo. Free questions exhausted.")
    st.stop()

# User input
if "sample" in st.session_state and st.session_state["sample"] is not None:
    user_input = st.session_state["sample"]
else:
    user_input = st.chat_input(placeholder="Ask a question", key="user_input")

if user_input:
    with user_placeholder.container():
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("ai"):
            with st.spinner("Thinking..."):
                result = handle_user_message(user_input)
                content = result["output"]

                # --- Debug expander ---
                cypher = result.get("cypher_query", "")

                with st.expander("Pipeline debug", expanded=False):
                    if cypher:
                        st.markdown("**Generated Cypher:**")
                        st.code(cypher, language="cypher")
                    else:
                        st.markdown("**Generated Cypher:** —")

                st.session_state.messages.append({"role": "ai", "content": content})
                st.session_state["FREE_QUESTIONS_REMAINING"] -= 1

            st.markdown(content, unsafe_allow_html=True)

    # Reset sample quick select
    if "sample" in st.session_state and st.session_state["sample"] is not None:
        st.session_state["sample"] = None
        st.chat_input(placeholder="Ask a question", key="user_input")

    emoji_feedback = st.empty()

# Emoji feedback
with emoji_feedback.container():
    feedback = streamlit_feedback(feedback_type="thumbs")
    if feedback:
        handle_feedback(feedback["score"])
