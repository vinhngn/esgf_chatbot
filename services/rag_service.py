"""
RAG pipeline service.
Uses Knowledge-Augmented Cypher Generation with Self-Correction.
"""

from __future__ import annotations

import copy
import logging
import threading
import urllib.parse
from collections import OrderedDict

from config import get_settings
from models.graph import get_graph, get_schema_labels, get_schema_relationships
from models.llm import get_main_llm, get_cypher_llm
from retry import retry
from services import agent_pipeline
from templates.cypher_templates import get_cypher_template
from templates.entity_definitions import get_entity_definitions
from templates.match_properties_map import get_match_properties_map
from utils.helpers import normalize_value

logger = logging.getLogger(__name__)

_CACHE_MAXSIZE = 512
_cache: "OrderedDict[tuple[str, str], dict]" = OrderedDict()
_cache_lock = threading.Lock()
NEO4J_BROWSER_URL = "https://neoforjcmip.templeuni.com/browser/"


def _neo4j_link(enc: str | None) -> str:
    if enc:
        return f"[Open Neo4J]({NEO4J_BROWSER_URL}?preselectAuthMethod=NO_AUTH&cmd=edit&arg={enc})"
    return f"[Open Neo4J]({NEO4J_BROWSER_URL})"


def _empty(r) -> bool:
    if not r: return True
    if isinstance(r, str) and r.strip() in ("", "No results found."): return True
    if isinstance(r, list) and len(r) == 0: return True
    return False


def _cached(db, q):
    with _cache_lock:
        c = _cache.get((db, q.strip()))
        if c: _cache.move_to_end((db, q.strip()))
        return copy.deepcopy(c) if c else None


def _set_cache(db, q, p):
    with _cache_lock:
        _cache[(db, q.strip())] = copy.deepcopy(p)
        _cache.move_to_end((db, q.strip()))
        while len(_cache) > _CACHE_MAXSIZE: _cache.popitem(last=False)


def _run_pipeline(question: str) -> dict:
    db = get_settings().database_name
    c = _cached(db, question)
    if c: return c

    graph = get_graph()
    result = agent_pipeline.run(
        question=question,
        graph=graph,
        schema_labels=get_schema_labels(),
        schema_relationships=get_schema_relationships(),
        full_schema=graph.get_schema,
        database=db,
        llm=get_cypher_llm(),
    )
    _set_cache(db, question, result)
    return result


def process_question(question: str, conversation_history=None) -> dict:
    conversation_history = conversation_history or []
    pipe = _run_pipeline(question)
    cypher = pipe.get("cypher_query", "")
    raw = pipe.get("result", [])
    error = pipe.get("error")
    link = _neo4j_link(pipe.get("encoded_query"))

    if error or _empty(raw):
        return {"input": question, "output": f"No results found. {link}", "cypher_query": cypher}

    conv = "\n".join(f"User: {m['input']}\nBot: {m['output']}" for m in conversation_history[-3:])
    prompt = (
        f"Based on the conversation and question, provide a helpful response.\n\n"
        f"Conversation:\n{conv}\n\nQuestion: {question}\n\n"
        f"Database output:\n{normalize_value(raw)}\n\n"
        f"Answer clearly. End with: \"Please click here to access the knowledge graph: [[btn]]\""
    )
    resp = get_main_llm().invoke(prompt).content.strip().replace("[[btn]]", link)
    return {"input": question, "output": resp, "cypher_query": cypher}


@retry(tries=2, delay=10)
def get_results(question, conversation_history=None):
    return process_question(question, conversation_history)


@retry(tries=2, delay=10)
def get_raw_results(question: str) -> dict:
    pipe = _run_pipeline(question)
    raw = pipe.get("result", [])
    return {
        "cypher_query": pipe.get("cypher_query", ""),
        "result": normalize_value(raw) if isinstance(raw, list) else [],
        "error": pipe.get("error"),
    }


def get_available_databases():
    from templates.cypher_templates import _TEMPLATE_MAP
    return list(_TEMPLATE_MAP.keys())

def get_database_info():
    db = get_settings().database_name
    return {"database": db, "cypher_template": get_cypher_template(db),
            "entity_definitions": get_entity_definitions(db),
            "match_properties": get_match_properties_map(db)}

def get_schema_info(database=None):
    db = database or get_settings().database_name
    return {"database": db, "entity_definitions": get_entity_definitions(db),
            "match_properties": get_match_properties_map(db),
            "has_cypher_template": db in get_available_databases()}
