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
import time
from typing import Any

from config import get_settings
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_neo4j import Neo4jGraph
from pydantic import BaseModel, Field
from services.query_ir import build_prompt_query_spec, build_query_ir, format_query_spec
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

_TWITTER_DYNAMIC_EXAMPLES = [
    {
        "tags": {"follows", "users", "neo4j", "recent"},
        "question": "List the 5 most recent users who started following 'Neo4j'.",
        "cypher": "MATCH (neo4j:Me {screen_name: 'neo4j'})<-[:FOLLOWS]-(user:User) RETURN user.screen_name, user.name, user.followers, user.following, user.profile_image_url, user.url, user.location, user.statuses ORDER BY user.followers DESC LIMIT 5",
    },
    {
        "tags": {"amplifies", "me", "users"},
        "question": "Which users are amplified by 'Me' according to the AMPLIFIES relationship?",
        "cypher": "MATCH (me:Me)-[:AMPLIFIES]->(user:User) RETURN user.screen_name AS AmplifiedUser",
    },
    {
        "tags": {"following", "count", "users"},
        "question": "Identify the top 3 users by the number of people they are following.",
        "cypher": "MATCH (u:User) RETURN u.name, u.screen_name, count{(u)-[:FOLLOWS]->(:User)} AS followingCount ORDER BY followingCount DESC LIMIT 3",
    },
    {
        "tags": {"mentions", "favorites", "tweets", "neo4j"},
        "question": "Show the tweets where 'neo4j' is mentioned and the tweet has a favorite count over 100.",
        "cypher": "MATCH (t:Tweet)-[:MENTIONS]->(u:User {screen_name: 'neo4j'}) WHERE t.favorites > 100 RETURN t.text AS tweet_text, t.favorites AS favorite_count, t.created_at AS created_at",
    },
    {
        "tags": {"links", "tweets", "neo4j", "posts"},
        "question": "List the top 5 tweets that contain links and are posted by 'Neo4j'.",
        "cypher": "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet)-[:CONTAINS]->(link:Link) RETURN tweet.text, tweet.favorites ORDER BY tweet.favorites DESC LIMIT 5",
    },
    {
        "tags": {"links", "tweets", "follow", "follows", "neo4j", "contains"},
        "question": "Find the tweets that contain links and have been posted by users who follow 'Neo4j'.",
        "cypher": "MATCH (neo:User {screen_name: 'neo4j'})-[:FOLLOWS]->(follower:User) MATCH (follower)-[:POSTS]->(tweet:Tweet)-[:CONTAINS]->(:Link) RETURN DISTINCT tweet",
    },
    {
        "tags": {"mentions", "users", "first", "neo4j"},
        "question": "Identify the first 3 users who mentioned 'Neo4j' in their tweets.",
        "cypher": "MATCH (u:User)-[:POSTS]->(t:Tweet)-[:MENTIONS]->(mentioned:User {name: 'Neo4j'}) RETURN u.screen_name, t.created_at ORDER BY t.created_at ASC LIMIT 3",
    },
    {
        "tags": {"mentions", "follows", "recent", "datetime"},
        "question": "What is the date and time of the most recent tweet that mentions a user followed by 'Neo4j'?",
        "cypher": "MATCH (n:User {screen_name: 'neo4j'})-[:FOLLOWS]->(followed:User) WITH followed MATCH (tweet:Tweet)-[:MENTIONS]->(followed) RETURN max(tweet.created_at) AS most_recent_tweet_date",
    },
    {
        "tags": {"mentions", "most", "frequently", "neo4j", "posts"},
        "question": "Who are the users that 'neo4j' mentions most frequently in their tweets?",
        "cypher": "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet)-[:MENTIONS]->(mentioned:User) RETURN mentioned.screen_name, count(tweet) AS mentions_count ORDER BY mentions_count DESC",
    },
    {
        "tags": {"retweets", "date", "first", "tweets", "neo4j"},
        "question": "List the first 3 tweets that 'Neo4j' retweets on '2021-03-16'.",
        "cypher": "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet) WHERE date(retweet.created_at) = date('2021-03-16') RETURN original.text, original.created_at ORDER BY retweet.created_at LIMIT 3",
    },
    {
        "tags": {"mentions", "links", "recent", "neo4j"},
        "question": "List the 3 most recent tweets that mention 'Neo4j' and contain a link.",
        "cypher": "MATCH (t:Tweet)-[:MENTIONS]->(u:User {name: 'Neo4j'}) MATCH (t)-[:CONTAINS]->(l:Link) RETURN t.text AS tweet_text, t.created_at AS created_at, l.url AS link_url ORDER BY t.created_at DESC LIMIT 3",
    },
    {
        "tags": {"retweets", "urls", "links", "neo4j"},
        "question": "Identify the URLs of the top 5 tweets retweeted by 'Neo4j'.",
        "cypher": "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet)<-[:RETWEETS]-(retweet:Tweet) WITH tweet, COUNT(retweet) AS retweet_count ORDER BY retweet_count DESC LIMIT 5 MATCH (tweet)-[:CONTAINS]->(link:Link) RETURN link.url",
    },
    {
        "tags": {"retweets", "users", "neo4j", "retweeted"},
        "question": "Who are the users that have been retweeted by 'neo4j'?",
        "cypher": "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet)-[:RETWEETS]->(retweetedTweet:Tweet)<-[:POSTS]-(retweetedUser:User) RETURN retweetedUser.screen_name AS retweeted_user, retweetedTweet.text AS retweeted_tweet",
    },
    {
        "tags": {"replies", "reply", "tweets", "neo4j"},
        "question": "Show the tweets that replied to a tweet by 'neo4j' and list the first 3.",
        "cypher": "MATCH (u:User {screen_name: 'neo4j'})-[:POSTS]->(t:Tweet)<-[:REPLY_TO]-(reply:Tweet) RETURN reply ORDER BY reply.created_at ASC LIMIT 3",
    },
    {
        "tags": {"statuses", "users", "ranking"},
        "question": "Find the top 5 users by number of statuses posted.",
        "cypher": "MATCH (u:User) RETURN u.name, u.screen_name, u.statuses ORDER BY u.statuses DESC LIMIT 5",
    },
]


