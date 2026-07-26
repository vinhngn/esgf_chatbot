"""Build role-separated prompts for profile-grounded Cypher generation."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

SYSTEM_PROMPT = """Translate the question into one read-only Neo4j Cypher query.
Output only raw Cypher.

Evidence priority:
1. The question defines semantics and requested output.
2. Runtime schema defines valid graph structure and properties.
3. Live evidence defines verified literals and values.
4. A learned contract is optional structural guidance.

Infer output, graph constraints, filters, aggregation, ordering, and cardinality
from the question. Use only supplied schema and evidence. Keep patterns connected
and reuse a variable when one entity has multiple roles. Never invent schema or
values, and never copy a learned contract that conflicts with the question."""


def build_coder_prompt(
    *,
    schema: str,
    learned_context: str,
    question: str,
    current_cypher: str,
    last_error: str | None,
    evidence_context: str = "",
) -> str:
    """Build the request-specific Human message."""
    sections = [
        f"=== RUNTIME NEO4J SCHEMA ===\n{schema.strip()}",
    ]
    if learned_context.strip():
        sections.append(f"=== RETRIEVED PROFILE EVIDENCE ===\n{learned_context.strip()}")
    if evidence_context.strip():
        sections.append(f"=== LIVE GRAPH EVIDENCE ===\n{evidence_context.strip()}")
    if last_error and current_cypher:
        sections.append(
            "=== EXECUTION FEEDBACK ===\n"
            f"Previous query:\n{current_cypher.strip()}\n"
            f"Database feedback:\n{last_error.strip()}\n"
            "Repair only the contradicted part; preserve supported structure."
        )
    sections.append(f"=== CURRENT QUESTION ===\n{question.strip()}")
    return "\n\n".join(sections)


def build_coder_messages(**kwargs) -> list[SystemMessage | HumanMessage]:
    """Return explicit System/Human roles for chat-model providers."""
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=build_coder_prompt(**kwargs)),
    ]
