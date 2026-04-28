"""Prompt sections for Cypher generation."""

from __future__ import annotations

import json
import os
import re

from config import get_settings
from templates.entity_definitions import get_entity_definitions
from templates.semantic_schema import build_semantic_plan_text

_DOMAIN_CONFIGS_PATH = os.path.join(os.path.dirname(__file__), "domain_configs.json")
with open(_DOMAIN_CONFIGS_PATH, "r", encoding="utf-8") as file:
    _DOMAIN_CONFIGS = json.load(file)

_DOMAIN_NAMES = {
    "climate": "Climate Science",
    "movies": "Movies",
    "recommendations": "Movie Recommendations",
    "northwind": "Northwind",
    "twitter": "Twitter",
}

_DEFAULT_TOP_K = 5

_SHARED_CORE_RULES = """
- Output only one raw Cypher query. No markdown, no explanations.
- Use only labels, relationships, properties, and directions that appear in the schema or selected examples.
- Treat the schema as the source of truth for graph structure.
- Use the semantic graph plan to reason through node labels, relationship directions, and traversal paths before writing Cypher.
- Use domain facts for semantic meaning, domain hints for relationship guidance, and examples for query shape.
- Prefer the selected example with the closest intent and adapt its structure instead of inventing a new structure.
- Preserve relationship direction from the schema/examples. Do not reverse a path because the English sentence is passive.
- Reuse the same variable when the same entity must satisfy multiple conditions.
- Put filters before aggregation/ranking unless the question asks to filter after ranking.
- For top/highest/most, compute or select the ranking metric, ORDER BY it DESC, then LIMIT.
- For lowest/least/fewest, ORDER BY the ranking metric ASC, then LIMIT.
- For first N without a ranking word, use LIMIT N and do not invent ORDER BY.
- If "first N" appears with a ranking phrase such as highest/lowest/most/fewest, honor the ranking phrase and ORDER BY the metric.
- For count/average/sum/min/max questions, return the computed metric alias.
- Return exactly what the question asks for. Do not add helper columns.
- The selected example's RETURN clause is a contract. For a close match, preserve the same number of return columns and aliases unless the user explicitly asks for different fields.
- Return full nodes only when the question asks for entities and does not ask for specific properties or metrics.
- Use DISTINCT only when the question asks for unique results or the join path can duplicate rows.
- Use exact equality for quoted entity literals unless the question asks for text containment/search.
- Before writing Cypher, decide these five contracts: anchor entity, path direction, filters, ranking/limit, and exact RETURN projection.
- Never invent relationship properties or inline variables inside a relationship pattern; bind a relationship variable when you need a relationship property.
""".strip()

_TOKEN_SYNONYMS = {
    "newest": "recent",
    "latest": "recent",
    "recently": "recent",
    "started": "start",
    "starting": "start",
    "followed": "follow",
    "following": "follow",
    "follows": "follow",
    "followers": "follower",
    "mentions": "mention",
    "mentioned": "mention",
    "retweets": "retweet",
    "retweeted": "retweet",
    "retweeting": "retweet",
    "amplified": "amplify",
    "amplifies": "amplify",
    "amplifying": "amplify",
    "interacts": "interact",
    "interaction": "interact",
    "interactions": "interact",
    "favorites": "favorite",
    "favourites": "favorite",
    "statuses": "status",
    "tweets": "tweet",
    "users": "user",
    "hashtags": "hashtag",
    "tagged": "tag",
    "tags": "tag",
    "links": "link",
    "urls": "url",
    "replies": "reply",
    "replied": "reply",
    "movies": "movie",
    "directors": "director",
    "actors": "actor",
    "products": "product",
    "suppliers": "supplier",
    "customers": "customer",
    "orders": "order",
    "variables": "variable",
    "models": "model",
}

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "based",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "list",
    "of",
    "on",
    "or",
    "show",
    "the",
    "their",
    "them",
    "to",
    "what",
    "which",
    "who",
    "with",
}


def _escape_prompt_block(text: str) -> str:
    return text.strip().replace("{", "{{").replace("}", "}}")


def _normalize_token(token: str) -> str:
    return _TOKEN_SYNONYMS.get(token.lower().strip("_"), token.lower().strip("_"))


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]*|\d+", text)
    return [
        normalized
        for token in tokens
        if (normalized := _normalize_token(token)) and normalized not in _STOPWORDS
    ]