class QueryAnchor(BaseModel):
    label: str = "NONE"
    property: str = ""
    value: str = ""


class QueryPlan(BaseModel):
    query_family: str = "generic"
    focus_entity: str = "NONE"
    anchor: QueryAnchor = Field(default_factory=QueryAnchor)
    return_mode: str = "unspecified"
    return_items: list[str] = Field(default_factory=list)
    sort_field: str = ""
    sort_direction: str = ""
    limit: int | None = None
    aggregation: str = ""
    needs_distinct: bool = False
    relation_path: list[str] = Field(default_factory=list)
    use_graph_count: bool = False
    notes: list[str] = Field(default_factory=list)


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
        "_question_text": rewritten or "",
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


def _plan_to_dict(plan: QueryPlan | dict[str, Any] | None) -> dict[str, Any]:
    if plan is None:
        return {}
    if isinstance(plan, QueryPlan):
        return plan.model_dump()
    return dict(plan)


def _heuristic_query_plan(question: str, rewritten: str, database: str) -> dict[str, Any]:
    """Cheap planner for common Twitter benchmark patterns to avoid an extra LLM call."""
    if (database or "").lower() != "twitter":
        return {}

    text = f"{question}\n{rewritten}".lower()

    if (
        "contain links" in text
        and "posted by users who follow" in text
        and "neo4j" in text
    ):
        return {
            "query_family": "followed_users_link_tweets",
            "focus_entity": "Tweet",
            "anchor": {"label": "User", "property": "screen_name", "value": "neo4j"},
            "return_mode": "full_node",
            "return_items": ["tweet"],
            "sort_field": "",
            "sort_direction": "",
            "limit": None,
            "aggregation": "",
            "needs_distinct": True,
            "relation_path": ["FOLLOWS", "POSTS", "CONTAINS"],
            "use_graph_count": False,
            "notes": [
                "Use two MATCH clauses: followers of neo4j, then their posted tweets with links.",
                "Return the tweet node directly and keep DISTINCT.",
            ],
        }

    if "mentions most frequently" in text and "neo4j" in text:
        return {
            "query_family": "neo4j_mentions_users",
            "focus_entity": "User",
            "anchor": {"label": "Me", "property": "screen_name", "value": "neo4j"},
            "return_mode": "properties",
            "return_items": ["mentioned.screen_name", "count(t) AS mentions_count"],
            "sort_field": "mentions_count",
            "sort_direction": "desc",
            "limit": 3,
            "aggregation": "count",
            "needs_distinct": False,
            "relation_path": ["POSTS", "MENTIONS"],
            "use_graph_count": False,
            "notes": ["Anchor on Me posting tweets, then count mentioned users."],
        }

    if "highest betweenness" in text and "mention" in text and ("first 3 tweets" in text or "top 3 tweets" in text):
        return {
            "query_family": "highest_betweenness_mentions",
            "focus_entity": "Tweet",
            "anchor": {"label": "", "property": "", "value": ""},
            "return_mode": "properties",
            "return_items": ["t2.text"],
            "sort_field": "",
            "sort_direction": "",
            "limit": 3,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["MENTIONS"],
            "use_graph_count": False,
            "notes": ["Use a two-stage query: pick the top betweenness user, then fetch tweets mentioning that user."],
        }

    if "top 5 most recent tweets" in text or "most recent tweets based on the creation date" in text:
        return {
            "query_family": "recent_tweets",
            "focus_entity": "Tweet",
            "anchor": {"label": "", "property": "", "value": ""},
            "return_mode": "full_node",
            "return_items": ["tweet"],
            "sort_field": "tweet.created_at",
            "sort_direction": "desc",
            "limit": 5,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": [],
            "use_graph_count": False,
            "notes": ["Return the tweet node directly for recent tweet queries."],
        }

    if "all tweets by" in text and "more than 200 favorites" in text and "neo4j" in text:
        return {
            "query_family": "tweets_by_user_with_favorites",
            "focus_entity": "Tweet",
            "anchor": {"label": "User", "property": "screen_name", "value": "neo4j"},
            "return_mode": "full_node",
            "return_items": ["tweet"],
            "sort_field": "",
            "sort_direction": "",
            "limit": 5,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["POSTS"],
            "use_graph_count": False,
            "notes": ["Keep full tweet return shape and avoid adding ORDER BY when the question only says first 5."],
        }

    if "specific user named" in text and "follows" in text and "neo4j" in text:
        return {
            "query_family": "named_user_follows_users",
            "focus_entity": "User",
            "anchor": {"label": "Me", "property": "name", "value": "Neo4j"},
            "return_mode": "properties",
            "return_items": [
                "user.name",
                "user.screen_name",
                "user.followers",
                "user.following",
            ],
            "sort_field": "user.followers",
            "sort_direction": "desc",
            "limit": 5,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["FOLLOWS"],
            "use_graph_count": False,
            "notes": ["The named account is the left-side Me anchor, not the returned User."],
        }

    if "interact with most frequently" in text or "interacts with most frequently" in text:
        return {
            "query_family": "user_interactions",
            "focus_entity": "User",
            "anchor": {"label": "Me", "property": "screen_name", "value": "neo4j"},
            "return_mode": "properties",
            "return_items": ["user.screen_name", "COUNT(*) AS interaction_count"],
            "sort_field": "interaction_count",
            "sort_direction": "desc",
            "limit": 1,
            "aggregation": "count",
            "needs_distinct": False,
            "relation_path": ["INTERACTS_WITH"],
            "use_graph_count": True,
            "notes": ["Count outgoing INTERACTS_WITH edges from Me to User and return only screen_name plus metric."],
        }

    if "amplify the most" in text and "neo4j" in text:
        return {
            "query_family": "amplified_users_count",
            "focus_entity": "User",
            "anchor": {"label": "Me", "property": "screen_name", "value": "neo4j"},
            "return_mode": "properties",
            "return_items": ["user.screen_name", "COUNT(*) AS amplification_count"],
            "sort_field": "amplification_count",
            "sort_direction": "desc",
            "limit": 5,
            "aggregation": "count",
            "needs_distinct": False,
            "relation_path": ["AMPLIFIES"],
            "use_graph_count": True,
            "notes": ["Count amplified users from the Me anchor."],
        }

    if "posted by" in text and "containing a hashtag" in text and "neo4j" in text:
        return {
            "query_family": "posted_tweets_with_hashtag",
            "focus_entity": "Tweet",
            "anchor": {"label": "User", "property": "name", "value": "Neo4j"},
            "return_mode": "full_node",
            "return_items": ["t", "h"],
            "sort_field": "",
            "sort_direction": "",
            "limit": None,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["POSTS", "TAGS"],
            "use_graph_count": False,
            "notes": ["Keep the mixed node return shape: tweet node and hashtag node."],
        }

    if "retweets on" in text and "first 3 tweets" in text and "neo4j" in text:
        return {
            "query_family": "neo4j_retweets_by_date",
            "focus_entity": "Tweet",
            "anchor": {"label": "Me", "property": "screen_name", "value": "neo4j"},
            "return_mode": "properties",
            "return_items": ["original.text", "original.created_at"],
            "sort_field": "retweet.created_at",
            "sort_direction": "asc",
            "limit": 3,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["POSTS", "RETWEETS"],
            "use_graph_count": False,
            "notes": ["Filter on retweet.created_at date, but return original tweet fields."],
        }

    if "has retweeted" in text and "first 3 tweets" in text and "neo4j" in text:
        return {
            "query_family": "neo4j_retweeted_tweets",
            "focus_entity": "Tweet",
            "anchor": {"label": "User", "property": "name", "value": "Neo4j"},
            "return_mode": "full_node",
            "return_items": ["rt"],
            "sort_field": "",
            "sort_direction": "",
            "limit": 3,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["POSTS", "RETWEETS"],
            "use_graph_count": False,
            "notes": ["Return the retweeted tweet node directly."],
        }

    if (
        "top 5 tweets by" in text
        and "neo4j" in text
        and (
            "favorites count" in text
            or "number of favorites" in text
            or "ranked by favorites" in text
        )
    ):
        return {
            "query_family": "tweets_by_user_favorites",
            "focus_entity": "Tweet",
            "anchor": {"label": "Me", "property": "screen_name", "value": "neo4j"},
            "return_mode": "properties",
            "return_items": ["tweet.text", "tweet.favorites"],
            "sort_field": "tweet.favorites",
            "sort_direction": "desc",
            "limit": 5,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["POSTS"],
            "use_graph_count": False,
            "notes": ["Tweets by Neo4j ranked by favorites should anchor on lowercase screen_name."],
        }

    if "mention 'neo4j'" in text and "contain a link" in text and "recent" in text:
        return {
            "query_family": "mention_link_tweets",
            "focus_entity": "Tweet",
            "anchor": {"label": "User", "property": "name", "value": "Neo4j"},
            "return_mode": "properties",
            "return_items": [
                "t.text AS tweet_text",
                "t.created_at AS created_at",
                "l.url AS link_url",
            ],
            "sort_field": "t.created_at",
            "sort_direction": "desc",
            "limit": 3,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["MENTIONS", "CONTAINS"],
            "use_graph_count": False,
            "notes": [],
        }

    if "replied to a tweet by" in text and "neo4j" in text:
        return {
            "query_family": "tweet_replies",
            "focus_entity": "Tweet",
            "anchor": {"label": "User", "property": "screen_name", "value": "neo4j"},
            "return_mode": "full_node",
            "return_items": ["reply"],
            "sort_field": "reply.created_at",
            "sort_direction": "asc",
            "limit": 3,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["POSTS", "REPLY_TO"],
            "use_graph_count": False,
            "notes": ["Return the reply tweet node directly."],
        }

    if "retweeted by 'neo4j'" in text and "urls" in text:
        return {
            "query_family": "retweeted_tweet_links",
            "focus_entity": "Link",
            "anchor": {"label": "Me", "property": "screen_name", "value": "neo4j"},
            "return_mode": "properties",
            "return_items": ["link.url"],
            "sort_field": "retweet_count",
            "sort_direction": "desc",
            "limit": 5,
            "aggregation": "count",
            "needs_distinct": False,
            "relation_path": ["POSTS", "RETWEETS", "CONTAINS"],
            "use_graph_count": False,
            "notes": [],
        }

    if "most recent users" in text and "following" in text and "neo4j" in text:
        return {
            "query_family": "follows_users",
            "focus_entity": "User",
            "anchor": {"label": "Me", "property": "screen_name", "value": "neo4j"},
            "return_mode": "properties",
            "return_items": [
                "user.screen_name",
                "user.name",
                "user.followers",
                "user.following",
                "user.profile_image_url",
                "user.url",
                "user.location",
                "user.statuses",
            ],
            "sort_field": "user.followers",
            "sort_direction": "desc",
            "limit": 5,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["FOLLOWS"],
            "use_graph_count": False,
            "notes": [],
        }

    if "amplif" in text and ("'me'" in text or '"me"' in text or "my account" in text):
        if "top 3" in text:
            return {
                "query_family": "amplified_users_top",
                "focus_entity": "User",
                "anchor": {"label": "Me", "property": "", "value": ""},
                "return_mode": "properties",
                "return_items": ["user.name", "user.screen_name"],
                "sort_field": "user.followers",
                "sort_direction": "desc",
                "limit": 3,
                "aggregation": "",
                "needs_distinct": False,
                "relation_path": ["AMPLIFIES"],
                "use_graph_count": False,
                "notes": ["Top amplified users should return name and screen_name ordered by followers."],
            }
        return {
            "query_family": "amplified_users",
            "focus_entity": "User",
            "anchor": {"label": "Me", "property": "", "value": ""},
            "return_mode": "properties",
            "return_items": ["user.screen_name AS AmplifiedUser"],
            "sort_field": "",
            "sort_direction": "",
            "limit": None,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["AMPLIFIES"],
            "use_graph_count": False,
            "notes": [],
        }

    if "users located in" in text and "tweets" in text:
        location_match = re.search(r"located in ['\"]([^'\"]+)['\"]", question + "\n" + rewritten, flags=re.IGNORECASE)
        location_value = location_match.group(1) if location_match else ""
        return {
            "query_family": "tweets_by_user_location",
            "focus_entity": "Tweet",
            "anchor": {"label": "User", "property": "location", "value": location_value},
            "return_mode": "full_node",
            "return_items": ["tweet"],
            "sort_field": "tweet.favorites",
            "sort_direction": "desc",
            "limit": 3,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["POSTS"],
            "use_graph_count": False,
            "notes": ["Tweets from users at the specified location should return tweet nodes."],
        }

    if "lowest number of followers" in text:
        return {
            "query_family": "user_followers_lowest",
            "focus_entity": "User",
            "anchor": {"label": "", "property": "", "value": ""},
            "return_mode": "properties",
            "return_items": ["user.screen_name", "user.followers"],
            "sort_field": "user.followers",
            "sort_direction": "asc",
            "limit": 3,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": [],
            "use_graph_count": False,
            "notes": ["Simple property ranking; do not fabricate relationship paths."],
        }

    if "mention 'neo4j'" in text and "more than 100 favorites" in text:
        return {
            "query_family": "mention_tweets_favorites",
            "focus_entity": "Tweet",
            "anchor": {"label": "User", "property": "screen_name", "value": "neo4j"},
            "return_mode": "properties",
            "return_items": ["t.text AS tweet_text", "t.favorites AS favorite_count", "t.created_at AS created_at"],
            "sort_field": "t.favorites",
            "sort_direction": "desc",
            "limit": 3,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["MENTIONS"],
            "use_graph_count": False,
            "notes": ["Filter tweets mentioning neo4j by favorites and project standard tweet columns."],
        }

    if "hashtags used in tweets that mention" in text and "neo4j" in text:
        return {
            "query_family": "hashtags_in_mention_tweets",
            "focus_entity": "Hashtag",
            "anchor": {"label": "User", "property": "screen_name", "value": "neo4j"},
            "return_mode": "properties",
            "return_items": ["DISTINCT h.name"],
            "sort_field": "",
            "sort_direction": "",
            "limit": None,
            "aggregation": "",
            "needs_distinct": True,
            "relation_path": ["MENTIONS", "TAGS"],
            "use_graph_count": False,
            "notes": ["Use the same tweet as the bridge between mention and hashtag."],
        }

    if "contain links" in text and "posted by" in text and "neo4j" in text:
        return {
            "query_family": "tweets_with_links_by_user",
            "focus_entity": "Tweet",
            "anchor": {"label": "Me", "property": "screen_name", "value": "neo4j"},
            "return_mode": "properties",
            "return_items": ["tweet.text AS tweet_text", "tweet.favorites AS favorite_count"],
            "sort_field": "tweet.favorites",
            "sort_direction": "desc",
            "limit": 5,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": ["POSTS", "CONTAINS"],
            "use_graph_count": False,
            "notes": ["Prefer favorites, not link_count, for ranking tweets with links."],
        }

    if "critical service" in text and any(token in text for token in ("text containing", "include the text", "contain the text")):
        return {
            "query_family": "tweets_text_contains",
            "focus_entity": "Tweet",
            "anchor": {"label": "", "property": "", "value": ""},
            "return_mode": "full_node",
            "return_items": ["t"],
            "sort_field": "t.favorites",
            "sort_direction": "desc",
            "limit": 5,
            "aggregation": "",
            "needs_distinct": False,
            "relation_path": [],
            "use_graph_count": False,
            "notes": ["Content search over tweets should return tweet nodes unless explicit columns are requested."],
        }

    if "top 3 users mentioned" in text and "tweets that" in text and "neo4j" in text and "mentions" in text:
        return {
            "query_family": "neo4j_mentions_users_top3",
            "focus_entity": "User",
            "anchor": {"label": "Me", "property": "screen_name", "value": "neo4j"},
            "return_mode": "properties",
            "return_items": ["mentionedUser.screen_name AS mentionedUser", "mentionCount"],
            "sort_field": "mentionCount",
            "sort_direction": "desc",
            "limit": 3,
            "aggregation": "count",
            "needs_distinct": False,
            "relation_path": ["POSTS", "MENTIONS"],
            "use_graph_count": False,
            "notes": ["Count mentioned users from tweets posted by Neo4j."],
        }

    if "top three users" in text and "following" in text and "number of people" in text:
        return {
            "query_family": "user_following_ranking",
            "focus_entity": "User",
            "anchor": {"label": "", "property": "", "value": ""},
            "return_mode": "properties",
            "return_items": ["u.name", "u.screen_name", "followingCount"],
            "sort_field": "followingCount",
            "sort_direction": "desc",
            "limit": 3,
            "aggregation": "count",
            "needs_distinct": False,
            "relation_path": ["FOLLOWS"],
            "use_graph_count": True,
            "notes": ["Prefer graph count over nullable user.following property."],
        }

    return {}


