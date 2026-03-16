"""
Triple extraction and verification service.
Restored from ES1 original - core "intelligence" layer.

Pipeline:
  1. interpret_question()              - rewrite + extract triples (no schema)
  2. interpret_question_with_schema()  - schema-guided triple extraction
  3. verify_triples()                  - validate schema + instance matching in Neo4j
  4. extract_triples_with_retry()      - full retry loop (up to MAX_ATTEMPTS)

Literal filtering: numeric values, very short strings, and reserved placeholders
(?, UNKNOWN, None, etc.) are skipped during instance matching to avoid false
positives like "1" matching Movie/Actor/User nodes by ID.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_neo4j import Neo4jGraph
from templates.entity_definitions import get_entity_definitions
from templates.match_properties_map import get_match_properties_map
from utils.helpers import strip_quotes

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
_INSTANCE_CACHE_MAXSIZE = 1024
_instance_cache: "OrderedDict[tuple[str, str], list[tuple[str, str, str]]]" = OrderedDict()
_instance_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_triple_response(response: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Parse LLM response to extract rewritten question and triples."""
    rewritten = ""
    triples: list[tuple[str, str, str]] = []

    for line in response.splitlines():
        if line.startswith("Rewritten:"):
            rewritten = line.replace("Rewritten:", "").strip()
        elif re.match(r"^\d+\.", line):
            match = re.search(r"\(([^,]+),\s*([^,]+),\s*([^)]+)\)", line)
            if match:
                triples.append(
                    tuple(strip_quotes(x.strip()) for x in match.groups())  # type: ignore[return-value]
                )

    return rewritten, triples


def _get_cached_instance_matches(
    database: str,
    literal: str,
) -> list[tuple[str, str, str]] | None:
    key = (database, literal.lower())
    with _instance_cache_lock:
        cached = _instance_cache.get(key)
        if cached is None:
            return None
        _instance_cache.move_to_end(key)
        return list(cached)


def _set_cached_instance_matches(
    database: str,
    literal: str,
    matches: list[tuple[str, str, str]],
) -> None:
    key = (database, literal.lower())
    with _instance_cache_lock:
        _instance_cache[key] = list(matches)
        _instance_cache.move_to_end(key)
        while len(_instance_cache) > _INSTANCE_CACHE_MAXSIZE:
            _instance_cache.popitem(last=False)


# ---------------------------------------------------------------------------
# Public extraction functions
# ---------------------------------------------------------------------------


def interpret_question(
    user_question: str,
    interpreter_llm,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]]]:
    """
    First-pass triple extraction — no schema constraints.
    Rewrites the question and extracts free-form semantic triples.

    Returns:
        (rewritten_question, triples)
    """
    system_prompt = (
        "You are a Neo4j graph assistant. Your job is to:\n"
        "1. Rewrite vague or unclear user questions into clear, formal English.\n"
        "2. Extract **semantic triples** from the clarified question using Neo4j schema terms.\n\n"
        "Each triple must be in the format: (subject, predicate, object)\n"
        "- Use `?` for the variable being asked about.\n"
        "- Use `UNKNOWN` if an entity isn't specified explicitly.\n\n"
        "Output format MUST be:\n"
        "Rewritten: <clarified question>\n"
        "Triples:\n"
        "1. (subject, predicate, object)\n"
        "2. ...\n\n"
        "Be concise. Do NOT add explanation or extra commentary.\n"
    )

    messages: list = [SystemMessage(content=system_prompt)]

    for turn in (conversation_history or [])[-3:]:
        messages.append(HumanMessage(content=turn["input"]))
        messages.append(AIMessage(content=turn["output"]))

    messages.append(HumanMessage(content=user_question))

    response = interpreter_llm.invoke(messages).content.strip()
    logger.debug("[TripleService] interpret_question raw response:\n%s", response)

    return _parse_triple_response(response)