def _cypher_signature(cypher: str) -> list[str]:
    labels = [f"label:{label.lower()}" for label in re.findall(r":([A-Za-z][A-Za-z0-9_]*)", cypher)]
    rels = [f"rel:{rel.lower()}" for rel in re.findall(r"\[:([A-Za-z][A-Za-z0-9_]*)\]", cypher)]
    props = [f"prop:{prop.lower()}" for prop in re.findall(r"\.([A-Za-z][A-Za-z0-9_]*)", cypher)]
    aliases = [
        f"alias:{alias.lower()}"
        for alias in re.findall(r"\bAS\s+([A-Za-z][A-Za-z0-9_]*)", cypher, re.IGNORECASE)
    ]

    structural: list[str] = []
    upper = cypher.upper()
    if "ORDER BY" in upper:
        structural.append("order_by")
    if " DESC" in upper:
        structural.append("desc")
    if " ASC" in upper:
        structural.append("asc")
    if "LIMIT" in upper:
        structural.append("limit")
    if "COUNT" in upper or "count{" in cypher:
        structural.append("aggregate_count")
    if "<-[:" in cypher:
        structural.append("incoming_relationship")
    if "]->" in cypher:
        structural.append("outgoing_relationship")
    return labels + rels + props + aliases + structural


def _build_retrieval_signature(question: str, cypher: str = "") -> str:
    tokens = _tokenize(question)
    quoted_entities = [
        f"entity:{entity.lower()}"
        for match in re.findall(r"'([^']+)'|\"([^\"]+)\"", question)
        for entity in match
        if entity
    ]
    numbers = [f"limit:{number}" for number in re.findall(r"\b\d+\b", question)]
    lowered = question.lower()
    intent_tokens: list[str] = []

    if re.search(r"\b(top|highest|largest|greatest)\b|\bmost\b(?!\s+recent)", lowered):
        intent_tokens.extend(["ranking", "desc"])
    if re.search(r"\b(lowest|least|smallest|oldest)\b", lowered):
        intent_tokens.extend(["ranking", "asc"])
    if re.search(r"\b(recent|newest|latest|most recent)\b", lowered):
        intent_tokens.extend(["recent", "order_by"])
    if re.search(r"\b(first)\b", lowered):
        intent_tokens.append("limit")

    concept_patterns = {
        r"\b(follow|follower|following|started following)\b": ["rel:follows"],
        r"\b(amplify|amplifies|amplified|amplifying)\b": ["rel:amplifies"],
        r"\b(interact|interacts|interaction|interactions)\b": ["rel:interacts_with"],
        r"\b(retweet|retweets|retweeted|retweeting)\b": ["rel:retweets"],
        r"\b(mention|mentions|mentioned)\b": ["rel:mentions"],
        r"\b(hashtag|hashtags|tag|tags|tagged)\b": ["label:hashtag", "rel:tags"],
        r"\b(link|links|url|urls)\b": ["label:link", "rel:contains", "prop:url"],
        r"\b(reply|replies|replied)\b": ["rel:reply_to"],
        r"\b(similar|similarity)\b": ["rel:similar_to", "prop:score"],
        r"\b(betweenness)\b": ["prop:betweenness"],
        r"\b(location|located)\b": ["prop:location"],
        r"\b(tweet|tweets)\b": ["label:tweet"],
        r"\b(user|users|account|accounts)\b": ["label:user"],
        r"\b(neo4j|me)\b": ["label:me", "entity:neo4j"],
    }
    for pattern, concepts in concept_patterns.items():
        if re.search(pattern, lowered):
            intent_tokens.extend(concepts)

    parts = tokens + quoted_entities + numbers + intent_tokens
    if cypher:
        parts.extend(_cypher_signature(cypher))

    seen: set[str] = set()
    return " ".join(part for part in parts if not (part in seen or seen.add(part)))


def _lexical_similarity(query: str, candidate: str) -> float:
    query_tokens = set(query.split())
    candidate_tokens = set(candidate.split())
    if not query_tokens or not candidate_tokens:
        return 0.0
    return len(query_tokens & candidate_tokens) / len(query_tokens | candidate_tokens)


def _format_hints(hints: list[str]) -> str:
    return "\n".join(f"- {hint}" for hint in hints)


def _format_examples(examples: list[dict[str, str]]) -> str:
    return "\n\n".join(
        f"[candidate score={example.get('score', 0):.3f}]\n"
        f"Q: {example['question']}\n"
        f"{example['cypher']}"
        for example in examples
    )


def _number_limit(question: str) -> str | None:
    lowered = question.lower()
    word_numbers = {
        "one": "1",
        "two": "2",
        "three": "3",
        "four": "4",
        "five": "5",
        "six": "6",
        "seven": "7",
        "eight": "8",
        "nine": "9",
        "ten": "10",
    }
    match = re.search(r"\b(?:top|first|last|limit|show|list|find|identify|which)\s+(?:the\s+)?(\d+)\b", lowered)
    if match:
        return match.group(1)
    for word, number in word_numbers.items():
        if re.search(rf"\b(?:top|first|last|which)\s+{word}\b", lowered):
            return number
    return None


