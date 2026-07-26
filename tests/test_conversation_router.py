from __future__ import annotations

import json
from types import SimpleNamespace

from services.conversation_router import (
    AnswerSource,
    QuestionDecision,
    QuestionRoute,
    classify_question,
)


class FakeChatModel:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.messages = []

    def invoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content=json.dumps(self.payload))


def test_social_question_is_tagged_without_graph_access() -> None:
    llm = FakeChatModel(
        {
            "route": "SOCIAL",
            "speech_act": "GREETING",
            "standalone_question": "",
            "history_dependency": "NONE",
            "referenced_turn_ids": [],
            "answer_source": "ASSISTANT_POLICY",
            "confidence": 0.99,
            "direct_answer": "Hello. How can I help with your graph data?",
            "clarification": "",
        }
    )

    decision = classify_question("Hello, how are you?", [], llm)

    assert decision.route == QuestionRoute.SOCIAL
    assert decision.answer_source == AnswerSource.ASSISTANT_POLICY
    assert decision.requires_graph is False
    assert decision.direct_answer.startswith("Hello")


def test_graph_follow_up_is_resolved_to_a_standalone_question() -> None:
    llm = FakeChatModel(
        {
            "route": "GRAPH_FOLLOW_UP",
            "speech_act": "REQUEST_INFORMATION",
            "standalone_question": "Which of the German customers placed orders?",
            "history_dependency": "REQUIRED",
            "referenced_turn_ids": ["turn-2"],
            "answer_source": "NEO4J",
            "confidence": 0.94,
            "direct_answer": "",
            "clarification": "",
        }
    )
    history = [
        {"input": "List suppliers.", "output": "There are 29 suppliers."},
        {
            "input": "List customers from Germany.",
            "output": "There are 11 German customers.",
        },
    ]

    decision = classify_question("Which of those placed orders?", history, llm)

    assert decision.route == QuestionRoute.GRAPH_FOLLOW_UP
    assert decision.requires_graph is True
    assert decision.standalone_question == (
        "Which of the German customers placed orders?"
    )
    assert decision.referenced_turn_ids == ["turn-2"]


def test_invalid_history_reference_is_changed_to_clarification() -> None:
    llm = FakeChatModel(
        {
            "route": "CONVERSATION_RECALL",
            "speech_act": "REQUEST_RECALL",
            "standalone_question": "",
            "history_dependency": "REQUIRED",
            "referenced_turn_ids": ["turn-999"],
            "answer_source": "HISTORY",
            "confidence": 0.91,
            "direct_answer": "The previous answer was 11.",
            "clarification": "",
        }
    )

    decision = classify_question(
        "What did you say above?",
        [{"input": "How many?", "output": "There are 11."}],
        llm,
    )

    assert decision.route == QuestionRoute.CLARIFY
    assert decision.answer_source == AnswerSource.NONE
    assert decision.requires_graph is False
    assert decision.direct_answer == ""
    assert decision.clarification


def test_history_given_to_the_tagger_is_bounded() -> None:
    llm = FakeChatModel(
        {
            "route": "GRAPH_QUERY",
            "speech_act": "REQUEST_INFORMATION",
            "standalone_question": "List all products.",
            "history_dependency": "NONE",
            "referenced_turn_ids": [],
            "answer_source": "NEO4J",
            "confidence": 0.98,
            "direct_answer": "",
            "clarification": "",
        }
    )
    history = [
        {"input": f"old question {index}", "output": f"old answer {index}"}
        for index in range(8)
    ]

    classify_question("List all products.", history, llm)

    prompt = llm.messages[-1].content
    assert "old question 4" not in prompt
    assert "old question 5" in prompt
    assert "old question 7" in prompt


def test_social_route_bypasses_the_graph_pipeline(monkeypatch) -> None:
    from services import rag_service

    decision = QuestionDecision(
        route=QuestionRoute.SOCIAL,
        speech_act="GREETING",
        answer_source=AnswerSource.ASSISTANT_POLICY,
        confidence=0.99,
        direct_answer="Hello. How can I help?",
    )
    monkeypatch.setattr(
        rag_service,
        "classify_question",
        lambda *_args, **_kwargs: decision,
        raising=False,
    )
    monkeypatch.setattr(
        rag_service,
        "_run_pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("social route must not access the graph")
        ),
    )

    result = rag_service.process_question("Hello", [])

    assert result["output"] == "Hello. How can I help?"
    assert result["question_tag"] == "SOCIAL"
    assert result["cypher_query"] == ""


def test_graph_route_uses_the_resolved_question_without_triple_retries(
    monkeypatch,
) -> None:
    from models import llm as llm_models
    from services import rag_service

    decision = QuestionDecision(
        route=QuestionRoute.GRAPH_FOLLOW_UP,
        speech_act="REQUEST_INFORMATION",
        standalone_question="Which German customers placed orders?",
        history_dependency="REQUIRED",
        referenced_turn_ids=["turn-1"],
        answer_source=AnswerSource.NEO4J,
        confidence=0.96,
    )
    monkeypatch.setattr(
        rag_service,
        "classify_question",
        lambda *_args, **_kwargs: decision,
        raising=False,
    )
    monkeypatch.setattr(
        rag_service,
        "extract_triples_with_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("resolved graph routes must skip triple retries")
        ),
    )
    captured = {}

    def fake_invoke_chain(question, trace_id=None, **_kwargs):
        captured["question"] = question
        return {
            "query": "MATCH (c:Customer) RETURN c.companyName",
            "result": [{"c.companyName": "Alfreds Futterkiste"}],
        }

    monkeypatch.setattr(rag_service, "invoke_chain", fake_invoke_chain)
    monkeypatch.setattr(
        llm_models,
        "get_interpreter_llm",
        lambda: object(),
    )
    monkeypatch.setattr(
        llm_models,
        "get_main_llm",
        lambda: SimpleNamespace(
            invoke=lambda _prompt: SimpleNamespace(
                content="Alfreds Futterkiste [[button_query]]"
            )
        ),
    )

    result = rag_service.process_question(
        "Which of those placed orders?",
        [{"input": "List German customers.", "output": "There are 11."}],
    )

    assert captured["question"] == "Which German customers placed orders?"
    assert result["question_tag"] == "GRAPH_FOLLOW_UP"
    assert result["answer_source"] == "NEO4J"
    assert result["rewritten"] == "Which German customers placed orders?"