def interpret_question_with_schema(
    user_question: str,
    interpreter_llm,
    schema_labels: set[str],
    schema_relationships: set[str],
    database: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]]]:
    """
    Schema-guided triple extraction.
    Only allows labels and relationship types that exist in Neo4j.

    Returns:
        (rewritten_question, triples)
    """
    labels_str = "\n".join(f"- {label}" for label in sorted(schema_labels))
    rels_str = "\n".join(f"- {r}" for r in sorted(schema_relationships))
    entity_defs = get_entity_definitions(database)

    system_prompt = f"""You are a Neo4j graph assistant.

Your job is to:
1. Rewrite vague or ambiguous user questions into **clear, formal English**.
2. Extract semantic triples using **only the approved node labels and relationship types** below.

### STRICT INSTRUCTIONS ###
- All triples must follow the format: (SubjectLabel, RELATIONSHIP_TYPE, ObjectLabel)
- Subject and Object MUST be one of the valid node labels listed below.
- Relationship MUST be from the allowed relationship types.
- DO NOT use `?`, `UNKNOWN`, or invent new labels or relationships.
- If a required element is missing, leave out the triple entirely.
- If no valid triple can be made, just say: `Rewritten: <clarified question>` and no triples.

### Allowed Node Labels:
{labels_str}

### Allowed Relationship Types:
{rels_str}

Output format:
Rewritten: <clarified question>
Triples:
1. (<subject_label>, <relationship_type>, <object_label>)
2. ...

{entity_defs}""".strip()

    messages: list = [SystemMessage(content=system_prompt)]

    for turn in (conversation_history or [])[-3:]:
        messages.append(HumanMessage(content=turn["input"]))
        messages.append(AIMessage(content=turn["output"]))

    messages.append(HumanMessage(content=user_question))

    response = interpreter_llm.invoke(messages).content.strip()
    logger.debug(
        "[TripleService] interpret_question_with_schema raw response:\n%s", response
    )

    return _parse_triple_response(response)