def _build_query_hints(question: str) -> str:
    lowered = question.lower()
    hints: list[str] = []
    limit = _number_limit(question)

    if limit:
        hints.append(f"LIMIT contract: use LIMIT {limit}.")

    if re.search(r"\b(first)\b", lowered) and not re.search(
        r"\b(top|highest|most|latest|newest|recent|lowest|least|oldest|earliest)\b",
        lowered,
    ):
        hints.append("ORDER contract: 'first N' alone means LIMIT only; do not invent ORDER BY.")
    if re.search(r"\b(top|highest|largest|greatest)\b|\bmost\b(?!\s+recent)", lowered):
        hints.append("ORDER contract: rank by the metric named in the question and ORDER BY that metric DESC before LIMIT.")
    if re.search(r"\b(latest|newest|most recent|recent)\b", lowered):
        hints.append("ORDER contract: use the date/time property only when the question is about recency; otherwise do not use created_at as a default.")
    if re.search(r"\b(lowest|least|smallest|oldest|earliest)\b", lowered):
        hints.append("ORDER contract: use ASC for lowest/least/oldest/earliest.")

    property_returns = []
    return_terms = {
        "screen_name": r"\b(screen names?|handles?)\b",
        "name": r"\b(names?)\b",
        "title": r"\b(titles?)\b",
        "text": r"\b(tweet text|text)\b",
        "favorites": r"\b(favorites?|favorite count)\b",
        "created_at": r"\b(created|creation date|date|time)\b",
        "url": r"\b(urls?|links?)\b",
        "location": r"\b(locations?)\b",
        "profile_image_url": r"\b(profile image|image url)\b",
        "id_str": r"\b(tweet id|identifier|id_str)\b",
        "betweenness": r"\bbetweenness\b",
        "statuses": r"\bstatuses\b",
        "followers": r"\bfollowers\b",
        "following": r"\b(following count|number of people they are following|people they are following)\b",
        "roles": r"\broles?\b",
        "rating": r"\bratings?\b",
        "summary": r"\bsummary\b",
        "votes": r"\bvotes?\b",
        "tagline": r"\btaglines?\b",
        "released": r"\breleased|release year|year\b",
        "budget": r"\bbudget\b",
        "revenue": r"\brevenue|box office\b",
        "imdbRating": r"\bimdb ?rating|top rated\b",
        "runtime": r"\bruntime\b",
        "languages": r"\blanguages?\b",
        "countries": r"\bcountries|country\b",
        "unitPrice": r"\bunit ?price\b",
        "quantity": r"\bquantity|quantities\b",
        "discount": r"\bdiscount\b",
        "customerID": r"\bcustomer ?id|customerID\b",
        "productName": r"\bproduct ?name|productName\b",
        "companyName": r"\bcompany ?name|companyName\b",
        "contactName": r"\bcontact ?name|contactName\b",
        "shipCity": r"\bship ?city|shipCity\b",
    }
    for prop, pattern in return_terms.items():
        if re.search(pattern, lowered):
            if prop == "name" and re.search(r"\bscreen names?\b", lowered):
                continue
            if prop == "url" and re.search(r"\bprofile image url\b|\bimage url\b", lowered):
                continue
            property_returns.append(prop)
    if property_returns:
        hints.append(
            "RETURN contract: project only the requested properties/metrics when possible: "
            + ", ".join(dict.fromkeys(property_returns))
            + "."
        )
    elif re.search(r"\b(tweets?|users?|hashtags?|links?)\b", lowered):
        hints.append("RETURN contract: if no specific properties are requested, return the matched entity node from the selected example.")

    stored_metric_number = re.search(
        r"\bnumber of\s+(favorites?|followers?|following|statuses?|votes?)\b",
        lowered,
    )
    count_question = (
        re.search(r"\b(how many|most frequently|frequency|number of)\b", lowered)
        and not stored_metric_number
    ) or (
        re.search(r"\bcount\b", lowered)
        and not re.search(r"\b(favorite|favorites|follower|followers|following|statuses?)\s+count\b", lowered)
    )
    if count_question:
        hints.append("AGGREGATION contract: use COUNT/count{} only for counted relationships/entities and return the count alias.")
    if re.search(r"\b(average|avg|mean)\b", lowered):
        hints.append("AGGREGATION contract: use AVG and return only the average alias unless another field is explicitly requested.")
    if re.search(r"\b(number of people they are following)\b", lowered):
        hints.append("METRIC contract: this asks for relationship count, so use count{(u)-[:FOLLOWS]->(:User)} rather than u.following.")
    if re.search(r"\b(number of statuses|statuses posted)\b", lowered):
        hints.append("METRIC contract: statuses is a stored user property; use u.statuses, not COUNT(tweets).")
    if re.search(r"\b(number of favorites|favorite count|favorites count)\b", lowered):
        hints.append("METRIC contract: tweet favorites is the stored Tweet.favorites property, not COUNT unless counting favorite relationships appears in the schema.")
    if re.search(r"\b(number of followers|followers count|follower count)\b", lowered):
        hints.append("METRIC contract: user followers is the stored User.followers property unless the question explicitly asks for matched follower nodes.")
    if re.search(r"\bposted the most tweets|most tweets posted|posted most tweets\b", lowered):
        hints.append("METRIC contract: count matched POSTS relationships/tweets for 'posted the most tweets'; do not use the stored statuses property.")
    if re.search(r"\b(sales amount|order value)\b", lowered) or (
        "revenue" in lowered and re.search(r"\b(order|orders|customer|customers|product|products|sales)\b", lowered)
    ):
        hints.append("METRIC contract: compute order revenue from ORDERS relationship properties: quantity * unitPrice * (1 - discount) when discount is relevant.")
    if re.search(r"\b(total order value|sum of.*unit ?price.*quantity)\b", lowered):
        hints.append("METRIC contract: group order-line totals with WITH before ranking customers/orders.")
    if re.search(r"\b(budget.*revenue|revenue.*budget|ratio)\b", lowered):
        hints.append("METRIC contract: for budget/revenue ratio, filter null/zero values, compute toFloat(budget) / revenue, return the computed alias with requested base fields.")

    if re.search(r"\bfollow(?:s|ing)?\b", lowered):
        hints.append("PATH contract: for 'A follows B' use (A)-[:FOLLOWS]->(B); for followers/users who follow B use (B)<-[:FOLLOWS]-(user).")
    if re.search(r"\bstarted following\b", lowered):
        hints.append("TWITTER INPUT PATTERN: FOLLOWS has no timestamp in the schema; for 'users who started following Neo4j' use incoming FOLLOWS and the closest example's projection/ranking instead of inventing a follow date.")
    if re.search(r"\bmentions?\b", lowered):
        hints.append("PATH contract: use Tweet-[:MENTIONS]->User/Me for mention semantics; do not use text CONTAINS for mentioned users.")
    if re.search(r"\bretweet", lowered):
        hints.append("PATH contract: retweet event is poster-[:POSTS]->retweet-[:RETWEETS]->original.")
    if re.search(r"\bretweeted (the )?most times|retweeted by other users|tweets? retweeted by other users\b", lowered):
        hints.append("PATH contract: for tweets retweeted by other tweets/users, count retweet Tweet nodes, not User nodes.")
    if re.search(r"\b(hashtag|tagged|tags?)\b", lowered):
        hints.append("PATH contract: hashtags use Tweet-[:TAGS]->Hashtag.")
    if re.search(r"\b(link|url)\b", lowered) and not re.search(r"\bprofile image url\b|\bimage url\b", lowered):
        hints.append("PATH contract: links use Tweet-[:CONTAINS]->Link; URL filters should use link.url CONTAINS for partial URLs.")
    if re.search(r"\btweets?\b", lowered) and re.search(r"\b(link|url)\b", lowered) and re.search(r"\b(favorite|top|sort)\b", lowered):
        hints.append("RETURN contract: for ranked tweets with links, usually return tweet text, favorites, and link.url rather than only the Tweet node.")
    if re.search(r"\btweets?\b", lowered) and re.search(r"\b(include|contain|link|url)\b", lowered) and re.search(r"\bfollowing ['\"]?neo4j|follow ['\"]?neo4j|users? following ['\"]?neo4j", lowered):
        hints.append("TWITTER INPUT PATTERN: tweets with links posted by users following Neo4j should return tweet.text AS tweet_text, tweet.created_at AS created_at, and link.url AS link_url when ranked/listed.")
    if re.search(r"\btweets?\b", lowered) and re.search(r"\b(using|posted using)\b", lowered):
        hints.append("RETURN contract: source-filtered top tweets usually return tweet text and favorites, not only the Tweet node.")
    if re.search(r"\btweets?\b", lowered) and re.search(r"\bhashtag\b", lowered) and re.search(r"\b(all|containing)\b", lowered):
        hints.append("RETURN contract: for tweets containing a hashtag, include both the Tweet and Hashtag entity when the question asks for all tweets containing a hashtag.")
    if re.search(r"\bhashtags? used in tweets? that mention\b", lowered):
        hints.append("PATH contract: first find tweets mentioning the target user, then traverse to the posted tweet/hashtag pattern shown by the closest example; do not attach TAGS to a User node.")
    if re.search(r"\bhashtags? (used|appearing|in).*tweets?.*(mention|latest|by)\b", lowered):
        hints.append("RETURN contract: hashtag listing questions usually return h.name, not the Hashtag node.")
    if re.search(r"\busers? mentioned in .*tweets?\b", lowered):
        hints.append("PATH contract: if asking users mentioned in someone's tweets, start from that person/account -> POSTS -> Tweet -> MENTIONS -> mentioned user.")
    if re.search(r"\bsame tweets? as\b", lowered):
        hints.append("PATH contract: 'mentioned in the same tweets as X' means bind one Tweet and traverse MENTIONS from that same tweet to the other users.")
    if re.search(r"\busers? that .*mentions? most frequently\b|\bmentions? most frequently\b", lowered):
        hints.append("AGGREGATION contract: count the posting tweets per mentioned user, order by that count DESC, and do not invent a LIMIT unless the selected example/question has one.")
    if re.search(r"\bmentioned the most\b|\bmentions? .* most\b", lowered) and re.search(r"\bneo4j\b", lowered):
        hints.append("TWITTER INPUT PATTERN: distinguish 'Neo4j mentions users' from 'users mention Neo4j'. The POSTing account owns POSTS; MENTIONS points from Tweet to the mentioned user.")
    if re.search(r"\btweets? that mention\b", lowered) and re.search(r"\bcontain(s|ing)? a link\b", lowered):
        hints.append("RETURN contract: for tweets that mention a user and contain a link, return tweet text, created_at, and link.url when the question asks for recent/listed link tweets.")
    if re.search(r"\broles?\b", lowered):
        hints.append("PATH contract: movie roles live on the ACTED_IN relationship variable, not on Person or Movie.")
    if re.search(r"\bratings?\b", lowered):
        hints.append("PATH contract: reviews/ratings can live on a relationship; bind the relationship variable when the schema/example shows rating there.")
    if re.search(r"\b(suppl|supplier|supplies)\b", lowered):
        hints.append("PATH contract: suppliers connect to products with Supplier-[:SUPPLIES]->Product.")
    if re.search(r"\b(order|ordered|purchased|customer)\b", lowered):
        hints.append("PATH contract: customer order flow is Customer-[:PURCHASED]->Order-[orders:ORDERS]->Product; order-line metrics are on orders.")
    if re.search(r"\b(category|categories)\b", lowered):
        hints.append("PATH contract: products connect to categories with Product-[:PART_OF]->Category.")
    if re.search(r"\b(genre|genres)\b", lowered):
        hints.append("PATH contract: movies connect to genres with Movie-[:IN_GENRE]->Genre.")
    if re.search(r"\b(actor|acted|directed by actors)\b", lowered):
        hints.append("PATH contract: actors use Actor/Person-[:ACTED_IN]->Movie; if the same person directed and acted, reuse the same person variable.")
    if re.search(r"\b(director|directed)\b", lowered):
        hints.append("PATH contract: directors use Director/Person-[:DIRECTED]->Movie.")

    if re.search(r"\bneo4j\b", lowered):
        hints.append("ENTITY contract: choose :Me vs :User from the selected example and relationship type; do not blindly convert every Neo4j reference to :Me.")
    if re.search(r"\bamplif(?:y|ies|ied|ying)\b", lowered):
        hints.append("TWITTER INPUT PATTERN: AMPLIFIES is Me-[:AMPLIFIES]->User. If wording says 'amplifies the most', bind the relationship and COUNT it; if it only asks which users are amplified, do not rank unless asked.")
    if re.search(r"\btop\s+\d+\s+users?.*amplified|users?.*has amplified\b", lowered) and not re.search(r"\bmost|number|count|how many\b", lowered):
        hints.append("RETURN contract: for top amplified users without a count phrase, return the user entity or requested user fields and rank by user.followers.")
    if re.search(r"\btop\s+\d+\s+followers?.*betweenness|followers?.*betweenness\b", lowered):
        hints.append("RETURN contract: for followers ranked by betweenness, return follower.name and follower.betweenness, ordered by follower.betweenness DESC.")
    if re.search(r"\btweets? by .*using .*source\b", lowered):
        hints.append("RETURN contract: for tweets using a source, return the Tweet node unless the question asks for text/favorites/source fields.")
    if re.search(r"\bprofile image\b", lowered):
        hints.append("FILTER/RETURN contract: profile image questions require profile_image_url IS NOT NULL and should return user.screen_name plus user.profile_image_url, not the full User node.")
    if re.search(r"\bprofile image urls? of users? who follow\b", lowered):
        hints.append("RETURN contract: if asking only for profile image URLs, return only u.profile_image_url and do not add LIMIT unless the question asks for first/top.")
    if re.search(r"\blocation|based in|located in\b", lowered):
        hints.append("FILTER contract: Twitter location questions use User.location; grouping locations returns location plus count.")
    if re.search(r"\b(on|date)\s+['\"]?\d{4}-\d{2}-\d{2}['\"]?", lowered):
        hints.append("FILTER contract: date literals on tweets should compare date(tweet.created_at) to date('YYYY-MM-DD').")
    if re.search(r"\b(more than one|different|distinct|unique|multiple)\b", lowered):
        hints.append("COLLECTION contract: use DISTINCT in COUNT/collect or RETURN only when uniqueness is part of the question.")
    if re.search(r"\b(language|languages|country|countries)\b", lowered):
        hints.append("COLLECTION contract: languages/countries may be list properties; use collect(DISTINCT ...), size(...), or membership checks as shown by examples.")
    if re.search(r"\b(released in|released after|released before|release year|christmas day|year)\b", lowered):
        hints.append("DATE/YEAR contract: for recommendation movies, prefer Movie.year when examples use it; for movies dataset, prefer Movie.released.")
    if re.search(r"\bshortest|longest|runtime\b", lowered):
        hints.append("METRIC contract: shortest/longest movies rank by runtime ASC/DESC.")
    if re.search(r"\btop rated|imdb\b", lowered):
        hints.append("METRIC contract: IMDb ranking uses imdbRating for rating and imdbVotes for vote-count questions.")
    if re.search(r"\bdiscontinued\b", lowered):
        hints.append("FILTER contract: Northwind discontinued is a Product boolean; use p.discontinued = true/false.")
    if re.search(r"\bfreight\b", lowered):
        hints.append("FILTER contract: freight is on Order and may need toFloat(o.freight) for numeric comparison/ranking.")
    if re.search(r"\breorder level|units on order|units in stock\b", lowered):
        hints.append("METRIC contract: reorderLevel, unitsOnOrder, and unitsInStock are Product properties.")

    if re.search(r"\bscreen names? of .*users?.*posted the most tweets\b", lowered):
        hints.append("RETURN contract: return only u.screen_name AS screen_name after counting tweets; do not return name or the count unless asked.")
    if re.search(r"\btop\s+\d+\s+users?.*most followers\b|\busers? with the most followers\b", lowered):
        hints.append("RETURN contract: for users ranked by followers, return u.screen_name and u.followers unless the question asks for names too.")
    if re.search(r"\busers?.*follow(?:ed by)? ['\"]?neo4j['\"]?.*followers\b|\bfollowed by ['\"]?neo4j['\"]?.*followers\b", lowered):
        hints.append("TWITTER INPUT PATTERN: users followed by Neo4j use (me:Me {screen_name: 'neo4j'})-[:FOLLOWS]->(user:User) and return user.screen_name plus requested metrics.")
    if re.search(r"\btop\s+\d+\s+tweets?.*mention\b", lowered) and not re.search(r"\bscreen name\b", lowered):
        hints.append("RETURN contract: top tweets that mention a user usually return tweet text and favorites, ordered by favorites DESC.")
    if re.search(r"\btweets?.*mention.*screen name\b", lowered):
        hints.append("RETURN contract: when the target is described by screen_name and no fields are named, returning the Tweet node is acceptable; rank by favorites for top tweets.")
    if re.search(r"\bmost recent tweets?.*created_at|created_at.*date\b", lowered):
        hints.append("RETURN contract: for recent tweets based on created_at, return t.text and t.created_at when the question names the date field.")
    if re.search(r"\bhighest number of favorites|most favorites|favorites count\b", lowered):
        hints.append("ORDER contract: rank tweets by t.favorites DESC; use LIMIT 1 when the question says singular 'which tweet' without a number.")
    if re.search(r"\blowest number of followers|least number of followers\b", lowered):
        hints.append("RETURN contract: users ranked by lowest followers should return u.screen_name and u.followers, ordered by u.followers ASC.")
    if re.search(r"\busers? who have more than \d+ followers\b", lowered) and re.search(r"\bfirst\s+\d+\b", lowered):
        hints.append("RETURN contract: first users above a follower threshold usually return u.name, u.screen_name, and u.followers ordered by followers DESC.")
    if re.search(r"\bmore than \d+ followers\b", lowered) and re.search(r"\bless than \d+ statuses\b", lowered):
        hints.append("RETURN contract: follower/status filters should return screen_name, followers, and statuses as separate projected properties.")
    if re.search(r"\btop\s+\d+\s+tweets? from users located\b", lowered):
        hints.append("RETURN contract: top tweets from a location should return t.text and t.favorites, ordered by t.favorites DESC.")
    if re.search(r"\btweets? by ['\"]?neo4j['\"]?.*hashtag|tweets? by ['\"]?neo4j['\"]?.*#\w+", lowered):
        hints.append("RETURN contract: Neo4j hashtag tweet rankings usually return t.text, t.favorites, and t.created_at.")
    if re.search(r"\bcreated in 2021\b", lowered):
        hints.append("FILTER contract: use datetime('2021-01-01T00:00:00Z') through datetime('2021-12-31T23:59:59Z') for 2021 tweet ranges.")
    if re.search(r"\bfirst\s+\d+\s+tweets?.*tagged.*hashtag.*mention", lowered):
        hints.append("RETURN contract: tweets tagged with a hashtag and mentioning another user should return tweet_id, tweet_text, and created_at ordered by created_at ASC.")
    if re.search(r"\bfirst\s+\d+\s+users?.*mentioned ['\"]?neo4j", lowered):
        hints.append("TWITTER INPUT PATTERN: users who mentioned Neo4j are User-[:POSTS]->Tweet-[:MENTIONS]->Me; return user.screen_name and tweet.created_at ordered by tweet.created_at ASC.")
    if re.search(r"\bmost recent tweet posted by ['\"]?neo4j", lowered):
        hints.append("RETURN contract: most recent tweet posted by Neo4j should return t.text ordered by t.created_at DESC LIMIT 1.")
    if re.search(r"\bdate and time of the most recent tweet.*mentions a user followed by", lowered):
        hints.append("RETURN contract: use max(tweet.created_at) AS most_recent_tweet_date after matching tweets that mention followed users.")
    if re.search(r"\baverage number of favorites.*mention both.*hashtag", lowered):
        hints.append("TWITTER INPUT PATTERN: bind one Tweet with both MENTIONS target and TAGS hashtag, then return avg(t.favorites) AS average_favorites.")
    if re.search(r"\baverage number of followers.*same tweets as ['\"]?neo4j", lowered):
        hints.append("TWITTER INPUT PATTERN: for same tweets as Neo4j, bind one tweet connected to Neo4j and other users, then avg(other.followers) AS average_followers.")
    if re.search(r"\breplied to a tweet by ['\"]?neo4j", lowered):
        hints.append("PATH contract: replies use (user)-[:POSTS]->(original)<-[:REPLY_TO]-(reply); return reply ordered by reply.created_at ASC for first replies.")
    if re.search(r"\bmost replies\b", lowered):
        hints.append("PATH contract: count optional incoming REPLY_TO tweets per Neo4j tweet and return tweet_text plus reply_count.")
    if re.search(r"\bhashtags used by users with more than", lowered):
        hints.append("PATH contract: users with follower filters must remain connected to their posted tweets before traversing Tweet-[:TAGS]->Hashtag.")
    if re.search(r"\btweets that mention ['\"]?neo4j['\"]? and contain a link\b", lowered):
        hints.append("RETURN contract: when asking all tweets mentioning Neo4j and containing a link, return the Tweet node and use EXISTS for the link condition.")
    if re.search(r"\blinks to ['\"]?https?://", lowered):
        hints.append("FILTER contract: URL literal questions should use link.url CONTAINS '<url-fragment>' rather than exact Link map equality.")
    if re.search(r"\bbetweenness higher than", lowered):
        hints.append("RETURN contract: betweenness threshold questions return u.screen_name and u.betweenness.")
    if re.search(r"\btop\s+\d+\s+locations.*most tweets\b", lowered):
        hints.append("FILTER/RETURN contract: top tweet locations should filter u.location IS NOT NULL and return u.location AS Location, count(t) AS TweetCount.")
    if re.search(r"\bhashtags.*tweets.*contain links.*posted by", lowered):
        hints.append("PATH contract: for hashtags in tweets with links, first match posted Tweet, use EXISTS for CONTAINS Link, then MATCH Tweet-[:TAGS]->Hashtag.")
    if re.search(r"\busers? who follow ['\"]?neo4j\b|\ball users who follow ['\"]?neo4j\b", lowered):
        hints.append("PATH contract: users who follow Neo4j use (u:User)-[:FOLLOWS]->(:Me {screen_name: 'neo4j'}), not the reverse direction.")
    if re.search(r"\bfirst\s+\d+\s+tweets?.*contain a hashtag\b", lowered):
        hints.append("ORDER contract: first tweets containing a hashtag from neo4j should order by t.created_at ASC.")
    if re.search(r"\bsimilar to neo4j|similarity|similar_to\b", lowered):
        hints.append("PATH contract: bind the SIMILAR_TO relationship as [s:SIMILAR_TO] and return s.score AS similarity; do not put score inside the relationship pattern.")
    if re.search(r"\bpopular external sources|external sources\b", lowered):
        hints.append("TWITTER INPUT PATTERN: popular sources use Tweet-[:USING]->Source, count tweets per Source, then return tweets and sources for the selected sources.")
    if re.search(r"\bretweets mentions from\b", lowered):
        hints.append("TWITTER INPUT PATTERN: 'retweets mentions from' uses Me-[:RT_MENTIONS]->User and counts that relationship.")
    if re.search(r"\bretweeted by ['\"]?me['\"]?|users retweeted by ['\"]?me['\"]?", lowered):
        hints.append("RETURN contract: users retweeted by Me should return user.screen_name AS retweeted_user, not the full User node.")
    if re.search(r"\bretweeted.*tweets mentioning ['\"]?neo4j|retweeted tweets mentioning ['\"]?neo4j", lowered):
        hints.append("RETURN contract: users who retweeted tweets mentioning Neo4j should return u.screen_name when asking for users.")
    if re.search(r"\bsame tweets as users followed by ['\"]?neo4j", lowered):
        hints.append("TWITTER INPUT PATTERN: start from Neo4j User -> FOLLOWS -> followedUser, find tweets mentioning followedUser, then other users mentioned in that same tweet; average otherUser.followers.")

    if not hints:
        return "- No extra query-specific hints. Follow schema, domain hints, and selected examples."
    return "\n".join(f"- {hint}" for hint in hints)