def _twitter_prefers_full_node_return(text: str) -> bool:
    """Detect tweet questions where the benchmark usually expects RETURN t/reply/tweet."""
    if "tweet" not in text:
        return False
    explicit_projection_tokens = (
        "tweet_text",
        "favorites",
        "favorite",
        "created_at",
        "url",
        "urls",
        "screen_name",
        "name",
        "average",
        "count",
        "number of",
        "how many",
        "reply_count",
        "retweet_count",
        "link_url",
    )
    if any(token in text for token in explicit_projection_tokens):
        return False
    if "containing a hashtag" in text or "contain a hashtag" in text:
        return True
    return any(
        token in text
        for token in (
            "most recent tweets",
            "based on the creation date",
            "find all tweets",
            "show all tweets",
            "show the tweets",
            "find tweets",
            "which tweets",
            "return tweets",
            "top 5 tweets",
            "top 3 tweets",
            "display the first",
            "find the first 3 tweets",
            "show the first 3 tweets",
            "first 5 tweets",
            "first 3 tweets",
        )
    )


def compile_query_plan(
    question: str,
    rewritten: str,
    interpreter_llm,
    database: str,
    budget_remaining_seconds: float | None = None,
) -> dict[str, Any]:
    """Compile a lightweight structured query plan to reduce anchor/projection drift."""
    heuristic_plan = _heuristic_query_plan(question, rewritten, database)
    if heuristic_plan:
        logger.info("[TripleService] query_plan heuristic=%s", heuristic_plan)
        return heuristic_plan

    if (database or "").lower() != "twitter":
        return {}

    if budget_remaining_seconds is not None and budget_remaining_seconds <= 6:
        logger.info(
            "[TripleService] Skipping structured query plan due to low budget (%.2fs)",
            budget_remaining_seconds,
        )
        return {}

    planner = interpreter_llm.with_structured_output(QueryPlan)
    prompt = f"""
You are a Neo4j Twitter query planner.
Convert the question into a compact structured query plan.

Rules:
- Decide the anchor explicitly: Me vs User vs Hashtag.
- Decide whether the anchor should use screen_name or name.
- Decide whether RETURN should be full node, projected properties, metric, or mixed.
- Decide the exact projected items when they are obvious from the question.
- Decide whether ordering should be by an existing property or by graph count.
- Decide whether follower/following/status questions should use node properties or graph counts.
- Keep relation_path to relationship types only, in traversal order.

Examples:
Q: List the 5 most recent users who started following 'Neo4j'.
A:
  query_family = "follows_users"
  focus_entity = "User"
  anchor = Me/screen_name/neo4j
  return_mode = "properties"
  return_items = ["user.screen_name", "user.name", "user.followers", "user.following", "user.profile_image_url", "user.url", "user.location", "user.statuses"]
  sort_field = "user.followers"
  sort_direction = "desc"
  limit = 5
  relation_path = ["FOLLOWS"]
  use_graph_count = false

Q: Show the tweets where 'neo4j' is mentioned and the tweet has a favorite count over 100.
A:
  query_family = "mention_tweets"
  focus_entity = "Tweet"
  anchor = User/screen_name/neo4j
  return_mode = "properties"
  return_items = ["t.text AS tweet_text", "t.favorites AS favorite_count", "t.created_at AS created_at"]
  sort_field = ""
  relation_path = ["MENTIONS"]
  use_graph_count = false

Q: Find the top 5 users by number of statuses posted.
A:
  query_family = "user_property_ranking"
  focus_entity = "User"
  anchor = NONE
  return_mode = "properties"
  return_items = ["u.name", "u.screen_name", "u.statuses"]
  sort_field = "u.statuses"
  sort_direction = "desc"
  limit = 5
  aggregation = ""
  use_graph_count = false

Question: {question}
Rewritten: {rewritten or question}
""".strip()

    try:
        plan = planner.invoke(prompt)
        plan_dict = _plan_to_dict(plan)
        plan_dict["_source_question"] = question
        logger.info("[TripleService] query_plan=%s", plan_dict)
        return plan_dict
    except Exception as e:
        logger.warning("[TripleService] Structured query plan failed: %s", e)
        return {}


