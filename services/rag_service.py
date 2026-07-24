"""
RAG pipeline service - core business logic.
Restored: triple extraction + verification + enhanced question building.
No Streamlit dependency.

_run_pipeline() is the single shared core:
  1. extract_triples_with_retry()  → rewritten, verified_triples, instance_triples
  2. build_enhanced_question()     → enriched query string
  3. invoke_chain()                → raw chain result dict

process_question()  calls _run_pipeline() then adds LLM-formatted response.
get_raw_results()   calls _run_pipeline() then returns raw DB result only.
"""

from __future__ import annotations

import copy
import logging
import threading
from collections import OrderedDict

from config import get_settings
from models.graph import get_graph, get_schema_labels, get_schema_relationships
from services.text2cypher.pipeline import invoke_chain
from services.text2cypher.result_utils import (
    extract_cypher_queries as _extract_cypher_queries,
)
from services.text2cypher.service import (
    get_available_databases,
    get_database_info,
    get_raw_results,
    get_schema_info,
)
from services.triple_service import build_enhanced_question, extract_triples_with_retry
from utils.helpers import normalize_value
from utils.pipeline_trace import new_trace_id, trace_event

logger = logging.getLogger(__name__)
_PIPELINE_CACHE_MAXSIZE = 512
_pipeline_cache: "OrderedDict[tuple[str, str], dict]" = OrderedDict()
_pipeline_cache_lock = threading.Lock()

# Neo4j browser base URL
NEO4J_BROWSER_URL = "https://neoforjcmip.templeuni.com/browser/"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_neo4j_link(encoded_query: str | None) -> str:
    """Build a Neo4j Browser link with the query pre-filled (if available)."""
    if encoded_query:
        return (
            f"[Open Neo4J]({NEO4J_BROWSER_URL}"
            f"?preselectAuthMethod=NO_AUTH&cmd=edit&arg={encoded_query})"
        )
    return f"[Open Neo4J]({NEO4J_BROWSER_URL})"


def _is_empty_result(result) -> bool:
    """Return True when the chain produced no usable result."""
    if not result:
        return True
    if isinstance(result, str) and result.strip() in ("", "No results found."):
        return True
    if isinstance(result, list) and len(result) == 0:
        return True
    return False


def _get_cached_pipeline(database: str, question: str) -> dict | None:
    key = (database, question.strip())
    with _pipeline_cache_lock:
        cached = _pipeline_cache.get(key)
        if cached is None:
            return None
        _pipeline_cache.move_to_end(key)
        return copy.deepcopy(cached)


def _set_cached_pipeline(database: str, question: str, payload: dict) -> None:
    key = (database, question.strip())
    with _pipeline_cache_lock:
        _pipeline_cache[key] = copy.deepcopy(payload)
        _pipeline_cache.move_to_end(key)
        while len(_pipeline_cache) > _PIPELINE_CACHE_MAXSIZE:
            _pipeline_cache.popitem(last=False)


# ---------------------------------------------------------------------------
# Core shared pipeline (steps 1-3)
# ---------------------------------------------------------------------------


