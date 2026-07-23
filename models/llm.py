"""LLM factory with ordered OpenAI/local fallback routing."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from langchain_openai import ChatOpenAI

from config import get_settings


logger = logging.getLogger(__name__)
_lock = threading.Lock()


@dataclass(frozen=True)
class _Provider:
    name: str
    llm: ChatOpenAI


class RoutedChatModel:
    """Invoke providers in order and fall back only when invocation fails."""

    def __init__(self, providers: list[_Provider]) -> None:
        if not providers:
            raise RuntimeError(
                "No LLM provider is configured. Set OPENAI_API_KEY or "
                "LOCAL_LLM_BASE_URL and LOCAL_LLM_MODEL."
            )
        self._providers = tuple(providers)

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(provider.name for provider in self._providers)

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for index, provider in enumerate(self._providers):
            try:
                if index:
                    logger.info("[LLM] Invoking fallback provider: %s", provider.name)
                return provider.llm.invoke(input, config=config, **kwargs)
            except Exception as exc:
                last_error = exc
                has_fallback = index + 1 < len(self._providers)
                logger.warning(
                    "[LLM] Provider %s failed (%s)%s",
                    provider.name,
                    type(exc).__name__,
                    "; switching provider" if has_fallback else "",
                )
        assert last_error is not None
        raise last_error


_main_llm: RoutedChatModel | None = None
_interpreter_llm: RoutedChatModel | None = None
_grounding_llm: RoutedChatModel | None = None
_cypher_llm: RoutedChatModel | None = None
_qa_llm: RoutedChatModel | None = None


def _has_real_openai_key(api_key: str) -> bool:
    value = (api_key or "").strip()
    return bool(value and value != "sk-your-key-here")


def _chat_openai(
    *,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float,
    timeout: float,
    max_retries: int,
) -> ChatOpenAI:
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "model": model,
        "temperature": temperature,
        "request_timeout": timeout,
        "max_retries": max_retries,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def _create_llm(temperature: float, model: str | None = None) -> RoutedChatModel:
    settings = get_settings()
    providers: dict[str, _Provider] = {}

    if _has_real_openai_key(settings.OPENAI_API_KEY):
        providers["openai"] = _Provider(
            name="openai",
            llm=_chat_openai(
                model=model or settings.OPENAI_MODEL,
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
                temperature=temperature,
                timeout=settings.OPENAI_REQUEST_TIMEOUT,
                max_retries=settings.LLM_PROVIDER_MAX_RETRIES,
            ),
        )

    if settings.LOCAL_LLM_BASE_URL and settings.LOCAL_LLM_MODEL:
        providers["local"] = _Provider(
            name="local",
            llm=_chat_openai(
                model=settings.LOCAL_LLM_MODEL,
                api_key=settings.LOCAL_LLM_API_KEY or "local",
                base_url=settings.LOCAL_LLM_BASE_URL,
                temperature=temperature,
                timeout=settings.LOCAL_LLM_REQUEST_TIMEOUT,
                max_retries=settings.LLM_PROVIDER_MAX_RETRIES,
            ),
        )

    order = ["local", "openai"] if settings.LLM_LOCAL_FIRST else ["openai", "local"]
    selected = [providers[name] for name in order if name in providers]
    if not settings.LLM_FALLBACK_ENABLED:
        selected = selected[:1]

    routed = RoutedChatModel(selected)
    logger.info("[LLM] Provider route: %s", " -> ".join(routed.provider_names))
    return routed


def get_main_llm() -> RoutedChatModel:
    global _main_llm
    if _main_llm is None:
        with _lock:
            if _main_llm is None:
                logger.info("[LLM] Initializing main LLM...")
                _main_llm = _create_llm(temperature=0.5)
    return _main_llm


def get_interpreter_llm() -> RoutedChatModel:
    global _interpreter_llm
    if _interpreter_llm is None:
        with _lock:
            if _interpreter_llm is None:
                logger.info("[LLM] Initializing interpreter LLM...")
                _interpreter_llm = _create_llm(temperature=0)
    return _interpreter_llm


def get_grounding_llm() -> RoutedChatModel:
    global _grounding_llm
    if _grounding_llm is None:
        with _lock:
            if _grounding_llm is None:
                logger.info("[LLM] Initializing grounding LLM...")
                _grounding_llm = _create_llm(temperature=0)
    return _grounding_llm


def get_cypher_llm() -> RoutedChatModel:
    global _cypher_llm
    if _cypher_llm is None:
        with _lock:
            if _cypher_llm is None:
                logger.info("[LLM] Initializing Cypher LLM...")
                _cypher_llm = _create_llm(temperature=0)
    return _cypher_llm


def get_qa_llm() -> RoutedChatModel:
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
