"""
GraphCypherQAChain setup.
Uses langchain_neo4j.GraphCypherQAChain (compatible with GraphStore/Neo4jGraph)
instead of langchain_community version which caused validation errors.
Thread-safe singleton with double-checked locking.
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
    """Build the GraphCypherQAChain with current config."""
    logger.info("[Chain] Building GraphCypherQAChain...")
    template = get_cypher_template()
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
    """Get or create the chain (thread-safe singleton)."""
    global _chain
    if _chain is None:
        with _chain_lock:
            if _chain is None:  # double-checked locking
                _chain = _build_chain()
    return _chain


def reset_chain() -> None:
    """Force rebuild the chain on next call (e.g. after config change)."""
    global _chain
    with _chain_lock:
        _chain = None
    logger.info("[Chain] Chain reset — will rebuild on next invoke.")


def invoke_chain(question: str) -> dict | str:
    """
    Invoke the chain with a question. 60s timeout.
    Returns chain result dict or an error string.
    """
    import signal
    import functools
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    maybe_refresh_schema()
    chain = get_chain()

    def _run():
        return chain.invoke({"query": question}, return_only_outputs=True)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run)
            result = future.result(timeout=60)
    except FuturesTimeout:
        logger.warning("[Chain] Chain timed out after 60s for question: %s", question[:80])
        return "Sorry, the query took too long."
    except Exception as e:
        logger.warning("[Chain] GraphCypher chain error: %s", e)
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
        logger.warning("[Chain] Failed to extract/clean Cypher query: %s", e)

    return result