def _select_examples(question: str, examples: list[dict[str, str]], top_k: int = _DEFAULT_TOP_K) -> list[dict[str, str]]:
    if not examples:
        return []

    query_signature = _build_retrieval_signature(question)
    scored = []
    for example in examples:
        candidate_signature = _build_retrieval_signature(example["question"], example["cypher"])
        score = _lexical_similarity(query_signature, candidate_signature)
        scored.append({**example, "score": score})

    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[: max(1, min(top_k, len(scored)))]


def _build_prompt_sections(
    *,
    domain_name: str,
    domain_facts: str,
    semantic_plan: str,
    domain_hints: str,
    query_hints: str,
    examples: str,
    escape_braces: bool = False,
) -> str:
    format_block = _escape_prompt_block if escape_braces else str.strip
    return (
        f"You are a Cypher expert for the Neo4j {domain_name} graph database.\n\n"
        "=== SHARED CORE RULES ===\n"
        f"{format_block(_SHARED_CORE_RULES)}\n\n"
        "=== DOMAIN FACTS ===\n"
        f"{format_block(domain_facts)}\n\n"
        "=== SEMANTIC GRAPH PLAN ===\n"
        f"{format_block(semantic_plan)}\n\n"
        "=== DOMAIN HINTS ===\n"
        f"{format_block(domain_hints)}\n\n"
        "=== QUERY INTENT HINTS ===\n"
        f"{format_block(query_hints)}\n\n"
        "=== SELECTED EXAMPLES ===\n"
        f"{format_block(examples)}"
    )


