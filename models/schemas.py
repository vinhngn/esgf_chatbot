"""Pydantic data models."""
from __future__ import annotations

from pydantic import BaseModel


class GraphToolInput(BaseModel):
    question: str
    rewritten: str
    verified_triples: list[tuple[str, str, str]]
    instance_triples: list[tuple[str, str, str]]
    history: str


class ChatMessage(BaseModel):
    role: str  # "user" or "ai"
    content: str


class ConversationTurn(BaseModel):
    input: str
    output: str
