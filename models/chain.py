"""
GraphCypherQAChain — direct LLM Cypher generation.
Proven baseline: 43% Exact Match on eval.
"""

from __future__ import annotations

import logging
import threading
import urllib.parse

from langchain_core.prompts import PromptTemplate
from langchain_neo4j import GraphCypherQAChain
from templates.cypher_templates import get_cypher_template
from utils.helpers import clean_cypher_query

from models.graph import get_graph, maybe_refresh_schema
from models.llm import get_cypher_llm, get_qa_llm

logger = logging.getLogger(__name__)

_chain: GraphCypherQAChain | None = None
_chain_lock = threading.Lock()


def _build_chain() -> GraphCypherQAChain:
    from config import get_settings
    from services.knowledge_base import format_for_cypher_prompt

    logger.info("[Chain] Building GraphCypherQAChain...")
    template = get_cypher_template()

    db_name = get_settings().database_name
    knowledge = format_for_cypher_prompt(db_name)
    knowledge_escaped = knowledge.replace("{", "{{").replace("}", "}}")
    template = template.replace("{knowledge}", knowledge_escaped)

    prompt = PromptTemplate(
        input_variables=["schema", "question"],
        template=template,
    )
    chain = GraphCypherQAChain.from_llm(
        cypher_llm=get_cypher_llm(),
        qa_llm=get_qa_llm(),
        graph=get_graph(),
        cypher_prompt=prompt,
        validate_cypher=True,
        return_direct=True,
        verbose=True,
        allow_dangerous_requests=True,
        return_intermediate_steps=True,
        top_k=100,
    )
    logger.info("[Chain] GraphCypherQAChain ready.")
    return chain


def get_chain() -> GraphCypherQAChain:
    global _chain
    if _chain is None:
        with _chain_lock:
            if _chain is None:
                _chain = _build_chain()
    return _chain


def reset_chain() -> None:
    global _chain
    with _chain_lock:
        _chain = None


def invoke_chain(question: str) -> dict | str:
    maybe_refresh_schema()
    chain = get_chain()

    try:
        result = chain.invoke({"query": question}, return_only_outputs=True)
    except Exception as e:
        logger.warning("[Chain] error: %s", e)
        return "Sorry, I couldn't find an answer to your question."

    if result is None:
        return "No answer was generated."

    try:
        steps = result.get("intermediate_steps", [{}])
        if steps and isinstance(steps[-1], dict):
            query_raw = steps[-1].get("query", "")
            if query_raw:
                cleaned = clean_cypher_query(query_raw)
                encoded = urllib.parse.quote(cleaned)
                result["intermediate_steps"][-1]["query"] = encoded
    except Exception as e:
        logger.warning("[Chain] Failed to clean query: %s", e)

    return result
