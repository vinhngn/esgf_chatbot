"""
Subproblem Decomposer — breaks complex questions into clause-level sub-tasks.

Inspired by DIN-SQL and SQL-of-Thought: decomposing the question BEFORE
planning significantly improves accuracy on complex multi-hop queries.

Input: question + cropped schema
Output: list of sub-problems, each mapping to a Cypher clause

Example:
  Q: "Find tweets by users who follow Neo4j with more than 100 favorites"
  →
  [
    {"type": "MATCH", "task": "Find Me/User node for 'Neo4j'"},
    {"type": "MATCH", "task": "Find Users who FOLLOWS Neo4j"},
    {"type": "MATCH", "task": "Find Tweets POSTED by those Users"},
    {"type": "WHERE", "task": "Filter Tweets where favorites > 100"},
    {"type": "RETURN", "task": "Return the Tweet nodes"}
  ]

Uses 1 LLM call with JSON mode.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


@dataclass
class SubProblem:
    """A clause-level sub-task."""
    type: str       # MATCH, WHERE, RETURN, ORDER_BY, LIMIT, AGGREGATE
    task: str       # natural language description
    entities: list[str] = None  # referenced entity names

    def __post_init__(self):
        if self.entities is None:
            self.entities = []


DECOMPOSE_PROMPT = """You are a graph query decomposer. Break the user question into clause-level sub-problems for Cypher query generation.

Each sub-problem should map to ONE Cypher clause:
- MATCH: a graph pattern to traverse (node-relationship-node)
- WHERE: a filter condition on a property
- RETURN: what to output
- ORDER_BY: sorting
- LIMIT: row limit
- AGGREGATE: count, avg, sum, collect

Rules:
- For multi-hop queries, create one MATCH sub-problem per hop
- Keep entity names/values EXACTLY as in the question
- Reference the schema labels and relationships below

Schema:
{schema}

Output JSON:
{{
  "subproblems": [
    {{"type": "MATCH", "task": "description", "entities": ["entity1"]}},
    {{"type": "WHERE", "task": "description", "entities": []}},
    {{"type": "RETURN", "task": "description", "entities": []}}
  ]
}}"""


def decompose(
    question: str,
    cropped_schema: str,
    llm: ChatOpenAI,
) -> list[SubProblem]:
    """Decompose question into sub-problems."""
    prompt = DECOMPOSE_PROMPT.replace("{schema}", cropped_schema)

    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=question),
    ]

    try:
        response = llm.invoke(messages, response_format={"type": "json_object"})
        data = json.loads(response.content.strip())
    except Exception as e:
        logger.warning("[Decomposer] Failed: %s", e)
        # Fallback: single sub-problem
        return [SubProblem(type="MATCH", task=question)]

    subproblems = []
    for sp in data.get("subproblems", []):
        subproblems.append(SubProblem(
            type=sp.get("type", "MATCH"),
            task=sp.get("task", ""),
            entities=sp.get("entities", []),
        ))

    if not subproblems:
        subproblems = [SubProblem(type="MATCH", task=question)]

    logger.info("[Decomposer] Decomposed into %d sub-problems", len(subproblems))
    for sp in subproblems:
        logger.info("[Decomposer]   %s: %s", sp.type, sp.task)

    return subproblems
