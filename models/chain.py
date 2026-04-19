"""
GraphCypherQAChain setup.
Uses langchain_neo4j.GraphCypherQAChain (compatible with GraphStore/Neo4jGraph)
instead of langchain_community version which caused validation errors.
Thread-safe singleton with double-checked locking.

Includes retry logic: if the first Cypher query fails at the Neo4j level,
the error message is fed back to the LLM so it can self-correct.
"""

from __future__ import annotations

import logging
import re
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

MAX_CYPHER_RETRIES = 2  # total attempts = 1 original + up to 2 retries


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


def _extract_and_clean_query(result: dict) -> str | None:
    """
    Extract the raw Cypher query from chain result intermediate_steps,
    clean it (strip markdown / prefix / semicolons), and store the cleaned
    version back into intermediate_steps.

    Returns the cleaned query string, or None if not found.
    """
    steps = result.get("intermediate_steps", [])
    if not isinstance(steps, list):
        return None
    for step in steps:
        if isinstance(step, dict):
            query_raw = step.get("query", "")
            if query_raw:
                cleaned = clean_cypher_query(query_raw)
                step["query"] = cleaned  # store cleaned, NOT url-encoded
                return cleaned
    return None


def _invoke_once(chain: GraphCypherQAChain, question: str, timeout: int = 60) -> dict | str:
    """Single chain invocation with timeout. Returns result dict or error string."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    def _run():
        return chain.invoke({"query": question}, return_only_outputs=True)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run)
            result = future.result(timeout=timeout)
    except FuturesTimeout:
        logger.warning("[Chain] Timed out after %ds for: %s", timeout, question[:80])
        return "Sorry, the query took too long."
    except Exception as e:
        logger.warning("[Chain] GraphCypher chain error: %s", e)
        return f"CYPHER_ERROR: {e}"

    if result is None:
        return "No answer was generated."
    return result


def invoke_chain(question: str) -> dict | str:
    """
    Invoke the chain with a question.

    Retry logic:
      1. Run the chain.
      2. If the result contains a Neo4j / Cypher error, append the error to
         the question and retry so the LLM can self-correct.
      3. Up to MAX_CYPHER_RETRIES additional attempts.

    Returns chain result dict (with cleaned Cypher in intermediate_steps)
    or an error string.
    """
    maybe_refresh_schema()
    chain = get_chain()

    current_question = question
    last_error: str | None = None

    for attempt in range(1 + MAX_CYPHER_RETRIES):
        logger.info("[Chain] invoke attempt %d/%d", attempt + 1, 1 + MAX_CYPHER_RETRIES)

        result = _invoke_once(chain, current_question)

        # Hard error (timeout / unexpected) — no point retrying
        if isinstance(result, str):
            if result.startswith("CYPHER_ERROR:") and attempt < MAX_CYPHER_RETRIES:
                last_error = result.replace("CYPHER_ERROR: ", "")
                current_question = (
                    f"{question}\n\n"
                    f"[IMPORTANT] The previous Cypher query failed with this error:\n"
                    f"{last_error}\n"
                    f"Please fix the Cypher query to avoid this error."
                )
                logger.info("[Chain] Retrying with error feedback: %s", last_error[:120])
                continue
            # Non-retryable or exhausted retries
            if result.startswith("CYPHER_ERROR:"):
                return "Sorry, I couldn't find an answer to your question."
            return result

        # Got a dict result — clean the Cypher query
        _extract_and_clean_query(result)

        # Check if the result itself signals an error (some chains embed errors)
        raw_result = result.get("result")
        if isinstance(raw_result, str) and _looks_like_cypher_error(raw_result):
            if attempt < MAX_CYPHER_RETRIES:
                last_error = raw_result
                current_question = (
                    f"{question}\n\n"
                    f"[IMPORTANT] The previous Cypher query returned an error:\n"
                    f"{last_error}\n"
                    f"Please fix the Cypher query to avoid this error."
                )
                logger.info("[Chain] Result looks like error, retrying: %s", last_error[:120])
                continue

        return result

    # Should not reach here, but just in case
    return "Sorry, I couldn't find an answer to your question."


def _looks_like_cypher_error(text: str) -> bool:
    """Heuristic: does the text look like a Neo4j / Cypher error message?"""
    error_patterns = [
        r"SyntaxError",
        r"Neo\.ClientError",
        r"Invalid input",
        r"Unknown function",
        r"Type mismatch",
        r"Variable `.+` not defined",
        r"There is no procedure with the name",
    ]
    for pattern in error_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False
