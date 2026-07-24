"""Build role-separated prompts for profile-grounded Cypher generation."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

SYSTEM_PROMPT = """You generate one read-only Neo4j Cypher query.

Return only the raw Cypher query. Do not use Markdown or explain the answer.

Use evidence in this order:
1. The runtime Neo4j schema is authoritative for labels, relationship types,
   properties, and relationship direction.
2. Retrieved profile evidence guides intent, graph traversal, query shape,
   and return shape only when it matches the current question.
3. Live graph evidence validates entity literals and usable properties.
4. The user question controls filters, aggregation, ordering, limits, and the
   exact requested output.

If sources conflict, obey the runtime schema for graph structure and the user
question for requested semantics. Never invent schema elements or literal
values. Do not copy a retrieved scaffold whose target or intent does not match.

Preserve identity constraints: when one entity must satisfy multiple roles,
reuse one variable. Avoid disconnected MATCH components and accidental
Cartesian products. Apply filters before aggregation unless post-aggregation
filtering is explicitly required. For ranking, order by the requested metric
before applying LIMIT. Return only the requested entity properties or computed
metrics, using aliases consistently.

Generate the best query privately, then emit only that query."""


def build_coder_prompt(
    *,
    schema: str,
    domain_template: str,
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
    if domain_template.strip():
        sections.append(
            "=== FALLBACK DOMAIN CONTEXT ===\n"
            f"{domain_template.strip()}\n"
            "Use this only where it agrees with the runtime schema."
        )
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
