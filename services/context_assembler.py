"""
Dynamic Context Assembler — builds the minimal, high-signal prompt for each question.

Core principle (Anthropic): "Find the smallest set of high-signal tokens
that maximize the likelihood of the desired outcome."

Instead of dumping all schema + all knowledge + all examples into every prompt,
this module analyzes the question and selects ONLY what's relevant:

  1. Relevant schema elements (from Schema Linker)
  2. Relevant knowledge facts (from Question Analyzer)
  3. Relevant few-shot examples (matched by intent/pattern)
  4. Minimal rules (only what applies to this question type)

Result: ~500-800 tokens instead of ~3000. LLM focuses on what matters.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AssembledContext:
    """The minimal context assembled for a specific question."""
    schema: str = ""
    knowledge: str = ""
    examples: str = ""
    rules: str = ""
    question: str = ""
    total_tokens_estimate: int = 0


def assemble(
    question: str,
    enriched_question: str,
    cropped_schema: str,
    knowledge: dict,
    all_examples: list[dict],
    question_intents: dict,
) -> AssembledContext:
    """
    Assemble minimal context for a specific question.

    Args:
        question: original question
        enriched_question: question with [ANALYSIS] annotations
        cropped_schema: already cropped by Schema Linker
        knowledge: full knowledge dict
        all_examples: list of {"question": str, "cypher": str, "tags": list[str]}
        question_intents: extracted intents from Question Analyzer
    """
    ctx = AssembledContext()

    # 1. Schema — already cropped by Schema Linker
    ctx.schema = cropped_schema

    # 2. Knowledge — select only relevant facts
    ctx.knowledge = _select_knowledge(knowledge, question_intents)

    # 3. Examples — select by intent matching
    ctx.examples = _select_examples(all_examples, question_intents, max_examples=3)

    # 4. Rules — select by question type
    ctx.rules = _select_rules(question_intents)

    # 5. Question
    ctx.question = enriched_question

    # Estimate tokens (~4 chars per token)
    total_chars = len(ctx.schema) + len(ctx.knowledge) + len(ctx.examples) + len(ctx.rules) + len(ctx.question)
    ctx.total_tokens_estimate = total_chars // 4

    logger.info(
        "[ContextAssembler] Assembled: schema=%d, knowledge=%d, examples=%d, rules=%d, question=%d chars (~%d tokens)",
        len(ctx.schema), len(ctx.knowledge), len(ctx.examples),
        len(ctx.rules), len(ctx.question), ctx.total_tokens_estimate,
    )

    return ctx


# ---------------------------------------------------------------------------
# Knowledge selection
# ---------------------------------------------------------------------------

def _select_knowledge(knowledge: dict, intents: dict) -> str:
    """Select only knowledge facts relevant to this question's intents."""
    lines = []
    node_props = knowledge.get("node_properties", {})
    rel_props = knowledge.get("relationship_properties", {})
    patterns = knowledge.get("relationship_patterns", [])
    constraints = knowledge.get("unique_constraints", [])

    # Always include: matched entity resolution
    entities = intents.get("entities", [])
    for ent in entities:
        lines.append(f"Entity '{ent['value']}' → :{ent['label']} {{{ent['property']}: '{ent['exact_value']}'}}")

    # Include: property locations for mentioned properties
    mentioned_props = intents.get("mentioned_properties", set())
    for prop_name in mentioned_props:
        for label, props in node_props.items():
            if prop_name in props:
                info = props[prop_name]
                lines.append(f"Property '{prop_name}' is on NODE :{label} (type: {info.get('type', '?')})")
        for rel, props in rel_props.items():
            if prop_name in props:
                info = props[prop_name]
                lines.append(f"Property '{prop_name}' is on REL [:{rel}] (type: {info.get('type', '?')})")

    # CRITICAL: Return column hints — when filtering by a property, include it in RETURN
    if intents.get("has_filter") and mentioned_props:
        return_cols = []
        for prop_name in mentioned_props:
            # Check if on relationship
            for rel, props in rel_props.items():
                if prop_name in props:
                    return_cols.append(f"r.{prop_name} (from [:{rel}])")
                    break
            else:
                # On node
                for label, props in node_props.items():
                    if prop_name in props:
                        return_cols.append(f"node.{prop_name} (from :{label})")
                        break
        if return_cols:
            lines.append(f"RETURN MUST INCLUDE: {', '.join(return_cols)} alongside the main entity")

    # Include: directions for matched relationships
    matched_rels = intents.get("matched_rels", set())
    for pat in patterns:
        if pat["rel"] in matched_rels:
            lines.append(f"Direction: (:{pat['from']})-[:{pat['rel']}]->(:{pat['to']})")

    # Include: unique constraints for matched labels
    matched_labels = intents.get("matched_labels", set())
    for c in constraints:
        if c["label"] in matched_labels:
            lines.append(f"Match :{c['label']} by .{c['property']} (UNIQUE)")

    if not lines:
        return ""

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Example selection by intent matching
# ---------------------------------------------------------------------------

