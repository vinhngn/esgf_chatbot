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

import logging
import re
import urllib.parse
from typing import Any

from config import get_settings
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from models.chain import invoke_chain
from models.graph import (
    get_graph,
    get_schema_context,
    get_schema_labels,
    get_schema_node_properties,
    get_schema_patterns,
    get_schema_relationships,
)
from models.llm import get_interpreter_llm, get_main_llm
from retry import retry
from templates.cypher_templates import get_cypher_template
from templates.entity_definitions import get_entity_definitions
from templates.match_properties_map import get_match_properties_map
from utils.helpers import normalize_value

from services.query_ir import build_query_ir, render_cypher_from_ir
from services.triple_service import build_enhanced_question, extract_triples_with_retry

logger = logging.getLogger(__name__)
RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
    ServiceUnavailable,
    SessionExpired,
    TransientError,
)

# Neo4j browser base URL
NEO4J_BROWSER_URL = "https://neoforjcmip.templeuni.com/browser/"
_LABEL_ALIAS_RE = re.compile(r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z][A-Za-z0-9_]*)")
_MAP_LABEL_RE = re.compile(r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z][A-Za-z0-9_]*)\s*\{([^}]*)\}")
_REL_PATTERN_RE = re.compile(r"\[:([A-Z_]+)\]")
_PROP_REF_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_cypher_queries(chain_result: dict) -> tuple[str | None, str | None]:
    """Extract the (encoded, decoded) Cypher query from intermediate_steps."""
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


