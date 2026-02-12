"""
RAG pipeline service - core business logic.
No Streamlit dependency. No triple extraction.
"""
from __future__ import annotations

import logging
import urllib.parse

from retry import retry

from models.chain import invoke_chain
from models.llm import get_main_llm
from utils.helpers import normalize_value

logger = logging.getLogger(__name__)

# Neo4j browser base URL
NEO4J_BROWSER_URL = "https://neoforjcmip.templeuni.com/browser/"


def _extract_cypher_queries(chain_result: dict) -> tuple[str | None, str | None]:
    """Extract encoded and decoded Cypher queries from chain result."""
    steps = chain_result.get("intermediate_steps", [])
    if not isinstance(steps, list):
        return None, None
    for step in steps:
        if isinstance(step, dict):
            encoded = step.get("query")
            if encoded:
                return encoded, urllib.parse.unquote(encoded)
    return None, None


def _build_neo4j_link(encoded_query: str | None) -> str:
    """Build Neo4j Browser link with optional pre-filled query."""
    if encoded_query:
        return (
            f"[Open Neo4J]({NEO4J_BROWSER_URL}"
            f"?preselectAuthMethod=NO_AUTH&cmd=edit&arg={encoded_query})"
        )
    return f"[Open Neo4J]({NEO4J_BROWSER_URL})"


def process_question(
    question: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict:
    """
    RAG pipeline: question → Cypher chain → LLM-formatted response.
    Returns dict with keys: input, output, cypher_query.
    """
    conversation_history = conversation_history or []
    main_llm = get_main_llm()

    conversation_text = "\n".join(
        f"User: {msg['input']}\nBot: {msg['output']}"
        for msg in conversation_history[-3:]
    )

    # Invoke chain directly
    chain_result = invoke_chain(question)

    # Handle string error responses
    if isinstance(chain_result, str):
        return {"input": question, "output": chain_result, "cypher_query": ""}

    encoded_query, decoded_query = _extract_cypher_queries(chain_result)
    neo4j_link = _build_neo4j_link(encoded_query)

    chain_result["result"] = normalize_value(chain_result.get("result"))
    result_only = chain_result.get("result") or chain_result.get("error") or "No results found."

    # Generate final response with LLM
    if not result_only or result_only == "No results found.":
        final_response = (
            f"It appears that there are no results for your question "
            f"in the database. Please click here to access the knowledge graph: {neo4j_link}"
        )
    else:
        final_prompt = f"""
Based on the conversation and the user question, provide a relevant and helpful response.

Conversation:
{conversation_text}

Current question: {question}

Here is the output from the database:
{result_only}

Please process the output and answer the user question clearly.
Always end your answer with the exact phrase:
"Please click here to access the knowledge graph: [[button_query]]"
Do not use any other wording for the link.
""".strip()

        final_response = main_llm.invoke(final_prompt).content.strip()
        final_response = final_response.replace("[[button_query]]", neo4j_link)

    return {
        "input": question,
        "output": final_response,
        "cypher_query": decoded_query or "",
    }


@retry(tries=2, delay=10)
def get_results(
    question: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict:
    """Public entry point for the RAG pipeline (with LLM-formatted response)."""
    return process_question(question, conversation_history)


@retry(tries=2, delay=10)
def get_raw_results(question: str) -> dict:
    """
    Flask API entry point — runs chain and returns raw database results
    without LLM formatting.
    """
    chain_result = invoke_chain(question)

    if isinstance(chain_result, dict):
        _, decoded_query = _extract_cypher_queries(chain_result)
        result = normalize_value(chain_result.get("result")) if chain_result.get("result") else ""
        error = chain_result.get("error")
    else:
        decoded_query = ""
        result = chain_result
        error = None

    return {
        "cypher_query": decoded_query or "",
        "result": result,
        "error": error,
    }