def _run_pipeline(
    question: str,
    conversation_history: list[dict[str, str]],
) -> dict:
    """
      Run the shared RAG pipeline steps 1–3:

        1. Triple extraction with retry  (interpreter LLM + Neo4j verification)
        2. Build enhanced question        (original + rewritten + triples)
    3. Invoke Cypher generator        (retrieval-grounded Cypher → Neo4j)

      Returns a dict with keys:
          rewritten          : str
          verified_triples   : list[tuple[str,str,str]]
          instance_triples   : list[tuple[str,str,str]]
          chain_result       : dict | str   (raw chain output)
          encoded_query      : str | None
          decoded_query      : str | None
    """
    from models.llm import get_interpreter_llm

    db_name = get_settings().profile_database_name
    trace_id = new_trace_id()
    trace_event(
        logger,
        trace_id,
        "RAG-01",
        "RAG request received before preprocessing",
        {
            "question": question,
            "conversation_messages": len(conversation_history),
            "database": db_name,
        },
    )
    if not conversation_history:
        cached = _get_cached_pipeline(db_name, question)
        if cached is not None:
            logger.debug("[RAGService] cache hit for %s", question)
            trace_event(
                logger,
                trace_id,
                "RAG-02",
                "Pipeline cache hit; no new preprocessing or grounding call",
                {"cached_trace_id": cached.get("trace_id")},
            )
            return cached

    interpreter_llm = get_interpreter_llm()
    graph = get_graph()
    schema_labels = get_schema_labels()
    schema_relationships = get_schema_relationships()

    # --- Step 1: Triple extraction with retry ---
    try:
        rewritten, verified_triples, instance_triples = extract_triples_with_retry(
            question=question,
            interpreter_llm=interpreter_llm,
            schema_labels=schema_labels,
            schema_relationships=schema_relationships,
            graph=graph,
            database=db_name,
            conversation_history=conversation_history,
        )
    except Exception as exc:
        trace_event(
            logger,
            trace_id,
            "RAG-ERROR",
            "Interpreter preprocessing failed",
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise

    logger.debug("[RAGService] rewritten=%r", rewritten)
    logger.debug("[RAGService] verified_triples=%s", verified_triples)
    logger.debug("[RAGService] instance_triples=%s", instance_triples)
    trace_event(
        logger,
        trace_id,
        "RAG-02",
        "Interpreter preprocessing return value",
        {
            "rewritten_question": rewritten,
            "verified_schema_triples": verified_triples,
            "instance_triples": instance_triples,
        },
    )

    # --- Step 2: Build enriched question ---
    enhanced_question = build_enhanced_question(
        question=question,
        rewritten=rewritten,
        verified_triples=verified_triples,
        instance_triples=instance_triples,
        conversation_history=conversation_history,
    )
    trace_event(
        logger,
        trace_id,
        "RAG-03",
        "Enhanced question passed into the Text-to-Cypher chain",
        enhanced_question,
    )

    # --- Step 3: Invoke chain ---
    try:
        chain_result = invoke_chain(enhanced_question, trace_id=trace_id)
    except Exception as exc:
        trace_event(
            logger,
            trace_id,
            "RAG-ERROR",
            "Text-to-Cypher synthesis failed",
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise

    encoded_query, decoded_query = (
        _extract_cypher_queries(chain_result) if isinstance(chain_result, dict) else (None, None)
    )

    payload = {
        "rewritten": rewritten,
        "verified_triples": verified_triples,
        "instance_triples": instance_triples,
        "chain_result": chain_result,
        "encoded_query": encoded_query,
        "decoded_query": decoded_query,
        "trace_id": trace_id,
    }
    if not conversation_history:
        _set_cached_pipeline(db_name, question, payload)
    return payload


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def process_question(
    question: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict:
    """
    Full RAG pipeline → LLM-formatted final response.

    Steps:
      1-3. _run_pipeline()
      4.   Format the DB result with the main LLM.

    Returns dict with keys:
        input, output, cypher_query, rewritten, verified_triples, instance_triples
    """
    from models.llm import get_main_llm

    conversation_history = conversation_history or []
    main_llm = get_main_llm()

    pipe = _run_pipeline(question, conversation_history)
    chain_result = pipe["chain_result"]
    encoded_query = pipe["encoded_query"]
    decoded_query = pipe["decoded_query"]
    rewritten = pipe["rewritten"]
    verified_triples = pipe["verified_triples"]
    instance_triples = pipe["instance_triples"]

    neo4j_link = _build_neo4j_link(encoded_query)

    # Handle chain error string
    if isinstance(chain_result, str):
        return {
            "input": question,
            "output": chain_result,
            "cypher_query": "",
            "rewritten": rewritten,
            "verified_triples": verified_triples,
            "instance_triples": instance_triples,
            "trace_id": pipe.get("trace_id", ""),
        }

    # Normalize Neo4j result
    raw_result = normalize_value(chain_result.get("result"))
    trace_event(
        logger,
        pipe.get("trace_id", "unknown"),
        "RAG-04",
        "Database result passed to the final answer formatter",
        {
            "result_type": type(raw_result).__name__,
            "row_count": len(raw_result) if isinstance(raw_result, list) else None,
            "sample_rows": raw_result[:2]
            if isinstance(raw_result, list)
            else str(raw_result)[:1000],
        },
    )

    # --- Step 4: LLM-formatted response ---
    conversation_text = "\n".join(
        f"User: {msg['input']}\nBot: {msg['output']}" for msg in conversation_history[-3:]
    )

    if _is_empty_result(raw_result):
        final_response = (
            "It appears that there are no results for your question "
            f"in the database. Please click here to access the knowledge graph: {neo4j_link}"
        )
    else:
        final_prompt = (
            "Based on the conversation and the user question, provide a relevant and helpful response.\n\n"
            f"Conversation:\n{conversation_text}\n\n"
            f"Current question: {question}\n"
            f"Rewritten question: {rewritten or question}\n\n"
            f"Here is the output from the database:\n{raw_result}\n\n"
            "Please process the output and answer the user question clearly.\n"
            "Always end your answer with the exact phrase:\n"
            '"Please click here to access the knowledge graph: [[button_query]]"\n'
            "Do not use any other wording for the link."
        )
        trace_event(
            logger,
            pipe.get("trace_id", "unknown"),
            "RAG-05",
            "Full prompt sent to the final answer LLM",
            final_prompt,
            verbose_only=True,
        )
        try:
            final_response = main_llm.invoke(final_prompt).content.strip()
        except Exception as exc:
            trace_event(
                logger,
                pipe.get("trace_id", "unknown"),
                "RAG-ERROR",
                "Final answer LLM request failed",
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise
        trace_event(
            logger,
            pipe.get("trace_id", "unknown"),
            "RAG-06",
            "Final answer LLM return value: user-facing text",
            final_response,
        )
        final_response = final_response.replace("[[button_query]]", neo4j_link)

    return {
        "input": question,
        "output": final_response,
        "cypher_query": decoded_query or "",
        "rewritten": rewritten,
        "verified_triples": verified_triples,
        "instance_triples": instance_triples,
        "trace_id": pipe.get("trace_id", ""),
    }


def get_results(
    question: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict:
    """Public entry point for the RAG pipeline (with LLM-formatted response)."""
    return process_question(question, conversation_history)


__all__ = [
    "get_available_databases",
    "get_database_info",
    "get_raw_results",
    "get_results",
    "get_schema_info",
    "process_question",
]