def _validate_direct_cypher_against_schema(
    cypher: str,
    schema_relationships: set[str],
    schema_node_properties: dict[str, list[str]],
) -> list[str]:
    issues: list[str] = []
    alias_to_label: dict[str, str] = {}
    for alias, label in _LABEL_ALIAS_RE.findall(cypher or ""):
        alias_to_label[alias] = label
    for alias, label, body in _MAP_LABEL_RE.findall(cypher or ""):
        alias_to_label[alias] = label
        for raw in body.split(","):
            if ":" not in raw:
                continue
            prop = raw.split(":", 1)[0].strip()
            if prop and prop not in schema_node_properties.get(label, []):
                issues.append(f"unknown property {label}.{prop}")
    for rel in _REL_PATTERN_RE.findall(cypher or ""):
        if rel not in schema_relationships:
            issues.append(f"unknown relationship {rel}")
    for alias, prop in _PROP_REF_RE.findall(cypher or ""):
        label = alias_to_label.get(alias)
        if not label:
            continue
        if prop not in schema_node_properties.get(label, []):
            issues.append(f"unknown property {label}.{prop}")
    deduped: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        if issue in seen:
            continue
        seen.add(issue)
        deduped.append(issue)
    return deduped


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
      3. Invoke Cypher chain            (GraphCypherQAChain → Neo4j)

    Returns a dict with keys:
        rewritten          : str
        verified_triples   : list[tuple[str,str,str]]
        instance_triples   : list[tuple[str,str,str]]
        intent             : dict[str, Any]
        chain_result       : dict | str   (raw chain output)
        encoded_query      : str | None
        decoded_query      : str | None
    """
    db_name = get_settings().database_name
    interpreter_llm = get_interpreter_llm()
    graph = get_graph()
    schema_labels = get_schema_labels()
    schema_relationships = get_schema_relationships()
    schema_node_properties = get_schema_node_properties()
    schema_patterns = get_schema_patterns()
    schema_context = get_schema_context()

    # --- Step 1: Triple extraction with retry ---
    (
        rewritten,
        verified_triples,
        instance_triples,
        intent,
        query_plan,
        return_contract,
        path_hints,
        query_constraints,
    ) = extract_triples_with_retry(
        question=question,
        interpreter_llm=interpreter_llm,
        schema_labels=schema_labels,
        schema_relationships=schema_relationships,
        schema_patterns=schema_patterns,
        graph=graph,
        database=db_name,
        schema_context=schema_context,
        conversation_history=conversation_history,
    )

    logger.info("[RAGService] rewritten=%r", rewritten)
    logger.info("[RAGService] intent=%s", intent)
    logger.info("[RAGService] query_plan=%s", query_plan)
    logger.info("[RAGService] return_contract=%s", return_contract)
    logger.info("[RAGService] path_hints=%s", path_hints)
    logger.info("[RAGService] query_constraints=%s", query_constraints)
    logger.info("[RAGService] verified_triples=%s", verified_triples)
    logger.info("[RAGService] instance_triples=%s", instance_triples)

    query_ir = build_query_ir(
        database=db_name,
        verified_triples=verified_triples,
        instance_triples=instance_triples,
        intent=intent,
        query_plan=query_plan,
        return_contract=return_contract,
        path_hints=path_hints,
        query_constraints=query_constraints,
    )
    logger.info("[RAGService] query_ir=%s", query_ir)

    # --- Step 2: Build enriched question ---
    enhanced_question = build_enhanced_question(
        question=question,
        rewritten=rewritten,
        verified_triples=verified_triples,
        instance_triples=instance_triples,
        intent=intent,
        query_plan=query_plan,
        return_contract=return_contract,
        path_hints=path_hints,
        query_constraints=query_constraints,
        query_ir=query_ir,
        database=db_name,
        schema_context=schema_context,
        conversation_history=conversation_history,
    )

    direct_cypher = render_cypher_from_ir(query_ir)
    if direct_cypher:
        schema_issues = _validate_direct_cypher_against_schema(
            direct_cypher,
            schema_relationships,
            schema_node_properties,
        )
        if schema_issues:
            logger.warning(
                "[RAGService] Skipping generic IR query due to schema issues: %s | cypher=%s",
                schema_issues,
                direct_cypher,
            )
        else:
            logger.info("[RAGService] Answering via generic IR renderer.")
            try:
                direct_result = graph.query(direct_cypher)
                encoded_query = urllib.parse.quote(direct_cypher)
                return {
                    "rewritten": rewritten,
                    "verified_triples": verified_triples,
                    "instance_triples": instance_triples,
                    "intent": intent,
                    "query_ir": query_ir,
                    "query_plan": query_plan,
                    "return_contract": return_contract,
                    "path_hints": path_hints,
                    "query_constraints": query_constraints,
                    "chain_result": {
                        "result": direct_result,
                        "intermediate_steps": [{"query": encoded_query}],
                    },
                    "encoded_query": encoded_query,
                    "decoded_query": direct_cypher,
                }
            except Exception as e:
                logger.warning("[RAGService] Generic IR query failed, falling back to chain: %s", e)

    # --- Step 3: Invoke chain ---
    chain_result = invoke_chain(enhanced_question)

    encoded_query, decoded_query = (
        _extract_cypher_queries(chain_result)
        if isinstance(chain_result, dict)
        else (None, None)
    )

    return {
        "rewritten": rewritten,
        "verified_triples": verified_triples,
        "instance_triples": instance_triples,
        "intent": intent,
        "query_ir": query_ir,
        "query_plan": query_plan,
        "return_contract": return_contract,
        "path_hints": path_hints,
        "query_constraints": query_constraints,
        "chain_result": chain_result,
        "encoded_query": encoded_query,
        "decoded_query": decoded_query,
    }


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
    intent: dict[str, Any] = pipe.get("intent", {})
    query_ir: dict[str, Any] = pipe.get("query_ir", {})
    query_plan: dict[str, Any] = pipe.get("query_plan", {})
    return_contract: dict[str, Any] = pipe.get("return_contract", {})
    path_hints: dict[str, Any] = pipe.get("path_hints", {})
    query_constraints: dict[str, Any] = pipe.get("query_constraints", {})

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
            "intent": intent,
            "query_ir": query_ir,
            "query_plan": query_plan,
            "return_contract": return_contract,
            "path_hints": path_hints,
            "query_constraints": query_constraints,
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
            f"Intent hints: {intent}\n\n"
            f"Structured query plan: {query_plan}\n\n"
            f"Return contract hints: {return_contract}\n\n"
            f"Path hints: {path_hints}\n\n"
            f"Query constraints: {query_constraints}\n\n"
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
        "intent": intent,
        "query_ir": query_ir,
        "query_plan": query_plan,
        "return_contract": return_contract,
        "path_hints": path_hints,
        "query_constraints": query_constraints,
    }


@retry(exceptions=RETRYABLE_EXCEPTIONS, tries=2, delay=2)
def get_results(
    question: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict:
    """Public entry point for the RAG pipeline (with LLM-formatted response)."""
    return process_question(question, conversation_history)


@retry(exceptions=RETRYABLE_EXCEPTIONS, tries=2, delay=2)
def get_raw_results(question: str) -> dict:
    """
    Flask / T2C evaluation entry point.
    Runs the full pipeline (triple extraction → chain) but skips LLM formatting.

    Returns dict with keys:
        cypher_query     : str
        result           : list  (JSON-safe, normalised)
        error            : str | None
        rewritten        : str
        verified_triples : list[list[str]]
        instance_triples : list[list[str]]
    """
    pipe = _run_pipeline(question, conversation_history=[])
    chain_result = pipe["chain_result"]
    decoded_query = pipe["decoded_query"]
    rewritten = pipe["rewritten"]
    verified_triples = pipe["verified_triples"]
    instance_triples = pipe["instance_triples"]
    intent: dict[str, Any] = pipe.get("intent", {})
    query_ir: dict[str, Any] = pipe.get("query_ir", {})
    query_plan: dict[str, Any] = pipe.get("query_plan", {})
    return_contract: dict[str, Any] = pipe.get("return_contract", {})
    path_hints: dict[str, Any] = pipe.get("path_hints", {})
    query_constraints: dict[str, Any] = pipe.get("query_constraints", {})

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
        # chain_result is an error string
        decoded_query = ""
        result = []
        error = str(chain_result) if chain_result else None

    return {
        "cypher_query": decoded_query or "",
        "result": result,
        "error": error,
        "rewritten": rewritten,
        "verified_triples": [list(t) for t in verified_triples],
        "instance_triples": [list(t) for t in instance_triples],
        "intent": intent,
        "query_ir": query_ir,
        "query_plan": query_plan,
        "return_contract": return_contract,
        "path_hints": path_hints,
        "query_constraints": query_constraints,
    }


# ---------------------------------------------------------------------------
# Info / schema helpers (used by Flask endpoints)
# ---------------------------------------------------------------------------


def get_available_databases() -> list[str]:
    """Return the list of databases that have a Cypher template."""
    from templates.cypher_templates import _TEMPLATE_MAP

    return list(_TEMPLATE_MAP.keys())


def get_database_info() -> dict:
    """Return current database config + template info (for debugging)."""
    settings = get_settings()
    db_name = settings.database_name
    return {
        "database": db_name,
        "use_generalized_template": settings.USE_GENERALIZED_TEMPLATE,
        "cypher_template": get_cypher_template(db_name),
        "entity_definitions": get_entity_definitions(db_name),
        "match_properties": get_match_properties_map(db_name),
    }


def get_schema_info(database: str | None = None) -> dict:
    """Return schema info (entity defs + property map) for a given database."""
    db = database or get_settings().database_name
    return {
        "database": db,
        "use_generalized_template": get_settings().USE_GENERALIZED_TEMPLATE,
        "entity_definitions": get_entity_definitions(db),
        "match_properties": get_match_properties_map(db),
        "has_cypher_template": db in get_available_databases(),
    }
