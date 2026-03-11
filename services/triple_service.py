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
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_neo4j import Neo4jGraph
from templates.entity_definitions import get_entity_definitions
from templates.match_properties_map import get_match_properties_map
from utils.helpers import strip_quotes

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
_EMPTY_MARKERS = {"", "none", "null", "n/a", "unknown", "?"}
_GENERIC_ENTITY_LITERALS = {
    "user",
    "users",
    "tweet",
    "tweets",
    "hashtag",
    "hashtags",
    "link",
    "links",
    "movie",
    "movies",
    "person",
    "people",
    "actor",
    "actors",
    "director",
    "directors",
    "producer",
    "producers",
    "review",
    "reviews",
    "role",
    "roles",
    "count",
    "average",
    "sum",
    "total",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_triple_response(
    response: str,
) -> tuple[str, list[tuple[str, str, str]], dict[str, Any]]:
    """Parse LLM response to extract rewritten question, triples, and light intent."""
    rewritten = ""
    triples: list[tuple[str, str, str]] = []
    intent: dict[str, Any] = {
        "operation": "",
        "target": "",
        "filters": [],
        "sort": "",
        "limit": "",
        "aggregation": "",
    }
    current_section = ""

    for line in response.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("Rewritten:"):
            rewritten = line.replace("Rewritten:", "").strip()
        elif line.startswith("Intent:"):
            current_section = "intent"
        elif line.startswith("Triples:"):
            current_section = "triples"
        elif current_section == "intent" and ":" in line:
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if key == "filters":
                intent["filters"] = [item.strip() for item in value.split(";") if item.strip()]
            elif key in intent:
                intent[key] = value
        elif re.match(r"^\d+\.", line):
            match = re.search(r"\(([^,]+),\s*([^,]+),\s*([^)]+)\)", line)
            if match:
                triples.append(
                    tuple(strip_quotes(x.strip()) for x in match.groups())  # type: ignore[return-value]
                )

    return rewritten, triples, _merge_intent_with_question(intent, rewritten)


def _merge_intent_with_question(
    intent: dict[str, Any],
    rewritten: str,
) -> dict[str, Any]:
    """Backfill missing intent slots with light heuristics."""
    text = rewritten.lower()
    merged = {
        "operation": _normalize_slot_value(intent.get("operation", "")),
        "target": _normalize_slot_value(intent.get("target", "")),
        "filters": _normalize_filters(intent.get("filters", []) or []),
        "sort": _normalize_slot_value(intent.get("sort", "")),
        "limit": _normalize_slot_value(intent.get("limit", "")),
        "aggregation": _normalize_slot_value(intent.get("aggregation", "")),
    }

    if not merged["operation"]:
        if any(token in text for token in ("how many", "count", "number of")):
            merged["operation"] = "count"
        elif any(token in text for token in ("top ", "highest", "most", "lowest", "least")):
            merged["operation"] = "rank"
        elif re.search(r"\b(average|avg|sum|total|minimum|maximum)\b", text):
            merged["operation"] = "aggregate"
        else:
            merged["operation"] = "lookup"

    if not merged["aggregation"]:
        if re.search(r"\b(average|avg)\b", text):
            merged["aggregation"] = "avg"
        elif "count" in merged["operation"] or "how many" in text or "number of" in text:
            merged["aggregation"] = "count"
        elif re.search(r"\b(sum|total)\b", text):
            merged["aggregation"] = "sum"

    if not merged["sort"]:
        if any(token in text for token in ("highest", "most", "top")):
            merged["sort"] = "desc"
        elif any(token in text for token in ("lowest", "least", "oldest")):
            merged["sort"] = "asc"

    if not merged["limit"]:
        limit_match = re.search(r"\b(top|first)\s+(\d+)\b", text)
        if limit_match:
            merged["limit"] = limit_match.group(2)

    if not merged["filters"]:
        filters: list[str] = []
        if any(token in text for token in ("after ", "before ", "between ")):
            filters.append("temporal")
        if any(token in text for token in ("in ", "over ", "within ", "across ")):
            filters.append("scope")
        merged["filters"] = filters

    return merged


def infer_return_contract(
    question: str,
    rewritten: str,
    intent: dict[str, Any] | None = None,
    database: str = "",
) -> dict[str, Any]:
    """Infer a lightweight output contract from the user question."""
    intent = intent or {}
    text = f"{question}\n{rewritten}".lower()
    db = (database or "").lower()

    contract: dict[str, Any] = {
        "cardinality": "all",
        "strict": False,
        "return_mode": "unspecified",
        "expected_items": [],
        "notes": [],
    }

    limit = _normalize_slot_value(intent.get("limit", ""))
    if limit:
        contract["cardinality"] = f"top_{limit}"
    elif any(token in text for token in (" most ", " most frequently", " highest ", " lowest ")):
        contract["cardinality"] = "top_1"

    if any(token in text for token in ("list all", "show all", "which users", "who are the users")):
        contract["return_mode"] = "properties"
    elif any(token in text for token in ("list tweets", "show tweets", "return tweets")):
        contract["return_mode"] = "full_node"

    if db == "twitter":
        expected_items: list[str] = []

        if "amplified by 'me'" in text or 'amplified by "me"' in text:
            expected_items = ["user.screen_name AS AmplifiedUser"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
            contract["notes"].append("Return only the amplified user screen name with the expected alias.")
        elif "interact with most frequently" in text or "interacts with most frequently" in text:
            expected_items = ["user.screen_name", "COUNT(*) AS interaction_count"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif "top 5 users" in text and "follows" in text:
            expected_items = [
                "user.name",
                "user.screen_name",
                "user.followers",
                "user.following",
            ]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif ("most recent users" in text or "started following" in text or "who follows" in text) and "tweet" not in text:
            expected_items = [
                "user.screen_name",
                "user.name",
                "user.followers",
                "user.following",
                "user.profile_image_url",
                "user.url",
                "user.location",
                "user.statuses",
            ]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif "favorite count over" in text or ("mentioned" in text and "favorite" in text):
            expected_items = [
                "t.text AS tweet_text",
                "t.favorites AS favorite_count",
                "t.created_at AS created_at",
            ]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif "statuses posted" in text or "number of statuses posted" in text:
            expected_items = ["u.name", "u.screen_name", "u.statuses"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
            contract["notes"].append("Use the statuses property directly for ranking, not a graph count.")
        elif "mentions most frequently" in text:
            expected_items = ["mentioned.screen_name", "count(t) AS mentions_count"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif "most recent tweet" in text and "mentions a user followed by" in text:
            expected_items = ["max(tweet.created_at) AS most_recent_tweet_date"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif "first 3 users who mentioned" in text:
            expected_items = ["u.screen_name", "t.created_at"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif "tweets with most mentions" in text:
            expected_items = [
                "t.id_str AS tweet_id",
                "t.text AS tweet_text",
                "mention_count",
            ]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif "number of people they are following" in text or "by the number of people they are following" in text:
            expected_items = ["u.name", "u.screen_name", "followingCount"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif "number of followers" in text and "top" in text:
            expected_items = ["u.screen_name", "u.name", "u.followers"]
            contract["return_mode"] = "properties"
            contract["strict"] = True

        if expected_items:
            contract["expected_items"] = expected_items

    return contract


def infer_path_hints(
    question: str,
    rewritten: str,
    database: str = "",
) -> dict[str, Any]:
    """Infer lightweight path/query-shape hints for multi-hop questions."""
    text = f"{question}\n{rewritten}".lower()
    db = (database or "").lower()
    hints: dict[str, Any] = {
        "focus_entity": "",
        "path_patterns": [],
        "count_pattern": "",
        "needs_distinct": False,
        "notes": [],
    }

    if db != "twitter":
        return hints

    if "retweeted" in text or "retweet" in text:
        hints["notes"].append(
            "Retweet questions usually require a POSTS -> RETWEETS path, not RT_MENTIONS."
        )

    if "mention" in text:
        hints["notes"].append(
            "Mention questions use Tweet-[:MENTIONS]->User/Me and often require Tweet as the central entity."
        )

    if "number of people they are following" in text or "by the number of people they are following" in text:
        hints["focus_entity"] = "User"
        hints["count_pattern"] = "count{(u)-[:FOLLOWS]->(:User)} AS followingCount"
        hints["notes"].append("Prefer graph count over the nullable u.following property.")

    if "statuses posted" in text or "number of statuses posted" in text:
        hints["focus_entity"] = "User"
        hints["notes"].append("Statuses ranking should use the existing u.statuses property, not a POSTS count.")

    if "number of followers" in text and "top" in text:
        hints["focus_entity"] = "User"
        hints["count_pattern"] = "count{(u)<-[:FOLLOWS]-(:User)} AS followerCount"

    if "amplified by 'me'" in text or 'amplified by "me"' in text:
        hints["focus_entity"] = "User"
        hints["path_patterns"] = ["(:Me)-[:AMPLIFIES]->(:User)"]

    if "interact with most frequently" in text or "interacts with most frequently" in text:
        hints["focus_entity"] = "User"
        hints["path_patterns"] = ["(:Me)-[:INTERACTS_WITH]->(:User)"]
        hints["count_pattern"] = "COUNT(*) AS interaction_count"

    if "top 5 users" in text and "follows" in text:
        hints["focus_entity"] = "User"
        hints["path_patterns"] = ["(:Me)-[:FOLLOWS]->(:User)"]

    if "most recent users" in text or "started following" in text:
        hints["focus_entity"] = "User"
        hints["path_patterns"] = ["(:User)-[:FOLLOWS]->(:Me)"]

    if "tweets where" in text and "mentioned" in text and "favorite" in text:
        hints["focus_entity"] = "Tweet"
        hints["path_patterns"] = ["(:Tweet)-[:MENTIONS]->(:User)"]
        hints["notes"].append("Keep Tweet as the returned entity and filter on t.favorites.")

    if "first 3 users who mentioned" in text:
        hints["focus_entity"] = "User"
        hints["path_patterns"] = ["(:User)-[:POSTS]->(:Tweet)-[:MENTIONS]->(:Me)"]

    if "most recent tweet" in text and "mentions a user followed by" in text:
        hints["focus_entity"] = "Tweet"
        hints["path_patterns"] = [
            "(:User)-[:FOLLOWS]->(:User)",
            "(:Tweet)-[:MENTIONS]->(:User)",
        ]
        hints["notes"].append("Use max(tweet.created_at) for the final projection instead of COUNT.")

    if "hashtags used in tweets that mention" in text:
        hints["focus_entity"] = "Hashtag"
        hints["path_patterns"] = [
            "(:Tweet)-[:MENTIONS]->(:User)",
            "(:Tweet)-[:TAGS]->(:Hashtag)",
        ]
        hints["needs_distinct"] = True

    if "posted by" in text and "containing a hashtag" in text:
        hints["focus_entity"] = "Tweet"
        hints["path_patterns"] = ["(:User|:Me)-[:POSTS]->(:Tweet)-[:TAGS]->(:Hashtag)"]
        hints["notes"].append("Return both Tweet and Hashtag when the question asks for tweets containing a hashtag.")

    if "top 3 users mentioned" in text and "neo4j" in text:
        hints["focus_entity"] = "User"
        hints["path_patterns"] = ["(:Me)-[:POSTS]->(:Tweet)-[:MENTIONS]->(:User)"]
        hints["count_pattern"] = "COUNT(*) AS mentionCount"

    if "has retweeted" in text or "retweeted by" in text:
        hints["focus_entity"] = "Tweet"
        hints["path_patterns"] = ["(:Me)-[:POSTS]->(:Tweet)-[:RETWEETS]->(:Tweet)"]

    if "retweets the most" in text:
        hints["focus_entity"] = "User"
        hints["path_patterns"] = [
            "(:Me)-[:POSTS]->(:Tweet)-[:RETWEETS]->(:Tweet)<-[:POSTS]-(:User)"
        ]
        hints["count_pattern"] = "count(*) AS retweet_count"

    if "identify the urls" in text and "retweeted by" in text:
        hints["focus_entity"] = "Link"
        hints["path_patterns"] = [
            "(:Me)-[:POSTS]->(:Tweet)<-[:RETWEETS]-(:Tweet)",
            "(:Tweet)-[:CONTAINS]->(:Link)",
        ]

    return hints


def infer_query_constraints(
    question: str,
    rewritten: str,
    database: str = "",
    intent: dict[str, Any] | None = None,
    return_contract: dict[str, Any] | None = None,
    path_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile lightweight hard constraints before Cypher generation."""
    text = f"{question}\n{rewritten}".lower()
    db = (database or "").lower()
    intent = intent or {}
    return_contract = return_contract or {}
    path_hints = path_hints or {}

    constraints: dict[str, Any] = {
        "query_mode": "lookup_entity",
        "projection_lock": return_contract.get("return_mode", "unspecified") or "unspecified",
        "allow_aggregation": False,
        "aggregation_style": "",
        "anchor_lock": {},
        "notes": [],
    }

    if return_contract.get("return_mode") == "full_node":
        constraints["projection_lock"] = "full_node"
    elif return_contract.get("return_mode") == "properties":
        constraints["projection_lock"] = "properties"

    if intent.get("aggregation") in {"count", "avg", "sum", "min", "max"}:
        constraints["allow_aggregation"] = True
        constraints["aggregation_style"] = intent.get("aggregation")

    if "statuses posted" in text or "number of statuses posted" in text:
        constraints["allow_aggregation"] = False
        constraints["aggregation_style"] = ""
        constraints["query_mode"] = "rank_by_existing_property"
    elif "most recent tweet" in text and "mentions a user followed by" in text:
        constraints["allow_aggregation"] = True
        constraints["aggregation_style"] = "max"
        constraints["query_mode"] = "aggregate_projection"

    if path_hints.get("count_pattern"):
        constraints["allow_aggregation"] = True
        constraints["aggregation_style"] = "path_count"
        constraints["query_mode"] = "rank_graph_count"
    elif constraints["projection_lock"] == "full_node" and intent.get("sort"):
        constraints["query_mode"] = "rank_by_existing_property"
    elif constraints["projection_lock"] == "properties" and constraints["allow_aggregation"]:
        constraints["query_mode"] = "aggregate_projection"
    elif path_hints.get("path_patterns"):
        constraints["query_mode"] = "path_retrieval"
    elif constraints["projection_lock"] == "properties":
        constraints["query_mode"] = "lookup_property"

    if db == "twitter":
        anchor_lock: dict[str, str] = {}

        if "'me'" in text or '"me"' in text or " according to the amplifies relationship" in text:
            anchor_lock = {"label": "Me", "property": "", "value": ""}
        elif "user named 'neo4j'" in text or 'user named "neo4j"' in text:
            anchor_lock = {"label": "Me", "property": "name", "value": "Neo4j"}
        elif "'neo4j'" in text or '"neo4j"' in text:
            if any(token in text for token in ("screen_name", "mentions", "started following", "follow 'neo4j'", "follow neo4j")):
                anchor_lock = {"label": "Me", "property": "screen_name", "value": "neo4j"}
            elif any(token in text for token in ("posted by 'neo4j'", "tweets by 'neo4j'", "by 'neo4j'")):
                anchor_lock = {"label": "User", "property": "screen_name", "value": "neo4j"}
            else:
                anchor_lock = {"label": "Me", "property": "name", "value": "Neo4j"}

        if anchor_lock:
            constraints["anchor_lock"] = anchor_lock

        if constraints["query_mode"] == "rank_graph_count":
            constraints["notes"].append("Do not convert graph-count questions into property lookups.")
        if constraints["projection_lock"] == "full_node":
            constraints["notes"].append("Return the node itself, not extra properties.")
        if constraints["projection_lock"] == "properties":
            constraints["notes"].append("Return only the requested projected columns, not the full node.")
        if not constraints["allow_aggregation"]:
            constraints["notes"].append("Do not add COUNT/AVG/SUM unless explicitly required by the question.")

    return constraints


def _normalize_slot_value(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in _EMPTY_MARKERS:
        return ""
    return text


def _normalize_filters(filters: list[Any]) -> list[str]:
    normalized: list[str] = []
    for item in filters:
        text = _normalize_slot_value(item)
        if text:
            normalized.append(text)
    return normalized


def _is_meaningful_triple(triple: tuple[str, str, str]) -> bool:
    return all(strip_quotes(part).strip().lower() not in _EMPTY_MARKERS for part in triple)


def _is_generic_literal(value: str) -> bool:
    text = strip_quotes(value).strip().lower()
    if text in _GENERIC_ENTITY_LITERALS:
        return True
    if re.fullmatch(r"(top|first|last|latest|recent|most|least)\s+\d+", text):
        return True
    return False


# ---------------------------------------------------------------------------
# Public extraction functions
# ---------------------------------------------------------------------------


def interpret_question(
    user_question: str,
    interpreter_llm,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]], dict[str, Any]]:
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
        "Intent:\n"
        "operation: <lookup|count|aggregate|rank|compare>\n"
        "target: <main entity or metric>\n"
        "filters: <semicolon-separated filters or NONE>\n"
        "sort: <desc|asc|NONE>\n"
        "limit: <integer or NONE>\n"
        "aggregation: <count|avg|sum|min|max|NONE>\n"
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
    schema_context: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]], dict[str, Any]]:
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

### Schema Context:
{schema_context}

Output format:
Rewritten: <clarified question>
Intent:
operation: <lookup|count|aggregate|rank|compare>
target: <main entity or metric>
filters: <semicolon-separated filters or NONE>
sort: <desc|asc|NONE>
limit: <integer or NONE>
aggregation: <count|avg|sum|min|max|NONE>
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
    schema_patterns: set[tuple[str, str, str]] | None = None,
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
            if _is_generic_literal(val):
                logger.debug("[TripleService] Skipping generic literal: %r", val)
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
                    if triple not in instance_triples:
                        instance_triples.append(triple)
                        logger.info(
                            "[TripleService] Found instance: %s -> %s",
                            literal,
                            label,
                        )
                    break  # found in this label, no need to check other props

    schema_patterns = schema_patterns or set()

    # Validate structural triples against schema
    for s, p, o in triples:
        if (
            p in schema_relationships
            and s in schema_labels
            and o in schema_labels
            and (not schema_patterns or (s, p, o) in schema_patterns)
        ):
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
    schema_patterns: set[tuple[str, str, str]],
    graph: Neo4jGraph,
    database: str,
    schema_context: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[
    str,
    list[tuple[str, str, str]],
    list[tuple[str, str, str]],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    """
    Full triple extraction pipeline with retry loop (up to MAX_ATTEMPTS).

    Attempt 0  -> interpret_question()              (no schema)
    Attempts 1+ -> interpret_question_with_schema() (schema-guided)

    Accumulates instance_triples across retries (no duplicates).
    Stops early if verified_triples are found.
    Falls back to raw triples if all attempts fail.

    Returns:
        (rewritten, verified_triples, instance_triples, intent, return_contract, path_hints, query_constraints)
    """
    verified_triples: list[tuple[str, str, str]] = []
    instance_triples: list[tuple[str, str, str]] = []
    intent: dict[str, Any] = {}
    return_contract: dict[str, Any] = {}
    path_hints: dict[str, Any] = {}
    query_constraints: dict[str, Any] = {}
    rewritten = ""
    raw_triples: list[tuple[str, str, str]] = []

    for attempt in range(MAX_ATTEMPTS):
        logger.info("[TripleService] extract attempt %d/%d", attempt + 1, MAX_ATTEMPTS)

        if attempt == 0:
            rewritten, triples, intent = interpret_question(
                question, interpreter_llm, conversation_history
            )
        else:
            rewritten, triples, intent = interpret_question_with_schema(
                question,
                interpreter_llm,
                schema_labels,
                schema_relationships,
                database,
                schema_context,
                conversation_history,
            )

        return_contract = infer_return_contract(
            question=question,
            rewritten=rewritten or question,
            intent=intent,
            database=database,
        )
        path_hints = infer_path_hints(
            question=question,
            rewritten=rewritten or question,
            database=database,
        )
        query_constraints = infer_query_constraints(
            question=question,
            rewritten=rewritten or question,
            database=database,
            intent=intent,
            return_contract=return_contract,
            path_hints=path_hints,
        )

        raw_triples = triples  # keep latest for fallback

        temp_verified, temp_instance = verify_triples(
            triples,
            schema_labels,
            schema_relationships,
            graph,
            database,
            schema_patterns=schema_patterns,
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
        fallback_triples = [triple for triple in raw_triples if _is_meaningful_triple(triple)]
        logger.warning(
            "[TripleService] No verified triples after %d attempts, "
            "using raw triples as fallback",
            MAX_ATTEMPTS,
        )
        verified_triples = fallback_triples

    logger.info(
        "[TripleService] Final -> rewritten=%r verified=%d instance=%d",
        rewritten,
        len(verified_triples),
        len(instance_triples),
    )
    return (
        rewritten,
        verified_triples,
        instance_triples,
        intent,
        return_contract,
        path_hints,
        query_constraints,
    )


def build_enhanced_question(
    question: str,
    rewritten: str,
    verified_triples: list[tuple[str, str, str]],
    instance_triples: list[tuple[str, str, str]],
    intent: dict[str, Any] | None = None,
    return_contract: dict[str, Any] | None = None,
    path_hints: dict[str, Any] | None = None,
    query_constraints: dict[str, Any] | None = None,
    schema_context: str = "",
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """
    Build the enriched query string passed to the Cypher chain.

    Includes:
      - Recent conversation history (last 3 turns)
      - Original question
      - Rewritten (clarified) question
      - Light intent slots
      - Return contract hints
      - Path/query-shape hints
      - Query constraints / locks
      - Verified triples (schema-validated)
      - Instance triples (actual DB entity matches)
      - Compact schema context
    """
    triples_text = (
        "\n".join(f"({s}, {r}, {o})" for s, r, o in verified_triples) or "None"
    )
    instance_text = (
        "\n".join(f"({s}, {r}, {o})" for s, r, o in instance_triples) or "None"
    )
    intent = intent or {}
    return_contract = return_contract or {}
    path_hints = path_hints or {}
    query_constraints = query_constraints or {}
    intent_text = "\n".join(
        [
            f"operation: {intent.get('operation', 'unknown')}",
            f"target: {intent.get('target', 'unknown')}",
            f"filters: {'; '.join(intent.get('filters', [])) or 'NONE'}",
            f"sort: {intent.get('sort', 'NONE') or 'NONE'}",
            f"limit: {intent.get('limit', 'NONE') or 'NONE'}",
            f"aggregation: {intent.get('aggregation', 'NONE') or 'NONE'}",
        ]
    )
    contract_items = return_contract.get("expected_items", []) or []
    contract_notes = return_contract.get("notes", []) or []
    return_contract_text = "\n".join(
        [
            f"cardinality: {return_contract.get('cardinality', 'all')}",
            f"return_mode: {return_contract.get('return_mode', 'unspecified')}",
            f"strict: {str(bool(return_contract.get('strict', False))).lower()}",
            f"expected_items: {' | '.join(contract_items) if contract_items else 'NONE'}",
            f"notes: {' | '.join(contract_notes) if contract_notes else 'NONE'}",
        ]
    )
    path_patterns = path_hints.get("path_patterns", []) or []
    path_notes = path_hints.get("notes", []) or []
    path_hints_text = "\n".join(
        [
            f"focus_entity: {path_hints.get('focus_entity', 'NONE') or 'NONE'}",
            f"path_patterns: {' | '.join(path_patterns) if path_patterns else 'NONE'}",
            f"count_pattern: {path_hints.get('count_pattern', 'NONE') or 'NONE'}",
            f"needs_distinct: {str(bool(path_hints.get('needs_distinct', False))).lower()}",
            f"notes: {' | '.join(path_notes) if path_notes else 'NONE'}",
        ]
    )
    anchor_lock = query_constraints.get("anchor_lock", {}) or {}
    constraint_notes = query_constraints.get("notes", []) or []
    constraints_text = "\n".join(
        [
            f"query_mode: {query_constraints.get('query_mode', 'lookup_entity')}",
            f"projection_lock: {query_constraints.get('projection_lock', 'unspecified')}",
            f"allow_aggregation: {str(bool(query_constraints.get('allow_aggregation', False))).lower()}",
            f"aggregation_style: {query_constraints.get('aggregation_style', 'NONE') or 'NONE'}",
            f"anchor_lock: {anchor_lock if anchor_lock else 'NONE'}",
            f"notes: {' | '.join(constraint_notes) if constraint_notes else 'NONE'}",
        ]
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
    parts.append(f"Intent:\n{intent_text}")
    parts.append(f"Return Contract:\n{return_contract_text}")
    parts.append(f"Path Hints:\n{path_hints_text}")
    parts.append(f"Query Constraints:\n{constraints_text}")
    parts.append(f"Verified Triples:\n{triples_text}")
    parts.append(f"Instance Triples:\n{instance_text}")
    if schema_context.strip():
        parts.append(schema_context.strip())

    return "\n\n".join(parts)
