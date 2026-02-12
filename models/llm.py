"""
LLM factory - creates ChatOpenAI instances from config.
No Streamlit dependency.
"""
from __future__ import annotations

from langchain_openai import ChatOpenAI

from config import get_settings


def create_llm(temperature: float = 0.5, model: str = "gpt-4o-mini") -> ChatOpenAI:
    """Create a ChatOpenAI instance using config API key."""
    settings = get_settings()
    return ChatOpenAI(
        api_key=settings.OPENAI_API_KEY,
        temperature=temperature,
        model=model,
    )


def get_main_llm() -> ChatOpenAI:
    return create_llm(temperature=0.5)


def get_interpreter_llm() -> ChatOpenAI:
    return create_llm(temperature=0.3)


def get_cypher_llm() -> ChatOpenAI:
    return create_llm(temperature=0.3)


def get_qa_llm() -> ChatOpenAI:
    return create_llm(temperature=0.7)
