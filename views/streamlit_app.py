"""
Streamlit main app - View layer only.
All business logic is in controllers/services.
"""
from __future__ import annotations

import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from langchain_core.globals import set_llm_cache
from langchain_community.cache import InMemoryCache
from streamlit_feedback import streamlit_feedback

from config import get_settings
from constants import TITLE
from controllers.chat_controller import handle_user_message, handle_feedback, get_session_id
from services.analytics_service import track
from views.sidebar import sidebar

# LangChain caching
set_llm_cache(InMemoryCache())

# Track app start
if "SESSION_ID" not in st.session_state:
    session_id = get_session_id()
    track("rag_demo", "appStarted", {}, session_id)

# Page layout
st.markdown(TITLE, unsafe_allow_html=True)
sidebar()
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
settings = get_settings()
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
            with st.spinner("..."):
                result = handle_user_message(user_input)
                content = result["output"]

                # Show generated Cypher if available
                cypher = result.get("cypher_query", "")
                if cypher:
                    st.code(cypher, language="cypher")

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
