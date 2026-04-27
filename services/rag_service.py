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
import urllib.parse
from collections import OrderedDict

from config import get_settings
from models.chain import invoke_chain
from models.graph import get_graph, get_schema_labels, get_schema_relationships
from models.llm import get_interpreter_llm, get_main_llm
from retry import retry
from templates.cypher_templates import get_cypher_template
from templates.entity_definitions import get_entity_definitions
from templates.match_properties_map import get_match_properties_map
from utils.helpers import normalize_value

from services.triple_service import build_enhanced_question, extract_triples_with_retry

logger = logging.getLogger(__name__)
_PIPELINE_CACHE_MAXSIZE = 512
_pipeline_cache: "OrderedDict[tuple[str, str], dict]" = OrderedDict()
_pipeline_cache_lock = threading.Lock()

# Neo4j browser base URL
NEO4J_BROWSER_URL = "https://neoforjcmip.templeuni.com/browser/"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_cypher_queries(chain_result: dict) -> tuple[str | None, str | None]:
    """
    Extract the (encoded, decoded) Cypher query from intermediate_steps.

    After the chain.py fix, intermediate_steps now stores the *cleaned*
    (non-encoded) query.  We derive the encoded version for the Neo4j
    Browser link and keep the decoded version for display / debugging.
    """
    steps = chain_result.get("intermediate_steps", [])
    if not isinstance(steps, list):
        return None, None
    for step in steps:
        if isinstance(step, dict):
            cleaned = step.get("query")
            if cleaned:
                encoded = urllib.parse.quote(cleaned)
                return encoded, cleaned
    return None, None


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
    db_name = get_settings().database_name
    if not conversation_history:
        cached = _get_cached_pipeline(db_name, question)
        if cached is not None:
            logger.info("[RAGService] cache hit for %s", question)
            return cached

    interpreter_llm = get_interpreter_llm()
    graph = get_graph()
    schema_labels = get_schema_labels()
    schema_relationships = get_schema_relationships()

    # --- Step 1: Triple extraction with retry ---
    rewritten, verified_triples, instance_triples = extract_triples_with_retry(
        question=question,
        interpreter_llm=interpreter_llm,
        schema_labels=schema_labels,
        schema_relationships=schema_relationships,
        graph=graph,
        database=db_name,
        conversation_history=conversation_history,
    )

    logger.info("[RAGService] rewritten=%r", rewritten)
    logger.info("[RAGService] verified_triples=%s", verified_triples)
    logger.info("[RAGService] instance_triples=%s", instance_triples)

    # --- Step 2: Build enriched question ---
    enhanced_question = build_enhanced_question(
        question=question,
        rewritten=rewritten,
        verified_triples=verified_triples,
        instance_triples=instance_triples,
        conversation_history=conversation_history,
    )

    # --- Step 3: Invoke chain ---
    chain_result = invoke_chain(enhanced_question)

    encoded_query, decoded_query = (
        _extract_cypher_queries(chain_result)
        if isinstance(chain_result, dict)
        else (None, None)
    )

    payload = {
        "rewritten": rewritten,
        "verified_triples": verified_triples,
        "instance_triples": instance_triples,
        "chain_result": chain_result,
        "encoded_query": encoded_query,
        "decoded_query": decoded_query,
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
        }

    # Normalize Neo4j result
    raw_result = normalize_value(chain_result.get("result"))

    # --- Step 4: LLM-formatted response ---
    conversation_text = "\n".join(
        f"User: {msg['input']}\nBot: {msg['output']}"
        for msg in conversation_history[-3:]
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
    """Public entry point for the RAG pipeline (with LLM-formatted response)."""
    return process_question(question, conversation_history)


@retry(tries=2, delay=10)
def get_raw_results(question: str, schema: str = "") -> dict:
    """
    Flask / T2C evaluation entry point.
    Passes the question DIRECTLY to the Cypher chain (no triple extraction)
    for cleaner, faster Cypher generation.
    Falls back to direct OpenAI call if the chain fails.
    """
    db_name = get_settings().database_name

    # Skip triple extraction — pass raw question directly to chain.
    # Triple extraction adds noise to the question and confuses the Cypher LLM.
    chain_result = invoke_chain(question, schema)

    decoded_query = None
    if isinstance(chain_result, dict):
        encoded_query, decoded_query = _extract_cypher_queries(chain_result)

    if isinstance(chain_result, dict):
        raw_result = chain_result.get("result")
        error = chain_result.get("error")

        if raw_result:
            if isinstance(raw_result, list):
                result: list = normalize_value(raw_result)
            elif isinstance(raw_result, str):
                if raw_result.startswith("[") and raw_result.endswith("]"):
                    try:
                        import ast
                        parsed = ast.literal_eval(raw_result)
                        result = normalize_value(parsed)
                    except Exception:
                        result = []
                else:
                    result = []
            else:
                normalised = normalize_value(raw_result)
                result = normalised if isinstance(normalised, list) else []
        else:
            result = []
    else:
        decoded_query = ""
        result = []
        error = str(chain_result) if chain_result else None

    # --- Fallback: if chain produced no cypher, try direct OpenAI call ---
    if not decoded_query:
        logger.info("[RAGService] Chain produced no cypher, trying direct fallback...")
        try:
            fallback_cypher = _direct_cypher_fallback(question)
            if fallback_cypher:
                decoded_query = fallback_cypher
                try:
                    graph = get_graph()
                    fallback_result = graph.query(fallback_cypher)
                    if fallback_result:
                        result = normalize_value(fallback_result)
                        if not isinstance(result, list):
                            result = []
                        error = None
                        logger.info("[RAGService] Fallback succeeded: %d rows", len(result))
                except Exception as exec_err:
                    logger.warning("[RAGService] Fallback execution failed: %s", exec_err)
        except Exception as fb_err:
            logger.warning("[RAGService] Fallback generation failed: %s", fb_err)

    return {
        "cypher_query": decoded_query or "",
        "result": result,
        "error": error,
        "rewritten": "",
        "verified_triples": [],
        "instance_triples": [],
    }


def _direct_cypher_fallback(question: str) -> str:
    """
    Direct OpenAI call to generate Cypher, used as a last-resort fallback.
    """
    import re as _re
    from models.llm import get_cypher_llm
    from models.graph import get_graph
    from utils.helpers import clean_cypher_query, strip_noisy_return_properties

    graph = get_graph()
    schema = graph.get_schema
    llm = get_cypher_llm()
    template = get_cypher_template()

    # Build prompt from template
    prompt = template.replace("{schema}", schema).replace(
        "{question}", f"Question: {question}\n\nCypher Query:"
    )

    try:
        response = llm.invoke(prompt)
        cypher = response.content.strip()
        cypher = clean_cypher_query(cypher)
        cypher = strip_noisy_return_properties(cypher)
        return cypher
    except Exception as e:
        logger.warning("[RAGService] Direct fallback LLM call failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Info / schema helpers (used by Flask endpoints)
# ---------------------------------------------------------------------------


def get_available_databases() -> list[str]:
    """Return the list of databases that have a Cypher template."""
    from templates.cypher_templates import _DOMAIN_CONFIGS

    return list(_DOMAIN_CONFIGS.keys())


def get_database_info() -> dict:
    """Return current database config + template info (for debugging)."""
    settings = get_settings()
    db_name = settings.database_name
    return {
        "database": db_name,
        "cypher_template": get_cypher_template(db_name),
        "entity_definitions": get_entity_definitions(db_name),
        "match_properties": get_match_properties_map(db_name),
    }


def get_schema_info(database: str | None = None) -> dict:
    """Return schema info (entity defs + property map) for a given database."""
    db = database or get_settings().database_name
    return {
        "database": db,
        "entity_definitions": get_entity_definitions(db),
        "match_properties": get_match_properties_map(db),
        "has_cypher_template": db in get_available_databases(),
    }