def _select_examples(
    all_examples: list[dict],
    intents: dict,
    max_examples: int = 3,
) -> str:
    """Select few-shot examples that match the question's intent pattern."""
    if not all_examples:
        return ""

    # Score each example by relevance to intents
    scored = []
    for ex in all_examples:
        score = _score_example(ex, intents)
        scored.append((score, ex))

    # Sort by score descending, take top N
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [ex for score, ex in scored[:max_examples] if score > 0]

    if not selected:
        # Fallback: take first 2 examples
        selected = all_examples[:2]

    lines = []
    for ex in selected:
        lines.append(f"Q: {ex['question']}")
        lines.append(ex['cypher'])
        lines.append("")

    return "\n".join(lines).strip()


def _score_example(example: dict, intents: dict) -> int:
    """Score an example's relevance to the question's intents."""
    score = 0
    tags = set(example.get("tags", []))
    cypher = example.get("cypher", "").lower()
    q = example.get("question", "").lower()

    # Match by intent type
    if intents.get("has_filter") and "where" in cypher:
        score += 2
    if intents.get("has_aggregation") and any(kw in cypher for kw in ["count", "avg", "sum", "order by"]):
        score += 2
    if intents.get("has_multi_condition") and cypher.count("match") >= 2:
        score += 3  # High priority — multi-MATCH examples are rare and important
    if intents.get("has_limit") and "limit" in cypher:
        score += 1

    # Match by mentioned labels
    matched_labels = intents.get("matched_labels", set())
    for label in matched_labels:
        if f":{label.lower()}" in cypher or label.lower() in q:
            score += 1

    # Match by mentioned relationships
    matched_rels = intents.get("matched_rels", set())
    for rel in matched_rels:
        if f":{rel.lower()}" in cypher or rel.lower() in cypher:
            score += 2

    # Match by mentioned properties
    mentioned_props = intents.get("mentioned_properties", set())
    for prop in mentioned_props:
        if prop.lower() in cypher:
            score += 1

    return score


# ---------------------------------------------------------------------------
# Rule selection by question type
# ---------------------------------------------------------------------------

def _select_rules(intents: dict) -> str:
    """Select only rules relevant to this question type."""
    rules = [
        "Output ONLY the raw Cypher query. NO markdown, NO code blocks, NO explanation.",
        "Use only labels, relationships, and properties from the schema.",
    ]

    if intents.get("has_multi_condition"):
        rules.append(
            "Use SEPARATE MATCH clauses with the SAME variable when one entity has multiple relationships. "
            "Do NOT chain everything into one long path."
        )

    if intents.get("has_aggregation"):
        rules.append("Keep aggregate columns (COUNT, AVG, SUM) in RETURN.")

    if intents.get("has_filter"):
        rules.append("Include the filtered property in RETURN alongside the entity.")

    if intents.get("has_count_pattern"):
        rules.append("Use count{(a)-[:REL]->(b)} subquery syntax, NOT COUNT(pattern).")

    # Always
    rules.append("Follow relationship directions exactly as shown in knowledge.")

    return "\n".join(f"- {r}" for r in rules)


# ---------------------------------------------------------------------------
# Build final prompt
# ---------------------------------------------------------------------------

def build_prompt(ctx: AssembledContext, database: str) -> str:
    """Build the final LLM prompt from assembled context."""
    parts = [
        f"You are a Cypher expert for the Neo4j {database} graph database.",
        "",
        "=== SCHEMA ===",
        ctx.schema,
        "",
    ]

    if ctx.knowledge:
        parts.extend(["=== KNOWLEDGE ===", ctx.knowledge, ""])

    if ctx.rules:
        parts.extend(["=== RULES ===", ctx.rules, ""])

    if ctx.examples:
        parts.extend(["=== EXAMPLES ===", ctx.examples, ""])

    parts.append(ctx.question)

    return "\n".join(parts)