def get_prompt_sections(
    db_name: str | None = None,
    question: str | None = None,
    original_question: str | None = None,
) -> str:
    db = db_name or get_settings().database_name
    config = _DOMAIN_CONFIGS.get(db, {})
    all_examples = config.get("examples", [])
    selected_examples = (
        _select_examples(f"{original_question or ''} {question or ''}".strip(), all_examples)
        if question
        else all_examples[:_DEFAULT_TOP_K]
    )

    return _build_prompt_sections(
        domain_name=_DOMAIN_NAMES.get(db, db.capitalize()),
        domain_facts=get_entity_definitions(db),
        semantic_plan=build_semantic_plan_text(db, f"{original_question or ''} {question or ''}".strip()),
        domain_hints=_format_hints(config.get("hints", [])),
        query_hints=_build_query_hints(f"{original_question or ''} {question or ''}".strip()),
        examples=_format_examples(selected_examples),
    )


def get_cypher_template(
    db_name: str | None = None,
    question: str | None = None,
    original_question: str | None = None,
) -> str:
    """Return a complete PromptTemplate-compatible Cypher prompt."""
    db = db_name or get_settings().database_name
    config = _DOMAIN_CONFIGS.get(db, {})
    all_examples = config.get("examples", [])
    selected_examples = (
        _select_examples(f"{original_question or ''} {question or ''}".strip(), all_examples)
        if question
        else all_examples[:_DEFAULT_TOP_K]
    )
    sections = _build_prompt_sections(
        domain_name=_DOMAIN_NAMES.get(db, db.capitalize()),
        domain_facts=get_entity_definitions(db),
        semantic_plan=build_semantic_plan_text(db, f"{original_question or ''} {question or ''}".strip()),
        domain_hints=_format_hints(config.get("hints", [])),
        query_hints=_build_query_hints(f"{original_question or ''} {question or ''}".strip()),
        examples=_format_examples(selected_examples),
        escape_braces=True,
    )
    return (
        "=== SCHEMA ===\n"
        "{schema}\n\n"
        f"{sections}\n\n"
        "=== USER INPUT ===\n"
        "{question}\n"
    )
