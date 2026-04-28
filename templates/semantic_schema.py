"""Semantic schema grounding for Text-to-Cypher prompts.

This module turns a natural-language question into a small graph-aware plan:
linked schema elements, plausible traversal paths, and query operation hints.
It is intentionally deterministic and training-free so it can be evaluated and
replaced module-by-module.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class NodeSpec:
    label: str
    description: str
    properties: tuple[str, ...]
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class RelationshipSpec:
    start: str
    rel_type: str
    end: str
    description: str
    aliases: tuple[str, ...] = ()
    properties: tuple[str, ...] = ()

    @property
    def pattern(self) -> str:
        return f"(:{self.start})-[:{self.rel_type}]->(:{self.end})"


@dataclass(frozen=True)
class SemanticSchema:
    nodes: tuple[NodeSpec, ...]
    relationships: tuple[RelationshipSpec, ...]


@dataclass(frozen=True)
class PathStep:
    rel: RelationshipSpec
    forward: bool = True

    @property
    def start_label(self) -> str:
        return self.rel.start if self.forward else self.rel.end

    @property
    def end_label(self) -> str:
        return self.rel.end if self.forward else self.rel.start

    @property
    def rel_type(self) -> str:
        return self.rel.rel_type


_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
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
    "show",
    "the",
    "to",
    "what",
    "which",
    "who",
    "with",
}


_SCHEMAS: dict[str, SemanticSchema] = {
    "twitter": SemanticSchema(
        nodes=(
            NodeSpec("Me", "The Neo4j account in the Twitter demo graph.", ("screen_name", "name", "followers", "following", "statuses", "betweenness", "profile_image_url", "url", "location"), ("neo4j", "me", "account")),
            NodeSpec("User", "A Twitter user/account.", ("screen_name", "name", "followers", "following", "statuses", "betweenness", "profile_image_url", "url", "location"), ("user", "users", "follower", "followers", "account", "person")),
            NodeSpec("Tweet", "A tweet object posted by a user/account.", ("id_str", "text", "created_at", "favorites"), ("tweet", "tweets", "post", "posts", "status")),
            NodeSpec("Hashtag", "A hashtag attached to a tweet.", ("name",), ("hashtag", "hashtags", "tag", "tags")),
            NodeSpec("Link", "A URL/link contained in a tweet.", ("url",), ("link", "links", "url", "urls")),
            NodeSpec("Source", "The client/source used to post a tweet.", ("name",), ("source", "sources", "client")),
        ),
        relationships=(
            RelationshipSpec("Me", "FOLLOWS", "User", "Neo4j/Me follows a user.", ("follow", "follows", "following", "followed")),
            RelationshipSpec("User", "FOLLOWS", "Me", "A user follows Neo4j/Me.", ("follower", "followers", "started following", "follow neo4j")),
            RelationshipSpec("User", "FOLLOWS", "User", "A user follows another user.", ("follow", "follows", "following")),
            RelationshipSpec("Me", "POSTS", "Tweet", "Neo4j/Me posted a tweet.", ("post", "posts", "posted", "tweeted", "by neo4j")),
            RelationshipSpec("User", "POSTS", "Tweet", "A user posted a tweet.", ("post", "posts", "posted", "tweeted", "by user")),
            RelationshipSpec("Tweet", "MENTIONS", "User", "A tweet mentions a user.", ("mention", "mentions", "mentioned")),
            RelationshipSpec("Tweet", "MENTIONS", "Me", "A tweet mentions Neo4j/Me.", ("mention neo4j", "mentions neo4j", "mentioned neo4j")),
            RelationshipSpec("Tweet", "RETWEETS", "Tweet", "A retweet tweet points to the original tweet.", ("retweet", "retweets", "retweeted")),
            RelationshipSpec("Tweet", "TAGS", "Hashtag", "A tweet is tagged with a hashtag.", ("hashtag", "hashtags", "tag", "tags", "tagged")),
            RelationshipSpec("Tweet", "CONTAINS", "Link", "A tweet contains a URL/link.", ("link", "links", "url", "urls", "contains link")),
            RelationshipSpec("Tweet", "USING", "Source", "A tweet was posted using a source/client.", ("source", "using", "posted using")),
            RelationshipSpec("Tweet", "REPLY_TO", "Tweet", "A tweet replies to another tweet.", ("reply", "replies", "replied")),
            RelationshipSpec("Me", "AMPLIFIES", "User", "Neo4j/Me amplifies a user.", ("amplify", "amplifies", "amplified")),
            RelationshipSpec("Me", "INTERACTS_WITH", "User", "Neo4j/Me interacts with a user.", ("interact", "interacts", "interaction")),
            RelationshipSpec("Me", "SIMILAR_TO", "User", "Neo4j/Me is similar to a user.", ("similar", "similarity"), ("score",)),
            RelationshipSpec("Me", "RT_MENTIONS", "User", "Neo4j/Me retweets mentions from a user.", ("retweets mentions from", "rt_mentions")),
        ),
    ),
    "movies": SemanticSchema(
        nodes=(
            NodeSpec("Person", "A person who can act, direct, write, produce, review, or follow another person.", ("name", "born"), ("person", "people", "actor", "director", "producer", "writer", "reviewer")),
            NodeSpec("Movie", "A movie.", ("title", "released", "votes", "tagline"), ("movie", "movies", "film", "films")),
        ),
        relationships=(
            RelationshipSpec("Person", "ACTED_IN", "Movie", "A person acted in a movie.", ("acted", "actor", "actors", "role", "roles"), ("roles",)),
            RelationshipSpec("Person", "DIRECTED", "Movie", "A person directed a movie.", ("directed", "director", "directors")),
            RelationshipSpec("Person", "PRODUCED", "Movie", "A person produced a movie.", ("produced", "producer", "producers")),
            RelationshipSpec("Person", "WROTE", "Movie", "A person wrote a movie.", ("wrote", "writer", "written")),
            RelationshipSpec("Person", "REVIEWED", "Movie", "A person reviewed a movie.", ("review", "reviewed", "rating", "summary"), ("rating", "summary")),
            RelationshipSpec("Person", "FOLLOWS", "Person", "A person follows another person.", ("follow", "follows", "followers")),
        ),
    ),
    "recommendations": SemanticSchema(
        nodes=(
            NodeSpec("Movie", "A movie in the recommendation graph.", ("title", "year", "released", "runtime", "budget", "revenue", "imdbRating", "imdbVotes", "plot", "languages", "countries"), ("movie", "movies", "film")),
            NodeSpec("Actor", "An actor.", ("name", "born", "died", "bornIn", "tmdbId", "imdbId", "bio"), ("actor", "actors", "star", "stars")),
            NodeSpec("Director", "A director.", ("name", "born", "died", "bornIn", "tmdbId", "imdbId", "bio"), ("director", "directors")),
            NodeSpec("Person", "A person who can act or direct.", ("name", "born", "died", "bornIn", "tmdbId", "imdbId", "bio"), ("person", "people")),
            NodeSpec("Genre", "A movie genre.", ("name",), ("genre", "genres")),
            NodeSpec("User", "A user who rated movies.", ("userId", "name"), ("user", "users", "viewer", "reviewer")),
        ),
        relationships=(
            RelationshipSpec("Actor", "ACTED_IN", "Movie", "An actor acted in a movie.", ("acted", "actor", "actors", "starred")),
            RelationshipSpec("Person", "ACTED_IN", "Movie", "A person acted in a movie.", ("acted", "actor", "actors", "starred")),
            RelationshipSpec("Director", "DIRECTED", "Movie", "A director directed a movie.", ("directed", "director", "directors")),
            RelationshipSpec("Person", "DIRECTED", "Movie", "A person directed a movie.", ("directed", "director", "directors")),
            RelationshipSpec("Movie", "IN_GENRE", "Genre", "A movie belongs to a genre.", ("genre", "genres")),
            RelationshipSpec("User", "RATED", "Movie", "A user rated a movie.", ("rated", "rating", "ratings", "reviewed"), ("rating", "timestamp")),
        ),
    ),
    "northwind": SemanticSchema(
        nodes=(
            NodeSpec("Product", "A sellable product.", ("productID", "productName", "unitPrice", "unitsInStock", "unitsOnOrder", "reorderLevel", "discontinued"), ("product", "products", "item", "items")),
            NodeSpec("Category", "A product category.", ("categoryID", "categoryName", "description"), ("category", "categories")),
            NodeSpec("Supplier", "A supplier/vendor.", ("supplierID", "companyName", "contactName", "contactTitle", "address", "city", "region", "postalCode", "country", "phone", "fax", "homePage"), ("supplier", "suppliers", "vendor")),
            NodeSpec("Customer", "A customer.", ("customerID", "companyName", "contactName", "contactTitle", "address", "city", "region", "postalCode", "country", "phone", "fax"), ("customer", "customers", "buyer")),
            NodeSpec("Order", "A customer order.", ("orderID", "orderDate", "requiredDate", "shippedDate", "shipVia", "freight", "shipName", "shipAddress", "shipCity", "shipRegion", "shipPostalCode", "shipCountry"), ("order", "orders", "purchase", "purchases")),
        ),
        relationships=(
            RelationshipSpec("Product", "PART_OF", "Category", "A product belongs to a category.", ("category", "part of", "belongs")),
            RelationshipSpec("Supplier", "SUPPLIES", "Product", "A supplier supplies a product.", ("supplies", "supplier", "supplied")),
            RelationshipSpec("Customer", "PURCHASED", "Order", "A customer purchased/placed an order.", ("purchased", "ordered", "orders", "customer")),
            RelationshipSpec("Order", "ORDERS", "Product", "An order contains a product line.", ("ordered product", "contains product", "sales", "order value"), ("unitPrice", "quantity", "discount")),
        ),
    ),
    "climate": SemanticSchema(
        nodes=(
            NodeSpec("Source", "A climate model/source.", ("name",), ("source", "sources", "model", "models")),
            NodeSpec("RCM", "A regional climate model.", ("name",), ("rcm", "regional climate model")),
            NodeSpec("Variable", "A climate variable.", ("name", "cf_standard_name"), ("variable", "variables")),
            NodeSpec("Experiment", "A climate experiment.", ("name",), ("experiment", "experiments")),
            NodeSpec("Institute", "An institute that produces a source.", ("name",), ("institute", "institutes")),
            NodeSpec("SourceComponent", "A component of a source.", ("name",), ("component", "components")),
            NodeSpec("SourceType", "A type of source.", ("name",), ("type", "types")),
            NodeSpec("Realm", "A climate realm.", ("name",), ("realm", "realms")),
            NodeSpec("Frequency", "A data frequency.", ("name",), ("frequency", "frequencies")),
            NodeSpec("Resolution", "A model resolution.", ("name",), ("resolution", "resolutions")),
            NodeSpec("Country", "A country region.", ("name", "code"), ("country", "countries")),
            NodeSpec("Country_Subdivision", "A country subdivision.", ("name", "code"), ("subdivision", "state", "province", "region")),
            NodeSpec("Continent", "A continent region.", ("name",), ("continent", "continents")),
        ),
        relationships=(
            RelationshipSpec("Source", "PRODUCES_VARIABLE", "Variable", "A source produces a variable.", ("variable", "produces variable", "include variable")),
            RelationshipSpec("Source", "USED_IN_EXPERIMENT", "Experiment", "A source is used in an experiment.", ("experiment", "used in")),
            RelationshipSpec("Source", "PRODUCED_BY_INSTITUTE", "Institute", "A source is produced by an institute.", ("institute", "produced by")),
            RelationshipSpec("Source", "HAS_SOURCE_COMPONENT", "SourceComponent", "A source has a source component.", ("component", "source component")),
            RelationshipSpec("Source", "IS_OF_TYPE", "SourceType", "A source has a source type.", ("type", "source type")),
            RelationshipSpec("Source", "APPLIES_TO_REALM", "Realm", "A source applies to a realm.", ("realm", "applies to")),
            RelationshipSpec("Source", "HAS_FREQUENCY", "Frequency", "A source has a data frequency.", ("frequency", "freq")),
            RelationshipSpec("Source", "HAS_RESOLUTION", "Resolution", "A source has a resolution.", ("resolution",)),
            RelationshipSpec("RCM", "DRIVEN_BY_SOURCE", "Source", "An RCM is driven by a source.", ("driven by", "drives")),
            RelationshipSpec("RCM", "COVERS_REGION", "Country", "An RCM covers a country.", ("covers", "region", "country")),
            RelationshipSpec("RCM", "COVERS_REGION", "Country_Subdivision", "An RCM covers a country subdivision.", ("covers", "subdivision", "region")),
            RelationshipSpec("RCM", "COVERS_REGION", "Continent", "An RCM covers a continent.", ("covers", "continent", "region")),
            RelationshipSpec("Country_Subdivision", "PART_OF", "Country", "A subdivision is part of a country.", ("part of", "belongs to")),
        ),
    ),
}


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z][a-z0-9_]*", text.lower()) if token not in _STOPWORDS}


def _term_score(question_tokens: set[str], lowered_question: str, terms: tuple[str, ...]) -> float:
    score = 0.0
    for term in terms:
        normalized = term.lower().replace("_", " ")
        term_tokens = _tokens(normalized)
        if normalized and normalized in lowered_question:
            score += 2.0
        if term_tokens:
            score += len(question_tokens & term_tokens) / len(term_tokens)
    return score


def _linked_nodes(schema: SemanticSchema, question: str) -> list[tuple[NodeSpec, float]]:
    lowered = question.lower()
    question_tokens = _tokens(question)
    linked = []
    for node in schema.nodes:
        terms = (node.label,) + node.aliases + node.properties
        score = _term_score(question_tokens, lowered, tuple(str(term) for term in terms))
        if score > 0:
            linked.append((node, score))
    linked.sort(key=lambda item: item[1], reverse=True)
    return linked


def _linked_relationships(schema: SemanticSchema, question: str) -> list[tuple[RelationshipSpec, float]]:
    lowered = question.lower()
    question_tokens = _tokens(question)
    linked = []
    for rel in schema.relationships:
        rel_terms = (rel.rel_type,) + rel.aliases + rel.properties + (rel.description,)
        score = _term_score(question_tokens, lowered, tuple(str(term) for term in rel_terms))
        if score > 0:
            linked.append((rel, score))
    linked.sort(key=lambda item: item[1], reverse=True)
    return linked


def _linked_properties(schema: SemanticSchema, question: str) -> list[str]:
    lowered = question.lower()
    properties = []
    for node in schema.nodes:
        for prop in node.properties:
            normalized = prop.lower().replace("_", " ")
            if prop.lower() in lowered or normalized in lowered:
                properties.append(f"{node.label}.{prop}")
    for rel in schema.relationships:
        for prop in rel.properties:
            normalized = prop.lower().replace("_", " ")
            if prop.lower() in lowered or normalized in lowered:
                properties.append(f"{rel.rel_type}.{prop}")
    return list(dict.fromkeys(properties))


def _operation_hints(question: str) -> list[str]:
    lowered = question.lower()
    hints: list[str] = []
    if limit_match := re.search(r"\b(?:top|first|last|show|list|find)\s+(?:the\s+)?(\d+)\b", lowered):
        hints.append(f"limit={limit_match.group(1)}")
    if re.search(r"\b(top|highest|largest|greatest)\b|\bmost\b(?!\s+recent)", lowered):
        hints.append("ranking=DESC by the metric named in the question")
    if re.search(r"\b(lowest|least|fewest|smallest)\b", lowered):
        hints.append("ranking=ASC by the metric named in the question")
    if re.search(r"\b(recent|latest|newest|created_at|date and time)\b", lowered):
        hints.append("recency=date/time ordering or max date")
    if re.search(r"\b(count|how many|number of|most frequently|frequency)\b", lowered):
        hints.append("aggregation=COUNT when counting matched graph elements")
    if re.search(r"\b(average|avg|mean)\b", lowered):
        hints.append("aggregation=AVG")
    if re.search(r"\b(distinct|unique|different)\b", lowered):
        hints.append("dedupe=DISTINCT")
    if re.search(r"\b(more than|less than|higher than|lower than|above|below|over|under)\b", lowered):
        hints.append("filter=numeric comparison")
    return hints


def _preferred_patterns(db_name: str, question: str) -> list[str]:
    """High-confidence semantic frames for compositional graph questions."""
    lowered = question.lower()
    patterns: list[str] = []

    if db_name == "twitter":
        if re.search(r"\b(neo4j|me)\b.*\bmentions?\b.*\b(their|its|his|her)?\s*tweets?\b|\bmentions? most frequently\b", lowered):
            patterns.append(
                "If an account mentions users in its tweets: (:Me|:User)-[:POSTS]->(:Tweet)-[:MENTIONS]->(:User); aggregate per mentioned user when frequency/most is asked."
            )
        if re.search(r"\btweets? that mention users?.*retweeted tweets?.*mention", lowered):
            patterns.append(
                "For tweets mentioning users who retweeted tweets mentioning Neo4j: bind a tweet that mentions Neo4j, find retweet Tweet nodes, find their posting User, then find other tweets mentioning that User."
            )
        if re.search(r"\busers?.*follow\b.*\bneo4j\b.*\bposted tweets?.*source\b|\bposted tweets?.*source\b.*\bfollow\b.*\bneo4j\b", lowered):
            patterns.append(
                "For users who follow Neo4j and posted tweets using a source: (:User)-[:FOLLOWS]->(:Me {screen_name:'neo4j'}), then (:User)-[:POSTS]->(:Tweet)-[:USING]->(:Source)."
            )
        if re.search(r"\btweets?.*(link|url).*(users? following|follow neo4j|following neo4j)", lowered):
            patterns.append(
                "For tweets with links posted by users following Neo4j: (:User)-[:FOLLOWS]->(:Me), (:User)-[:POSTS]->(:Tweet)-[:CONTAINS]->(:Link)."
            )
        if re.search(r"\bhashtags?.*tweets?.*(contain links|with links|links).*posted by", lowered):
            patterns.append(
                "For hashtags in linked tweets by users: (:User)-[:POSTS]->(:Tweet)-[:CONTAINS]->(:Link) and the same Tweet-[:TAGS]->(:Hashtag)."
            )
        if re.search(r"\bretweeted by other users|retweeted the most times|retweets? count\b", lowered):
            patterns.append(
                "For tweets retweeted by others: (:Tweet)<-[:RETWEETS]-(:Tweet)<-[:POSTS]-(:User); count retweet Tweet nodes."
            )

    if db_name == "northwind":
        if re.search(r"\bcustomers?.*\bpurchased\b.*\bproducts?.*\bsuppl", lowered):
            patterns.append(
                "Customer-to-supplier purchase path: (:Customer)-[:PURCHASED]->(:Order)-[:ORDERS]->(:Product)<-[:SUPPLIES]-(:Supplier)."
            )
        if re.search(r"\border value|sales amount|revenue", lowered):
            patterns.append(
                "Northwind order revenue lives on ORDERS relationship properties: quantity * unitPrice * (1 - discount)."
            )

    if db_name == "movies":
        if re.search(r"\bwritten and directed\b|\bdirected and written\b", lowered):
            patterns.append(
                "Same person and same movie frame: reuse one Person variable with both (p)-[:WROTE]->(m) and (p)-[:DIRECTED]->(m)."
            )
        if re.search(r"\broles?\b", lowered):
            patterns.append("Movie roles live on ACTED_IN relationship properties, not on Person or Movie.")
        if re.search(r"\brating|summary|review", lowered):
            patterns.append("Review rating/summary live on REVIEWED relationship properties.")

    if db_name == "recommendations":
        if re.search(r"\b(actor|actors|acted|starred)\b.*\bgenre\b|\bgenre\b.*\b(actor|actors|acted|starred)\b", lowered):
            patterns.append("Actor-to-genre path: (:Actor)-[:ACTED_IN]->(:Movie)-[:IN_GENRE]->(:Genre).")
        if re.search(r"\busers?\b.*\brated\b|\brated\b.*\b(movie|movies|film|films|title|highest|rating)\b", lowered):
            patterns.append("User ratings live on the RATED relationship between User and Movie.")
        if re.search(r"\bbudget\b.*\brevenue\b|\brevenue\b.*\bbudget\b", lowered):
            patterns.append("Budget/revenue comparisons are Movie properties; filter null/zero values before ratios.")

    if db_name == "climate":
        if re.search(r"\bmodels?.*\bvariable\b|\binclude.*variable\b", lowered):
            patterns.append("Climate model/source to variable path: (:Source)-[:PRODUCES_VARIABLE]->(:Variable).")
        if re.search(r"\brcm\b.*\bsource\b|\bdriven by\b", lowered):
            patterns.append("RCM driver path: (:RCM)-[:DRIVEN_BY_SOURCE]->(:Source).")
        if re.search(r"\bregion|country|continent|subdivision\b", lowered):
            patterns.append("RCM region coverage path: (:RCM)-[:COVERS_REGION]->(:Country|:Country_Subdivision|:Continent).")

    return patterns


def _find_paths(
    schema: SemanticSchema,
    seeds: list[str],
    targets: list[str],
    *,
    allowed_relationships: set[str] | None = None,
    max_depth: int = 3,
) -> list[list[PathStep]]:
    if not seeds or not targets:
        return []

    adjacency: dict[str, list[PathStep]] = {}
    for rel in schema.relationships:
        if allowed_relationships is not None and rel.rel_type not in allowed_relationships:
            continue
        adjacency.setdefault(rel.start, []).append(PathStep(rel, True))
        adjacency.setdefault(rel.end, []).append(PathStep(rel, False))

    paths: list[list[PathStep]] = []
    target_set = set(targets)
    for seed in seeds:
        queue = deque([(seed, [], {seed})])
        seen = {(seed, 0, (seed,), ())}
        while queue:
            node, path, visited_nodes = queue.popleft()
            if path and node in target_set:
                paths.append(path)
            if len(path) >= max_depth:
                continue
            for step in adjacency.get(node, []):
                if step.end_label in visited_nodes and step.end_label != node:
                    continue
                next_visited = visited_nodes | {step.end_label}
                rel_sequence = tuple(path_step.rel_type for path_step in path + [step])
                state = (step.end_label, len(path) + 1, tuple(sorted(next_visited)), rel_sequence)
                if state in seen:
                    continue
                seen.add(state)
                queue.append((step.end_label, path + [step], next_visited))
    paths.sort(key=lambda candidate: (len(candidate), " ".join(step.rel_type for step in candidate)))
    return paths[:50]


def _path_text(path: list[PathStep]) -> str:
    if not path:
        return ""
    parts = [f"(:{path[0].start_label})"]
    for step in path:
        if step.forward:
            parts.append(f"-[:{step.rel_type}]->(:{step.end_label})")
        else:
            parts.append(f"<-[:{step.rel_type}]-(:{step.end_label})")
    return "".join(parts)


def build_semantic_plan_text(db_name: str, question: str | None) -> str:
    """Return a compact, LLM-readable semantic graph plan."""
    if not question:
        return "- No question was provided; use the full schema and selected examples."

    schema = _SCHEMAS.get(db_name)
    if not schema:
        return "- No semantic schema is configured for this database; use the Neo4j schema directly."

    node_links = _linked_nodes(schema, question)
    rel_links = _linked_relationships(schema, question)
    prop_links = _linked_properties(schema, question)
    operations = _operation_hints(question)
    preferred_patterns = _preferred_patterns(db_name, question)

    labels = [node.label for node, score in node_links if score >= 0.8]
    for rel, score in rel_links[:4]:
        if score >= 0.8:
            labels.extend([rel.start, rel.end])
    labels = list(dict.fromkeys(labels))

    lowered = question.lower()
    seed_labels: list[str] = []
    if re.search(r"\b(neo4j|me)\b", lowered):
        seed_labels.extend(label for label in ("Me", "User") if label in {node.label for node in schema.nodes})
    seed_labels.extend(labels[:2])
    seed_labels = list(dict.fromkeys(seed_labels))

    target_labels = labels[:]

    strong_rels = {rel.rel_type for rel, score in rel_links if score >= 0.8}
    rel_weights = {rel.rel_type: score for rel, score in rel_links}
    linked_label_set = set(labels)
    connector_rels = {
        rel.rel_type
        for rel in schema.relationships
        if rel.start in linked_label_set and rel.end in linked_label_set
    }
    allowed_rels = strong_rels | connector_rels

    paths: list[list[PathStep]] = []
    paths.extend(
        _find_paths(
            schema,
            seed_labels[:3],
            target_labels[:5],
            allowed_relationships=allowed_rels or None,
        )
    )
    paths.extend([[PathStep(rel)] for rel, score in rel_links[:5] if score >= 0.8])
    if not paths:
        paths = _find_paths(schema, seed_labels[:3], target_labels[:5])

    unique_paths: list[list[PathStep]] = []
    seen_paths: set[str] = set()
    for path in paths:
        rendered = _path_text(path)
        if rendered and rendered not in seen_paths:
            seen_paths.add(rendered)
            unique_paths.append(path)
    seed_set = set(seed_labels)
    unique_paths.sort(
        key=lambda path: (
            -(
                sum(rel_weights.get(step.rel_type, 0.05) for step in path) / max(1, len(path))
                + (3.0 if path and path[0].start_label in seed_set else 0.0)
                + (0.5 if len(path) > 1 else 0.0)
            ),
            len(path),
            _path_text(path),
        )
    )
    paths = unique_paths[:8]

    lines = [
        "- Treat this as a graph traversal plan, not a memorized query.",
        "- Use linked schema elements to choose the anchor, path, filters, ranking, aggregation, and RETURN projection.",
    ]
    if node_links:
        lines.append(
            "- Linked node labels: "
            + ", ".join(f"{node.label}({score:.2f})" for node, score in node_links[:6])
        )
    if rel_links:
        lines.append(
            "- Linked relationships: "
            + ", ".join(f"{rel.pattern}({score:.2f})" for rel, score in rel_links[:6])
        )
    connector_only = sorted(connector_rels - strong_rels)
    if connector_only:
        lines.append("- Schema connector relationships: " + ", ".join(connector_only[:8]))
    if prop_links:
        lines.append("- Linked properties: " + ", ".join(prop_links[:10]))
    if operations:
        lines.append("- Detected operations: " + "; ".join(operations))
    if preferred_patterns:
        lines.append("- Preferred semantic frames:")
        lines.extend(f"  {idx}. {pattern}" for idx, pattern in enumerate(preferred_patterns, 1))
    if paths:
        lines.append("- Candidate traversal paths:")
        lines.extend(f"  {idx}. {_path_text(path)}" for idx, path in enumerate(paths[:8], 1))
    else:
        lines.append("- Candidate traversal paths: none confidently linked; fall back to schema and examples.")

    return "\n".join(lines)


def semantic_cypher_feedback(db_name: str, question: str | None, cypher: str) -> str | None:
    """Return feedback when Cypher violates high-confidence semantic frames."""
    if not question or not cypher:
        return None
    preferred_patterns = _preferred_patterns(db_name, question)
    if not preferred_patterns:
        return None

    cypher_relationships = {
        rel_type.upper()
        for rel_type in re.findall(r":([A-Za-z][A-Za-z0-9_]*)", cypher)
    }
    missing_frames: list[str] = []
    for pattern in preferred_patterns:
        expected_relationships = {
            rel_type.upper()
            for rel_type in re.findall(r"\[:([A-Z][A-Z0-9_]*)\]", pattern)
        }
        if expected_relationships and not expected_relationships <= cypher_relationships:
            missing = ", ".join(sorted(expected_relationships - cypher_relationships))
            missing_frames.append(f"missing {missing} for frame: {pattern}")

    if not missing_frames:
        return None
    return "Semantic graph plan mismatch; " + " | ".join(missing_frames)
