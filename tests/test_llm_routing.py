from __future__ import annotations

from types import SimpleNamespace

import pytest

import models.llm as llm_module
from models.llm import RoutedChatModel, _Provider


class FakeLLM:
    def __init__(self, *, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def invoke(self, input, config=None, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def test_primary_success_does_not_call_fallback() -> None:
    primary = FakeLLM(result="primary")
    fallback = FakeLLM(result="fallback")
    routed = RoutedChatModel(
        [
            _Provider("openai", primary),
            _Provider("local", fallback),
        ]
    )

    assert routed.invoke("question") == "primary"
    assert primary.calls == 1
    assert fallback.calls == 0


def test_primary_failure_calls_fallback_once() -> None:
    primary = FakeLLM(error=TimeoutError("timeout"))
    fallback = FakeLLM(result="fallback")
    routed = RoutedChatModel(
        [
            _Provider("openai", primary),
            _Provider("local", fallback),
        ]
    )

    assert routed.invoke("question") == "fallback"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_all_fail_raises_last_provider_error() -> None:
    routed = RoutedChatModel(
        [
            _Provider("openai", FakeLLM(error=TimeoutError("openai"))),
            _Provider("local", FakeLLM(error=ConnectionError("local"))),
        ]
    )

    with pytest.raises(ConnectionError, match="local"):
        routed.invoke("question")


def test_create_llm_respects_local_first(monkeypatch) -> None:
    settings = SimpleNamespace(
        OPENAI_API_KEY="sk-test",
        OPENAI_MODEL="gpt-4o-mini",
        OPENAI_BASE_URL="",
        OPENAI_REQUEST_TIMEOUT=30,
        LOCAL_LLM_API_KEY="local",
        LOCAL_LLM_MODEL="local-model",
        LOCAL_LLM_BASE_URL="http://localhost:20128/v1",
        LOCAL_LLM_REQUEST_TIMEOUT=120,
        LLM_PROVIDER_MAX_RETRIES=0,
        LLM_LOCAL_FIRST=True,
        LLM_FALLBACK_ENABLED=True,
    )
    monkeypatch.setattr(llm_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        llm_module, "_chat_openai", lambda **kwargs: FakeLLM(result=kwargs["model"])
    )

    routed = llm_module._create_llm(temperature=0)

    assert routed.provider_names == ("local", "openai")
    assert routed.invoke("question") == "local-model"


def test_missing_openai_key_uses_local_only(monkeypatch) -> None:
    settings = SimpleNamespace(
        OPENAI_API_KEY="",
        OPENAI_MODEL="gpt-4o-mini",
        OPENAI_BASE_URL="",
        OPENAI_REQUEST_TIMEOUT=30,
        LOCAL_LLM_API_KEY="local",
        LOCAL_LLM_MODEL="local-model",
        LOCAL_LLM_BASE_URL="http://localhost:20128/v1",
        LOCAL_LLM_REQUEST_TIMEOUT=120,
        LLM_PROVIDER_MAX_RETRIES=0,
        LLM_LOCAL_FIRST=False,
        LLM_FALLBACK_ENABLED=True,
    )
    monkeypatch.setattr(llm_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        llm_module, "_chat_openai", lambda **kwargs: FakeLLM(result=kwargs["model"])
    )

    routed = llm_module._create_llm(temperature=0)

    assert routed.provider_names == ("local",)
