"""
LLM factory - creates ChatOpenAI instances from config.
Module-level singletons: each LLM role is instantiated once and reused,
avoiding the overhead of creating new ChatOpenAI objects on every request.
No Streamlit dependency.
"""
from __future__ import annotations
import logging
import threading
from config import get_settings
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)
_lock = threading.Lock()

_main_llm: ChatOpenAI | None = None
_interpreter_llm: ChatOpenAI | None = None
_grounding_llm: ChatOpenAI | None = None
_cypher_llm: ChatOpenAI | None = None
_qa_llm: ChatOpenAI | None = None

def _create_llm(temperature: float, model: str | None = None) -> ChatOpenAI:
    settings = get_settings()
    kwargs = {
        "api_key": settings.OPENAI_API_KEY or "local",
        "temperature": temperature,
        "model": model or settings.OPENAI_MODEL,
        "request_timeout": 30,
    }
    if settings.OPENAI_BASE_URL:
        kwargs["base_url"] = settings.OPENAI_BASE_URL
    return ChatOpenAI(
        **kwargs,
    )

def get_main_llm() -> ChatOpenAI:
    global _main_llm
    if _main_llm is None:
        with _lock:
            if _main_llm is None:
                logger.info("[LLM] Initializing main LLM...")
                _main_llm = _create_llm(temperature=0.5)
    return _main_llm

def get_interpreter_llm() -> ChatOpenAI:
    global _interpreter_llm
    if _interpreter_llm is None:
        with _lock:
            if _interpreter_llm is None:
                logger.info("[LLM] Initializing interpreter LLM...")
                _interpreter_llm = _create_llm(temperature=0)
    return _interpreter_llm

def get_grounding_llm() -> ChatOpenAI:
    global _grounding_llm
    if _grounding_llm is None:
        with _lock:
            if _grounding_llm is None:
                logger.info("[LLM] Initializing grounding LLM...")
                _grounding_llm = _create_llm(temperature=0)
    return _grounding_llm

def get_cypher_llm() -> ChatOpenAI:
    global _cypher_llm
    if _cypher_llm is None:
        with _lock:
            if _cypher_llm is None:
                logger.info("[LLM] Initializing Cypher LLM...")
                _cypher_llm = _create_llm(temperature=0)
    return _cypher_llm

def get_qa_llm() -> ChatOpenAI:
    global _qa_llm
    if _qa_llm is None:
        with _lock:
            if _qa_llm is None:
                logger.info("[LLM] Initializing QA LLM...")
                _qa_llm = _create_llm(temperature=0.7)
    return _qa_llm

def reset_llms() -> None:
    global _main_llm, _interpreter_llm, _grounding_llm, _cypher_llm, _qa_llm
    with _lock:
        _main_llm = None
        _interpreter_llm = None
        _grounding_llm = None
        _cypher_llm = None
        _qa_llm = None
    logger.info("[LLM] All LLM singletons reset.")
