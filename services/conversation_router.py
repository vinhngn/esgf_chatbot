"""LLM-tagged routing for conversational and graph-backed questions."""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field


class QuestionRoute(str, Enum):
    SOCIAL = "SOCIAL"
    GRAPH_QUERY = "GRAPH_QUERY"
    GRAPH_FOLLOW_UP = "GRAPH_FOLLOW_UP"
    CONVERSATION_RECALL = "CONVERSATION_RECALL"
    CAPABILITY = "CAPABILITY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    CLARIFY = "CLARIFY"


class AnswerSource(str, Enum):
    NONE = "NONE"
    NEO4J = "NEO4J"
    HISTORY = "HISTORY"
    ASSISTANT_POLICY = "ASSISTANT_POLICY"


class HistoryDependency(str, Enum):
    NONE = "NONE"
    OPTIONAL = "OPTIONAL"
    REQUIRED = "REQUIRED"


class QuestionDecision(BaseModel):
    """Validated control decision returned by the language router."""

    model_config = ConfigDict(frozen=True)

    route: QuestionRoute
    speech_act: str = ""
    standalone_question: str = ""
    history_dependency: HistoryDependency = HistoryDependency.NONE
    referenced_turn_ids: list[str] = Field(default_factory=list)
    answer_source: AnswerSource = AnswerSource.NONE
    confidence: float = Field(default=0, ge=0, le=1)
    direct_answer: str = ""
    clarification: str = ""

    @property
    def requires_graph(self) -> bool:
        return self.route in {
            QuestionRoute.GRAPH_QUERY,
            QuestionRoute.GRAPH_FOLLOW_UP,
        }


_ROUTER_SYSTEM_PROMPT = """You route requests for a conversational assistant backed by one connected Neo4j database.
Return one JSON object only. Do not write Cypher.

Available routes:
- SOCIAL: greeting, thanks, farewell, or casual conversation needing no facts.
- GRAPH_QUERY: a standalone request answerable from the connected Neo4j database.
- GRAPH_FOLLOW_UP: a database request whose meaning depends on a prior turn.
- CONVERSATION_RECALL: asks what was said in a prior turn; answer only from cited turns.
- CAPABILITY: asks what this assistant or connected database assistant can do.
- OUT_OF_SCOPE: requests external facts or actions unsupported by the connected graph.
- CLARIFY: the request is too ambiguous to resolve safely.

Rules:
- Understand meaning in any language; never classify from a keyword list.
- Convert graph requests into a complete standalone_question.
- For history-dependent requests, cite only provided turn IDs.
- Never use conversation history as database evidence.
- GRAPH routes must use answer_source NEO4J and must not include direct_answer.
- CONVERSATION_RECALL must use answer_source HISTORY.
- SOCIAL, CAPABILITY, and OUT_OF_SCOPE use ASSISTANT_POLICY.
- If a reference cannot be resolved confidently, return CLARIFY.
- A direct answer must not claim database facts or external facts.

JSON shape:
{
  "route": "SOCIAL|GRAPH_QUERY|GRAPH_FOLLOW_UP|CONVERSATION_RECALL|CAPABILITY|OUT_OF_SCOPE|CLARIFY",
  "speech_act": "short language-neutral label",
  "standalone_question": "complete database question or empty",
  "history_dependency": "NONE|OPTIONAL|REQUIRED",
  "referenced_turn_ids": ["turn-N"],
  "answer_source": "NONE|NEO4J|HISTORY|ASSISTANT_POLICY",
  "confidence": 0.0,
  "direct_answer": "answer for a non-graph route or empty",
  "clarification": "one concise question when clarification is required or empty"
}"""