def verify_triples(
    triples: list[tuple[str, str, str]],
    schema_labels: set[str],
    schema_relationships: set[str],
    graph: Neo4jGraph,
    database: str,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """
    Verify triples against the Neo4j schema and find instance matches.

    Steps:
      1. Collect literal values from subject/object that are NOT labels or relationships.
      2. For each literal, search the DB across all labels and their mapped properties.
      3. Validate structural triples: subject in labels, predicate in rels, object in labels.

    Returns:
        (verified_triples, instance_triples)
    """
    verified_triples: list[tuple[str, str, str]] = []
    instance_triples: list[tuple[str, str, str]] = []

    match_map = get_match_properties_map(database)

    # Collect literals (entity names mentioned by the user, not schema terms).
    # Filter out values that would produce false-positive DB matches:
    #   - Pure numbers ("1", "3", "100") — often come from "first N" phrasing
    #   - Very short strings (< 3 chars) — too ambiguous
    #   - Reserved LLM placeholders ("?", "UNKNOWN", "None", "UNKNOWN_VALUE")
    _RESERVED = {"?", "unknown", "none", "unknown_value", "null", "true", "false"}

    literals: set[str] = set()
    for s, p, o in triples:
        for val in [strip_quotes(s), strip_quotes(o)]:
            if val in schema_labels or val in schema_relationships:
                continue
            if val.lower() in _RESERVED:
                logger.debug("[TripleService] Skipping reserved literal: %r", val)
                continue
            if val.replace(".", "").replace("-", "").isdigit():
                logger.debug("[TripleService] Skipping numeric literal: %r", val)
                continue
            if len(val) < 3:
                logger.debug("[TripleService] Skipping short literal: %r", val)
                continue
            literals.add(val)

    # Instance matching: try to find each literal as an actual node in the DB.
    #
    # Neo4j's toString() crashes on LIST properties (e.g. Movie.countries is
    # StringArray).  We therefore try two separate queries per property:
    #   1. Scalar query  – toLower(toString(n.prop)) = toLower($name)
    #   2. List   query  – any(item IN n.prop WHERE toLower(item) = toLower($name))
    # The list query is only attempted when the scalar query raises a TypeError.
    for literal in literals:
        cached_matches = _get_cached_instance_matches(database, literal)
        if cached_matches is not None:
            for triple in cached_matches:
                if triple not in instance_triples:
                    instance_triples.append(triple)
            continue

        literal_matches: list[tuple[str, str, str]] = []
        for label in schema_labels:
            properties_to_try = match_map.get(label, ["name"])
            for prop in properties_to_try:
                found = False

                # --- attempt 1: scalar match ---
                scalar_query = (
                    f"MATCH (n:{label}) "
                    f"WHERE n.{prop} IS NOT NULL "
                    f"  AND toLower(toString(n.{prop})) = toLower($name) "
                    f"RETURN n LIMIT 1"
                )
                try:
                    result = graph.query(scalar_query, {"name": literal})
                    if result:
                        found = True
                except Exception as scalar_err:
                    err_str = str(scalar_err)
                    if (
                        "TypeError" in err_str
                        or "StringArray" in err_str
                        or "invalid" in err_str.lower()
                    ):
                        # Property is likely a list — try list match
                        list_query = (
                            f"MATCH (n:{label}) "
                            f"WHERE n.{prop} IS NOT NULL "
                            f"  AND any(item IN n.{prop} "
                            f"          WHERE toLower(toString(item)) = toLower($name)) "
                            f"RETURN n LIMIT 1"
                        )
                        try:
                            result = graph.query(list_query, {"name": literal})
                            if result:
                                found = True
                        except Exception as list_err:
                            logger.warning(
                                "[TripleService] List-match also failed for '%s' on %s.%s: %s",
                                literal,
                                label,
                                prop,
                                list_err,
                            )
                    else:
                        logger.warning(
                            "[TripleService] Error checking '%s' on %s.%s: %s",
                            literal,
                            label,
                            prop,
                            scalar_err,
                        )

                if found:
                    triple = (literal, "instanceOf", label)
                    if triple not in literal_matches:
                        literal_matches.append(triple)
                    if triple not in instance_triples:
                        instance_triples.append(triple)
                        logger.info(
                            "[TripleService] Found instance: %s -> %s",
                            literal,
                            label,
                        )
                    break  # found in this label, no need to check other props
        _set_cached_instance_matches(database, literal, literal_matches)

    # Validate structural triples against schema
    for s, p, o in triples:
        if p in schema_relationships and s in schema_labels and o in schema_labels:
            verified_triples.append((s, p, o))

    logger.info(
        "[TripleService] verify_triples -> verified=%d instance=%d",
        len(verified_triples),
        len(instance_triples),
    )
    return verified_triples, instance_triples


# ---------------------------------------------------------------------------
# Full retry pipeline (public entry point)
# ---------------------------------------------------------------------------


def extract_triples_with_retry(
    question: str,
    interpreter_llm,
    schema_labels: set[str],
    schema_relationships: set[str],
    graph: Neo4jGraph,
    database: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """
    Full triple extraction pipeline with retry loop (up to MAX_ATTEMPTS).

    Attempt 0  -> interpret_question()              (no schema)
    Attempts 1+ -> interpret_question_with_schema() (schema-guided)

    Accumulates instance_triples across retries (no duplicates).
    Stops early if verified_triples are found.
    Falls back to raw triples if all attempts fail.

    Returns:
        (rewritten, verified_triples, instance_triples)
    """
    verified_triples: list[tuple[str, str, str]] = []
    instance_triples: list[tuple[str, str, str]] = []
    rewritten = ""
    raw_triples: list[tuple[str, str, str]] = []

    for attempt in range(MAX_ATTEMPTS):
        logger.info("[TripleService] extract attempt %d/%d", attempt + 1, MAX_ATTEMPTS)

        if attempt == 0:
            rewritten, triples = interpret_question(
                question, interpreter_llm, conversation_history
            )
        else:
            rewritten, triples = interpret_question_with_schema(
                question,
                interpreter_llm,
                schema_labels,
                schema_relationships,
                database,
                conversation_history,
            )

        raw_triples = triples  # keep latest for fallback

        temp_verified, temp_instance = verify_triples(
            triples, schema_labels, schema_relationships, graph, database
        )

        # Accumulate instance triples across retries (no duplicates)
        for t in temp_instance:
            if t not in instance_triples:
                instance_triples.append(t)

        if temp_verified:
            verified_triples = temp_verified
            logger.info(
                "[TripleService] Got %d verified triple(s) on attempt %d",
                len(verified_triples),
                attempt + 1,
            )
            break

    # Fallback: use raw triples if verification never succeeded
    if not verified_triples:
        logger.warning(
            "[TripleService] No verified triples after %d attempts, "
            "using raw triples as fallback",
            MAX_ATTEMPTS,
        )
        verified_triples = raw_triples

    logger.info(
        "[TripleService] Final -> rewritten=%r verified=%d instance=%d",
        rewritten,
        len(verified_triples),
        len(instance_triples),
    )
    return rewritten, verified_triples, instance_triples


def build_enhanced_question(
    question: str,
    rewritten: str,
    verified_triples: list[tuple[str, str, str]],
    instance_triples: list[tuple[str, str, str]],
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """
    Build the enriched query string passed to the Cypher chain.

    Includes:
      - Recent conversation history (last 3 turns)
      - Original question
      - Rewritten (clarified) question
      - Verified triples (schema-validated)
      - Instance triples (actual DB entity matches)
    """
    triples_text = (
        "\n".join(f"({s}, {r}, {o})" for s, r, o in verified_triples) or "None"
    )
    instance_text = (
        "\n".join(f"({s}, {r}, {o})" for s, r, o in instance_triples) or "None"
    )

    parts: list[str] = []

    if conversation_history:
        conversation_text = "\n".join(
            f"User: {msg['input']}\nBot: {msg['output']}"
            for msg in conversation_history[-3:]
        )
        if conversation_text.strip():
            parts.append(f"Conversation History:\n{conversation_text}")

    parts.append(f"Question: {question}")
    parts.append(f"Rewritten: {rewritten or question}")
    parts.append(f"Verified Triples:\n{triples_text}")
    parts.append(f"Instance Triples:\n{instance_text}")

    return "\n\n".join(parts)