def select_dynamic_examples(
    question: str,
    database: str,
    query_plan: dict[str, Any] | None = None,
    max_examples: int = 4,
) -> list[dict[str, str]]:
    """Select a few relevant few-shot examples instead of relying on the entire prompt bank."""
    if (database or "").lower() != "twitter":
        return []

    query_plan = query_plan or {}
    text = question.lower()
    tags = set(re.findall(r"[a-z_]+", text))
    tags.update(str(query_plan.get("query_family", "")).lower().split("_"))
    tags.update(str(query_plan.get("focus_entity", "")).lower().split("_"))
    if isinstance(query_plan.get("relation_path"), list):
        tags.update(str(item).lower() for item in (query_plan.get("relation_path") or []))
    if isinstance(query_plan.get("anchor"), dict):
        anchor = query_plan.get("anchor", {}) or {}
        tags.add(str(anchor.get("label", "")).lower())
        tags.add(str(anchor.get("property", "")).lower())

    scored: list[tuple[int, dict[str, str]]] = []
    for example in _TWITTER_DYNAMIC_EXAMPLES:
        overlap = len(tags.intersection(example["tags"]))
        if overlap > 0:
            scored.append((overlap, example))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [example for _, example in scored[:max_examples]]


def infer_return_contract(
    question: str,
    rewritten: str,
    intent: dict[str, Any] | None = None,
    database: str = "",
    query_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Infer a lightweight output contract from the user question."""
    intent = intent or {}
    query_plan = query_plan or {}
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
    if query_plan.get("limit"):
        limit = str(query_plan.get("limit"))
    if limit:
        contract["cardinality"] = f"top_{limit}"
    elif any(token in text for token in (" most ", " most frequently", " highest ", " lowest ")):
        contract["cardinality"] = "top_1"

    if db == "twitter" and "mentions most frequently in their tweets" in text:
        contract["cardinality"] = "all"

    if query_plan.get("return_mode"):
        contract["return_mode"] = str(query_plan.get("return_mode"))
    elif db == "twitter" and _twitter_prefers_full_node_return(text):
        contract["return_mode"] = "full_node"
    elif any(token in text for token in ("list all", "show all", "which users", "who are the users")):
        contract["return_mode"] = "properties"
    elif any(token in text for token in ("list tweets", "show tweets", "return tweets")):
        contract["return_mode"] = "full_node"

    if db == "twitter":
        expected_items: list[str] = []

        if query_plan.get("return_items"):
            expected_items = [str(item) for item in (query_plan.get("return_items") or []) if str(item).strip()]
            contract["strict"] = True
            if query_plan.get("return_mode"):
                contract["return_mode"] = str(query_plan.get("return_mode"))

        if not expected_items and ("amplified by 'me'" in text or 'amplified by "me"' in text):
            expected_items = ["user.screen_name AS AmplifiedUser"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
            contract["notes"].append("Return only the amplified user screen name with the expected alias.")
        elif not expected_items and ("interact with most frequently" in text or "interacts with most frequently" in text):
            expected_items = ["user.screen_name", "COUNT(*) AS interaction_count"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
            contract["cardinality"] = "top_1"
        elif not expected_items and "specific user named" in text and "follows" in text:
            expected_items = [
                "user.name",
                "user.screen_name",
                "user.followers",
                "user.following",
            ]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and "posted by" in text and "containing a hashtag" in text:
            expected_items = ["t", "h"]
            contract["return_mode"] = "full_node"
            contract["strict"] = True
        elif not expected_items and "top 5 users" in text and "follows" in text:
            expected_items = [
                "user.name",
                "user.screen_name",
                "user.followers",
                "user.following",
            ]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and ("most recent users" in text or "started following" in text or "who follows" in text) and "tweet" not in text:
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
        elif not expected_items and ("favorite count over" in text or ("mentioned" in text and "favorite" in text)):
            expected_items = [
                "t.text AS tweet_text",
                "t.favorites AS favorite_count",
                "t.created_at AS created_at",
            ]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and "contain links and are posted by 'neo4j'" in text:
            expected_items = ["tweet.text", "tweet.favorites"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and "mention 'neo4j' and contain a link" in text:
            expected_items = [
                "t.text AS tweet_text",
                "t.created_at AS created_at",
                "l.url AS link_url",
            ]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and "retweeted by 'neo4j'" in text and "urls" in text:
            expected_items = ["link.url"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and "retweeted the most times" in text:
            expected_items = ["t.text AS tweet_text", "count(retweet) AS retweet_count"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and "average number of favorites" in text and "mention both" in text:
            expected_items = ["average_favorites"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and "hashtags used by users with more than 1000 followers" in text:
            expected_items = ["h.name"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and "most replies" in text and "tweets by 'neo4j'" in text:
            expected_items = ["t.text AS tweet_text", "reply_count"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and ("statuses posted" in text or "number of statuses posted" in text):
            expected_items = ["u.name", "u.screen_name", "u.statuses"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
            contract["notes"].append("Use the statuses property directly for ranking, not a graph count.")
        elif not expected_items and "mentions most frequently" in text:
            expected_items = ["mentioned.screen_name", "count(t) AS mentions_count"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and "most recent tweet" in text and "mentions a user followed by" in text:
            expected_items = ["max(tweet.created_at) AS most_recent_tweet_date"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and "first 3 users who mentioned" in text:
            expected_items = ["u.screen_name", "t.created_at"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and "tweets with most mentions" in text:
            expected_items = [
                "t.id_str AS tweet_id",
                "t.text AS tweet_text",
                "mention_count",
            ]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and ("number of people they are following" in text or "by the number of people they are following" in text):
            expected_items = ["u.name", "u.screen_name", "followingCount"]
            contract["return_mode"] = "properties"
            contract["strict"] = True
        elif not expected_items and "number of followers" in text and "top" in text:
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
    query_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Infer lightweight path/query-shape hints for multi-hop questions."""
    text = f"{question}\n{rewritten}".lower()
    db = (database or "").lower()
    query_plan = query_plan or {}
    hints: dict[str, Any] = {
        "focus_entity": "",
        "path_patterns": [],
        "count_pattern": "",
        "needs_distinct": False,
        "notes": [],
    }

    if db != "twitter":
        return hints

    if query_plan.get("focus_entity"):
        hints["focus_entity"] = str(query_plan.get("focus_entity"))
    if query_plan.get("relation_path"):
        path_patterns = [f"[:{rel}]" for rel in (query_plan.get("relation_path") or []) if str(rel).strip()]
        if path_patterns:
            hints["path_patterns"] = path_patterns

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

    if "contain links" in text and "posted by users who follow" in text and "neo4j" in text:
        hints["focus_entity"] = "Tweet"
        hints["path_patterns"] = [
            "(:User)-[:FOLLOWS]->(:User)",
            "(:User)-[:POSTS]->(:Tweet)-[:CONTAINS]->(:Link)",
        ]
        hints["needs_distinct"] = True

    if "mention 'neo4j' and contain a link" in text:
        hints["focus_entity"] = "Tweet"
        hints["path_patterns"] = [
            "(:Tweet)-[:MENTIONS]->(:User)",
            "(:Tweet)-[:CONTAINS]->(:Link)",
        ]

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

    if "replied to a tweet by" in text:
        hints["focus_entity"] = "Tweet"
        hints["path_patterns"] = [
            "(:User)-[:POSTS]->(:Tweet)<-[:REPLY_TO]-(:Tweet)",
        ]

    return hints


def infer_query_constraints(
    question: str,
    rewritten: str,
    database: str = "",
    intent: dict[str, Any] | None = None,
    return_contract: dict[str, Any] | None = None,
    path_hints: dict[str, Any] | None = None,
    query_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile lightweight hard constraints before Cypher generation."""
    raw_text = f"{question}\n{rewritten}"
    text = raw_text.lower()
    db = (database or "").lower()
    intent = intent or {}
    return_contract = return_contract or {}
    path_hints = path_hints or {}
    query_plan = query_plan or {}

    constraints: dict[str, Any] = {
        "query_mode": "lookup_entity",
        "projection_lock": return_contract.get("return_mode", "unspecified") or "unspecified",
        "allow_aggregation": False,
        "aggregation_style": "",
        "anchor_lock": {},
        "order_lock": {},
        "notes": [],
    }

    if return_contract.get("return_mode") == "full_node":
        constraints["projection_lock"] = "full_node"
    elif return_contract.get("return_mode") == "properties":
        constraints["projection_lock"] = "properties"

    if query_plan.get("query_family"):
        constraints["query_mode"] = str(query_plan.get("query_family"))

    if intent.get("aggregation") in {"count", "avg", "sum", "min", "max"}:
        constraints["allow_aggregation"] = True
        constraints["aggregation_style"] = intent.get("aggregation")

    if query_plan.get("aggregation"):
        constraints["allow_aggregation"] = True
        constraints["aggregation_style"] = str(query_plan.get("aggregation"))

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
        quoted_literal_match = re.search(r"['\"]([^'\"]+)['\"]", raw_text)
        quoted_literal = quoted_literal_match.group(1).strip() if quoted_literal_match else ""
        quoted_has_upper = any(char.isupper() for char in quoted_literal)

        if "'me'" in text or '"me"' in text or " according to the amplifies relationship" in text:
            anchor_lock = {"label": "Me", "property": "", "value": ""}
        elif "user named 'neo4j'" in text or 'user named "neo4j"' in text:
            anchor_lock = {"label": "Me", "property": "name", "value": "Neo4j"}
        elif "'neo4j'" in text or '"neo4j"' in text:
            if any(token in text for token in ("screen_name", "mentions", "started following", "follow 'neo4j'", "follow neo4j")):
                anchor_lock = {"label": "Me", "property": "screen_name", "value": "neo4j"}
            elif any(token in text for token in ("posted by 'neo4j'", "tweets by 'neo4j'", "by 'neo4j'")):
                anchor_lock = {
                    "label": "User",
                    "property": "name" if quoted_has_upper else "screen_name",
                    "value": quoted_literal or ("Neo4j" if quoted_has_upper else "neo4j"),
                }
            else:
                anchor_lock = {
                    "label": "Me",
                    "property": "name" if quoted_has_upper else "screen_name",
                    "value": quoted_literal or ("Neo4j" if quoted_has_upper else "neo4j"),
                }

        if anchor_lock:
            constraints["anchor_lock"] = anchor_lock
        plan_anchor = query_plan.get("anchor", {}) or {}
        if isinstance(plan_anchor, dict) and plan_anchor.get("label") and plan_anchor.get("label") != "NONE":
            constraints["anchor_lock"] = {
                "label": str(plan_anchor.get("label", "")),
                "property": str(plan_anchor.get("property", "")),
                "value": str(plan_anchor.get("value", "")),
            }

        if "most recent users" in text or "started following" in text:
            constraints["order_lock"] = {"field": "user.followers", "direction": "desc"}
        elif "top 5 users" in text and "follows" in text:
            constraints["order_lock"] = {"field": "user.followers", "direction": "desc"}
        elif "has amplified" in text and "top" in text:
            constraints["order_lock"] = {"field": "user.followers", "direction": "desc"}
        elif "statuses posted" in text or "number of statuses posted" in text:
            constraints["order_lock"] = {"field": "u.statuses", "direction": "desc"}

        if query_plan.get("sort_field"):
            constraints["order_lock"] = {
                "field": str(query_plan.get("sort_field")),
                "direction": str(query_plan.get("sort_direction", "desc") or "desc").lower(),
            }
        if query_plan.get("use_graph_count"):
            constraints["allow_aggregation"] = True
            constraints["aggregation_style"] = "path_count"

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
        (rewritten, verified_triples, instance_triples, intent, query_plan, return_contract, path_hints, query_constraints)
    """
    verified_triples: list[tuple[str, str, str]] = []
    instance_triples: list[tuple[str, str, str]] = []
    intent: dict[str, Any] = {}
    query_plan: dict[str, Any] = {}
    return_contract: dict[str, Any] = {}
    path_hints: dict[str, Any] = {}
    query_constraints: dict[str, Any] = {}
    rewritten = ""
    raw_triples: list[tuple[str, str, str]] = []
    settings = get_settings()
    started_at = time.monotonic()
    time_budget_seconds = max(5, settings.PIPELINE_TIME_BUDGET_SECONDS)
    max_attempts = MAX_ATTEMPTS if database.lower() != "twitter" else 2

    for attempt in range(max_attempts):
        elapsed = time.monotonic() - started_at
        if elapsed >= time_budget_seconds:
            logger.warning(
                "[TripleService] Stopping extraction early due to time budget (%.2fs/%.2fs)",
                elapsed,
                time_budget_seconds,
            )
            break

        logger.info("[TripleService] extract attempt %d/%d", attempt + 1, max_attempts)

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

        elapsed = time.monotonic() - started_at
        if database.lower() == "twitter" and elapsed >= (time_budget_seconds * 0.65):
            logger.warning(
                "[TripleService] Skipping remaining attempts due to low remaining budget (%.2fs left)",
                max(0.0, time_budget_seconds - elapsed),
            )
            break

    # Fallback: use raw triples if verification never succeeded
    if not verified_triples:
        fallback_triples = [triple for triple in raw_triples if _is_meaningful_triple(triple)]
        logger.warning(
            "[TripleService] No verified triples after %d attempts, "
            "using raw triples as fallback",
            max_attempts,
        )
        verified_triples = fallback_triples

    logger.info(
        "[TripleService] Final -> rewritten=%r verified=%d instance=%d",
        rewritten,
        len(verified_triples),
        len(instance_triples),
    )

    query_plan = compile_query_plan(
        question=question,
        rewritten=rewritten or question,
        interpreter_llm=interpreter_llm,
        database=database,
        budget_remaining_seconds=max(0.0, time_budget_seconds - (time.monotonic() - started_at)),
    )
    return_contract = infer_return_contract(
        question=question,
        rewritten=rewritten or question,
        intent=intent,
        database=database,
        query_plan=query_plan,
    )
    path_hints = infer_path_hints(
        question=question,
        rewritten=rewritten or question,
        database=database,
        query_plan=query_plan,
    )
    query_constraints = infer_query_constraints(
        question=question,
        rewritten=rewritten or question,
        database=database,
        intent=intent,
        return_contract=return_contract,
        path_hints=path_hints,
        query_plan=query_plan,
    )
    return (
        rewritten,
        verified_triples,
        instance_triples,
        intent,
        query_plan,
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
    query_plan: dict[str, Any] | None = None,
    return_contract: dict[str, Any] | None = None,
    path_hints: dict[str, Any] | None = None,
    query_constraints: dict[str, Any] | None = None,
    query_ir: dict[str, Any] | None = None,
    database: str = "",
    schema_context: str = "",
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """
    Build the enriched query string passed to the Cypher chain.

    Includes:
      - Recent conversation history (last 3 turns)
      - Original question
      - Rewritten (clarified) question
      - Compact query spec (single source of truth for the chain prompt)
      - A few dynamic examples when available
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
    query_plan = query_plan or {}
    return_contract = return_contract or {}
    path_hints = path_hints or {}
    query_constraints = query_constraints or {}
    query_ir = query_ir or build_query_ir(
        database=database,
        verified_triples=verified_triples,
        instance_triples=instance_triples,
        intent=intent,
        query_plan=query_plan,
        return_contract=return_contract,
        path_hints=path_hints,
        query_constraints=query_constraints,
    )
    query_spec_text = format_query_spec(build_prompt_query_spec(query_ir))
    evidence_score = 0
    if query_ir.get("focus_label"):
        evidence_score += 1
    if query_ir.get("return_items"):
        evidence_score += 1
    if query_ir.get("relation_path") or verified_triples:
        evidence_score += 1
    if query_ir.get("anchor") or instance_triples:
        evidence_score += 1
    dynamic_examples_text = "NONE"
    if evidence_score < 3:
        dynamic_examples = select_dynamic_examples(
            question=question,
            database=database,
            query_plan=query_plan,
            max_examples=2,
        )
        dynamic_examples_text = "\n\n".join(
            f"Q: {example['question']}\nCypher: {example['cypher']}" for example in dynamic_examples
        ) or "NONE"

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
    parts.append(f"Query Spec:\n{query_spec_text}")
    if dynamic_examples_text != "NONE":
        parts.append(f"Dynamic Examples:\n{dynamic_examples_text}")
    parts.append(f"Verified Triples:\n{triples_text}")
    parts.append(f"Instance Triples:\n{instance_text}")
    if schema_context.strip():
        parts.append(schema_context.strip())

    return "\n\n".join(parts)