def _bounded_history(
    history: list[dict[str, str]],
    *,
    max_turns: int = 3,
    max_text_chars: int = 1_200,
) -> list[dict[str, str]]:
    start = max(0, len(history) - max_turns)
    bounded: list[dict[str, str]] = []
    for index, turn in enumerate(history[start:], start=start + 1):
        if not isinstance(turn, dict):
            continue
        bounded.append(
            {
                "turn_id": str(turn.get("turn_id") or f"turn-{index}"),
                "user": str(turn.get("input") or "")[:max_text_chars],
                "assistant": str(turn.get("output") or "")[:max_text_chars],
            }
        )
    return bounded


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*", "", text or "", flags=re.IGNORECASE)
    cleaned = cleaned.replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Question router did not return a JSON object")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Question router returned a non-object JSON value")
    return payload


def _clarification(message: str) -> QuestionDecision:
    return QuestionDecision(
        route=QuestionRoute.CLARIFY,
        speech_act="REQUEST_CLARIFICATION",
        answer_source=AnswerSource.NONE,
        confidence=0,
        clarification=message,
    )


def _enforce_decision(
    decision: QuestionDecision,
    *,
    question: str,
    available_turn_ids: set[str],
) -> QuestionDecision:
    if decision.confidence < 0.55:
        return _clarification(
            decision.clarification
            or "Could you clarify what information you want?"
        )

    invalid_references = set(decision.referenced_turn_ids) - available_turn_ids
    if invalid_references:
        return _clarification(
            "I could not identify the previous message you are referring to. "
            "Could you restate the relevant part?"
        )

    if decision.route == QuestionRoute.GRAPH_QUERY:
        return decision.model_copy(
            update={
                "standalone_question": decision.standalone_question or question,
                "history_dependency": HistoryDependency.NONE,
                "referenced_turn_ids": [],
                "answer_source": AnswerSource.NEO4J,
                "direct_answer": "",
            }
        )

    if decision.route == QuestionRoute.GRAPH_FOLLOW_UP:
        if not decision.standalone_question or not decision.referenced_turn_ids:
            return _clarification(
                decision.clarification
                or "Which previous result should this database question refer to?"
            )
        return decision.model_copy(
            update={
                "history_dependency": HistoryDependency.REQUIRED,
                "answer_source": AnswerSource.NEO4J,
                "direct_answer": "",
            }
        )

    if decision.route == QuestionRoute.CONVERSATION_RECALL:
        if not decision.referenced_turn_ids or not decision.direct_answer:
            return _clarification(
                decision.clarification
                or "Which previous message would you like me to recall?"
            )
        return decision.model_copy(
            update={
                "history_dependency": HistoryDependency.REQUIRED,
                "answer_source": AnswerSource.HISTORY,
                "standalone_question": "",
            }
        )

    if decision.route == QuestionRoute.CLARIFY:
        return decision.model_copy(
            update={
                "answer_source": AnswerSource.NONE,
                "standalone_question": "",
                "direct_answer": "",
                "clarification": (
                    decision.clarification
                    or "Could you clarify what information you want?"
                ),
            }
        )

    return decision.model_copy(
        update={
            "answer_source": AnswerSource.ASSISTANT_POLICY,
            "standalone_question": "",
            "referenced_turn_ids": [],
        }
    )


def classify_question(
    question: str,
    conversation_history: list[dict[str, str]],
    llm,
) -> QuestionDecision:
    """Classify and resolve one request without accessing Neo4j."""
    bounded_history = _bounded_history(conversation_history)
    human_payload = {
        "question": question.strip(),
        "conversation_history": bounded_history,
    }
    response = llm.invoke(
        [
            SystemMessage(content=_ROUTER_SYSTEM_PROMPT),
            HumanMessage(
                content=json.dumps(
                    human_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ),
        ]
    )
    decision = QuestionDecision.model_validate(
        _extract_json(str(response.content))
    )
    return _enforce_decision(
        decision,
        question=question,
        available_turn_ids={
            turn["turn_id"] for turn in bounded_history
        },
    )


__all__ = [
    "AnswerSource",
    "HistoryDependency",
    "QuestionDecision",
    "QuestionRoute",
    "classify_question",
]
