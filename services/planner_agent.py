"""
Planner Agent — generates structured QueryPlan from natural language.

Uses LLM with JSON mode to produce a validated query plan.
The plan is then translated to Cypher deterministically (no LLM).

Key design decisions:
  - JSON mode guarantees valid JSON output
  - Plan format is simpler than Cypher → LLM makes fewer mistakes
  - Each field can be validated independently → targeted error fixing
  - Planner never writes Cypher → eliminates syntax errors
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from services.query_plan import QueryPlan, parse_plan_dict
from templates.entity_definitions import get_entity_definitions

logger = logging.getLogger(__name__)

PLAN_SYSTEM_PROMPT = """You are a graph query planner. Given a user question and a Neo4j graph schema, produce a structured query plan in JSON format.

You do NOT write Cypher. You only produce the logical plan.

=== QUERY PLAN JSON FORMAT ===

{{
  "patterns": [
    {{
      "from": {{"label": "NodeLabel", "var": "a", "match": {{"property": "value"}}}},
      "rel": "RELATIONSHIP_TYPE",
      "rel_var": "r",
      "direction": "->",
      "to": {{"label": "NodeLabel", "var": "b", "match": {{}}}}
    }}
  ],
  "where": [
    {{"var": "b", "prop": "property_name", "op": ">", "value": 100}}
  ],
  "return": [
    {{"var": "a", "prop": "name"}},
    {{"agg": "count", "var": "b", "alias": "cnt"}}
  ],
  "order_by": {{"field": "cnt", "dir": "DESC"}},
  "limit": 5,
  "distinct": false
}}

=== FIELD RULES ===

patterns:
- Each pattern is one relationship traversal: (from)-[rel]->(to)
- "var" is a short variable name (1-2 chars like "p", "m", "u")
- REUSE the same "var" when the same node appears in multiple patterns
  Example: Person "p" who WROTE and DIRECTED the same Movie "m" → two patterns, both use "p" and "m"
- "match" contains exact property values for inline filtering (e.g., {{"name": "Tom Hanks"}})
- "direction" is "->" or "<-" following the schema relationship direction
- "rel_var" is needed only when you need to access relationship properties (e.g., roles on ACTED_IN)

where:
- Comparison conditions: >, <, =, >=, <=, <>, CONTAINS, STARTS WITH, IN, IS NOT NULL
- Only for conditions that can't go in "match" (numeric comparisons, text search)

return:
- "var" + "prop" returns a specific property: p.name
- "var" alone with "star": true returns the full node
- "agg" for aggregations: count, avg, sum, min, max, collect, size
- "alias" names the output column

order_by:
- "field" must match an alias from return, or be "var.prop"

=== CRITICAL RULES ===
- Use ONLY labels and relationships from the schema below
- Keep all specific names, values, numbers from the question EXACTLY as written
- For "top N" / "first N" / "most" / "least" → set order_by + limit
- For "how many" → use agg: "count"
- For "average" → use agg: "avg"
- ALWAYS return specific properties (var + prop), NEVER return full nodes (no "star": true)
- If the question asks for "movies" or "users", return their key identifying properties (title, name, screen_name) not the full node
- If the question doesn't need relationships, use a single pattern with just "from" node (set "rel" to "" and "to" to empty)

=== SCHEMA ===
{schema}

{knowledge}

{entity_defs}

Output ONLY the JSON object. No explanation, no markdown."""


def generate_plan(
    question: str,
    schema_text: str,
    entity_defs: str,
    llm: ChatOpenAI,
    knowledge_text: str = "",
    subproblems_text: str = "",
) -> QueryPlan:
    """
    Generate a QueryPlan from a natural language question.
    Uses JSON mode for guaranteed valid JSON.
    """
    kb_section = ""
    if knowledge_text:
        kb_section = f"\n=== GRAPH KNOWLEDGE ===\n{knowledge_text}\n"

    sp_section = ""
    if subproblems_text:
        sp_section = f"\n=== SUB-PROBLEMS (solve each one) ===\n{subproblems_text}\n"

    prompt = (
        PLAN_SYSTEM_PROMPT
        .replace("{schema}", schema_text)
        .replace("{entity_defs}", entity_defs)
        .replace("{knowledge}", kb_section)
    )
    # Append subproblems after the main prompt
    if sp_section:
        prompt += sp_section

    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=question),
    ]

    response = llm.invoke(
        messages,
        response_format={"type": "json_object"},
    )

    raw = response.content.strip()
    logger.debug("[Planner] Raw response:\n%s", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("[Planner] Failed to parse JSON: %s", e)
        # Return empty plan
        return QueryPlan()

    plan = parse_plan_dict(data)
    logger.info("[Planner] Generated plan with %d patterns", len(plan.patterns))
    return plan


def fix_plan(
    question: str,
    original_plan: QueryPlan,
    errors: list,
    schema_text: str,
    entity_defs: str,
    llm: ChatOpenAI,
) -> QueryPlan:
    """
    Fix a plan based on validation errors.
    Sends the original plan + errors back to LLM for correction.
    """
    error_text = "\n".join(
        f"- {e.field}: {e.message} Hint: {e.hint}"
        for e in errors
    )

    fix_prompt = f"""The following query plan has validation errors. Fix them.

ORIGINAL PLAN:
{json.dumps(original_plan.to_dict(), indent=2)}

ERRORS:
{error_text}

SCHEMA:
{schema_text}

{entity_defs}

Output the FIXED JSON plan only. No explanation."""

    messages = [
        SystemMessage(content="You are a graph query planner. Fix the plan based on the errors."),
        HumanMessage(content=fix_prompt),
    ]

    response = llm.invoke(
        messages,
        response_format={"type": "json_object"},
    )

    try:
        data = json.loads(response.content.strip())
        plan = parse_plan_dict(data)
        logger.info("[Planner] Fixed plan with %d patterns", len(plan.patterns))
        return plan
    except Exception as e:
        logger.error("[Planner] Failed to parse fixed plan: %s", e)
        return original_plan


def revise_plan(
    question: str,
    original_plan: QueryPlan,
    cypher: str,
    error_message: str,
    schema_text: str,
    entity_defs: str,
    llm: ChatOpenAI,
) -> QueryPlan:
    """
    Revise a plan after execution failure.
    Sends the original plan + generated Cypher + error back to LLM.
    """
    revise_prompt = f"""The query plan produced a Cypher query that failed. Revise the plan.

QUESTION: {question}

ORIGINAL PLAN:
{json.dumps(original_plan.to_dict(), indent=2)}

GENERATED CYPHER:
{cypher}

ERROR:
{error_message}

SCHEMA:
{schema_text}

{entity_defs}

Think about what went wrong and output a REVISED JSON plan. No explanation."""

    messages = [
        SystemMessage(content="You are a graph query planner. Revise the plan to fix the execution error."),
        HumanMessage(content=revise_prompt),
    ]

    response = llm.invoke(
        messages,
        response_format={"type": "json_object"},
    )

    try:
        data = json.loads(response.content.strip())
        plan = parse_plan_dict(data)
        logger.info("[Planner] Revised plan with %d patterns", len(plan.patterns))
        return plan
    except Exception as e:
        logger.error("[Planner] Failed to parse revised plan: %s", e)
        return original_plan
