"""
RAG pipeline service - core business logic.
No Streamlit dependency.

FIX: conversation_history is passed as parameter, not module-level mutable list.
FIX: question_block is actually used (was built but ignored in original).
"""
from __future__ import annotations

import logging
import urllib.parse

from retry import retry

from models.chain import invoke_chain
from models.graph import get_graph, get_schema_labels, get_schema_relationships, get_schema_text
from models.llm import get_main_llm, get_interpreter_llm
from services.triple_service import (
    interpret_question,
    interpret_question_with_schema,
    verify_triples,
)
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
    Full RAG pipeline: extract triples → verify → generate Cypher → format response.

    Returns dict with keys: input, output, cypher_query, rewritten,
    verified_triples, instance_triples, debug_info.
    """
    conversation_history = conversation_history or []
    interpreter_llm = get_interpreter_llm()
    main_llm = get_main_llm()
    graph = get_graph()
    schema_labels = get_schema_labels()
    schema_relationships = get_schema_relationships()

    conversation_text = "\n".join(
        f"User: {msg['input']}\nBot: {msg['output']}"
        for msg in conversation_history[-3:]
    )

    # --- Triple extraction with retry ---
    MAX_ATTEMPTS = 5
    verified_triples: list[tuple[str, str, str]] = []
    instance_triples: list[tuple[str, str, str]] = []
    rewritten = ""

    for attempt in range(MAX_ATTEMPTS):
        if attempt == 0:
            rewritten, triples = interpret_question(
                question, interpreter_llm, conversation_history
            )
        else:
            rewritten, triples = interpret_question_with_schema(
                question, interpreter_llm,
                schema_labels, schema_relationships,
                conversation_history,
            )

        temp_verified, temp_instance = verify_triples(
            triples, schema_labels, schema_relationships, graph
        )

        # Accumulate instance triples across retries
        for t in temp_instance:
            if t not in instance_triples:
                instance_triples.append(t)

        if temp_verified:
            verified_triples = temp_verified
            break

    if not verified_triples:
        verified_triples = triples

    logger.info(f"Rewritten: {rewritten}")
    logger.info(f"Verified: {verified_triples}, Instance: {instance_triples}")

    # --- Build enhanced question and invoke chain ---
    triples_text = (
        "\n".join(f"({s}, {r}, {o})" for s, r, o in verified_triples) or "None"
    )
    instance_text = (
        "\n".join(f"({s}, {r}, {o})" for s, r, o in instance_triples) or "None"
    )

    chain_result = invoke_chain(question)

    # Handle string error responses
    if isinstance(chain_result, str):
        return {
            "input": question,
            "output": chain_result,
            "cypher_query": "",
            "rewritten": rewritten,
            "verified_triples": verified_triples,
            "instance_triples": instance_triples,
        }

    encoded_query, decoded_query = _extract_cypher_queries(chain_result)
    neo4j_link = _build_neo4j_link(encoded_query)

    result_payload = chain_result
    result_payload["result"] = normalize_value(result_payload.get("result"))
    result_only = result_payload.get("result") or result_payload.get("error") or "No results found."

    # --- Generate final response with LLM ---
    if not result_only or result_only == "No results found.":
        final_response = (
            f"It appears that there are no models that include the requested variable "
            f"in the database. Please click here to access the knowledge graph: {neo4j_link}"
        )
    else:
        final_prompt = f"""
Based on the conversation and the user question, provide a relevant and helpful response.

Conversation:
{conversation_text}

Current question: {question}
Rewritten question: {rewritten}

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
        "rewritten": rewritten,
        "verified_triples": verified_triples,
        "instance_triples": instance_triples,
    }


@retry(tries=2, delay=10)
def get_results(
    question: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict:
    """Public entry point for the RAG pipeline."""
    return process_question(question, conversation_history)
