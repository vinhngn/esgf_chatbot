"""
GraphCypherQAChain setup -- multi-database support.

Each database gets its own chain instance (with the correct template
and graph connection). Chains are built lazily and cached.
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

# Per-DB chain pool
_chains: dict[str, GraphCypherQAChain] = {}
_chain_lock = threading.Lock()


def _build_chain(db_name: str) -> GraphCypherQAChain:
    """Build the GraphCypherQAChain for a specific database."""
    logger.info("[Chain] Building GraphCypherQAChain for '%s'...", db_name)
    template = get_cypher_template(db_name)
    prompt = PromptTemplate(
        input_variables=["schema", "question"],
        template=template,
    )
    chain = GraphCypherQAChain.from_llm(
        cypher_llm=get_cypher_llm(),
        qa_llm=get_qa_llm(),
        graph=get_graph(db_name),
        cypher_prompt=prompt,
        validate_cypher=True,
        return_direct=True,
        verbose=True,
        allow_dangerous_requests=True,
        return_intermediate_steps=True,
        top_k=100,
    )
    logger.info("[Chain] GraphCypherQAChain ready for '%s'.", db_name)
    return chain


def get_chain(db_name: str | None = None) -> GraphCypherQAChain:
    """Get or create the chain for a database (thread-safe)."""
    from config import get_settings
    db = db_name or get_settings().database_name or "movies"

    if db not in _chains:
        with _chain_lock:
            if db not in _chains:
                _chains[db] = _build_chain(db)
    return _chains[db]


def reset_chain(db_name: str | None = None) -> None:
    """Force rebuild chain(s) on next call."""
    with _chain_lock:
        if db_name:
            _chains.pop(db_name, None)
        else:
            _chains.clear()
    logger.info("[Chain] Chain reset for: %s", db_name or "ALL")


def invoke_chain(question: str, db_name: str | None = None) -> dict | str:
    """
    Invoke the chain with a question.
    If db_name is provided, uses that database's chain.
    Returns chain result dict or an error string.
    """
    from config import get_settings
    db = db_name or get_settings().database_name or "movies"

    maybe_refresh_schema(db)
    chain = get_chain(db)

    try:
        result = chain.invoke({"query": question}, return_only_outputs=True)
    except Exception as e:
        logger.warning("[Chain] GraphCypher chain error for '%s': %s", db, e)
        return "Sorry, I couldn't find an answer to your question."

    if result is None:
        return "No answer was generated."

    # Clean and URL-encode the generated Cypher query
    try:
        steps = result.get("intermediate_steps", [{}])
        if steps and isinstance(steps[-1], dict):
            query_raw = steps[-1].get("query", "")
            if query_raw:
                cleaned = clean_cypher_query(query_raw)
                encoded = urllib.parse.quote(cleaned)
                result["intermediate_steps"][-1]["query"] = encoded
    except Exception as e:
        logger.warning("[Chain] Failed to extract/clean Cypher query: %s", e)

    return result
