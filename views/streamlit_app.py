"""Streamlit entry point with separate Chat, Connection, and Inspector pages."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from views.logging_config import configure_streamlit_logging
from views.studio.benchmark_page import render as render_benchmark
from views.studio.chat_view import render as render_chat
from views.studio.connection_view import render as render_connection
from views.studio.inspector_page import render as render_inspector
from views.studio.state import get_compatible_studio_state

configure_streamlit_logging()

st.set_page_config(
    page_title="ClimatePub4KG",
    layout="wide",
    initial_sidebar_state="expanded",
)

state = get_compatible_studio_state()


def chat_page() -> None:
    render_chat(state)


def connection_page() -> None:
    render_connection(state)


def inspector_page() -> None:
    render_inspector(state)


def benchmark_page() -> None:
    render_benchmark(state)


page = st.navigation(
    [
        st.Page(chat_page, title="Chat", default=True),
        st.Page(connection_page, title="Connection"),
        st.Page(benchmark_page, title="Benchmark"),
        st.Page(inspector_page, title="Inspector"),
    ]
)
page.run()
