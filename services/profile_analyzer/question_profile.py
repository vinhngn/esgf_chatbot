from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
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
    "that",
    "all",
}

_TOKEN_NORMALS = {
    "movies": "movie",
    "films": "movie",
    "actors": "actor",
    "directors": "director",
    "producers": "producer",
    "reviews": "review",
    "ratings": "rating",
    "products": "product",
    "orders": "order",
    "customers": "customer",
    "suppliers": "supplier",
    "tweets": "tweet",
    "users": "user",
    "followers": "follower",
    "hashtags": "hashtag",
}


@dataclass
class QuestionProfile:
    tokens: list[str] = field(default_factory=list)
    intents: list[str] = field(default_factory=list)
    literals: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tokens": self.tokens,
            "intents": self.intents,
            "literals": self.literals,
            "numbers": self.numbers,
        }


def tokenize_question(question: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]*|\d+", question.lower())
    normalized = [_TOKEN_NORMALS.get(token, token) for token in tokens]
    return [token for token in normalized if token not in _STOPWORDS]


def classify_question(question: str) -> list[str]:
    lowered = question.lower()
    intents: list[str] = []
    patterns = {
        "ranking_desc": r"\b(top|highest|largest|greatest|most)\b",
        "ranking_asc": r"\b(lowest|least|smallest|oldest|earliest)\b",
        "limit": r"\b(first|top|last|limit)\s+(?:the\s+)?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
        "aggregation_count": r"\b(count|how many|number of|frequency|most common)\b",
        "aggregation_avg": r"\b(avg|average|mean)\b",
        "aggregation_sum": r"\b(sum|total)\b",
        "filter_literal": r"'[^']+'|\"[^\"]+\"",
        "filter_numeric": r"\b(more than|less than|above|below|higher than|lower than|greater than|before|after|exactly)\b",
        "text_contains": r"\b(contain|contains|including|includes|word|words|summary)\b",
        "same_entity_multi_role": r"\b(same person|also|both|never)\b",
        "relationship_property": r"\b(role|roles|rating|summary|unit price|quantity|discount)\b",
        "date_or_year": r"\b(year|date|recent|newest|latest|released|created)\b",
        "nested_topk_filter": r"\b(include|includes|including|with|have|has|ordered|placed)\b.*\b(top|highest|most|largest|greatest|lowest|least|smallest|expensive|cheapest)\b",
    }
    for name, pattern in patterns.items():
        if re.search(pattern, lowered):
            intents.append(name)
    return intents


def profile_question(question: str) -> QuestionProfile:
    literals = [
        value
        for match in re.findall(r"'([^']+)'|\"([^\"]+)\"", question)
        for value in match
        if value
    ]
    return QuestionProfile(
        tokens=tokenize_question(question),
        intents=classify_question(question),
        literals=literals,
        numbers=re.findall(r"\b\d+(?:\.\d+)?\b", question),
    )


def summarize_questions(questions: list[str]) -> dict:
    token_counts: Counter[str] = Counter()
    intent_counts: Counter[str] = Counter()
    literal_count = 0
    number_count = 0
    for question in questions:
        profile = profile_question(question)
        token_counts.update(profile.tokens)
        intent_counts.update(profile.intents)
        literal_count += len(profile.literals)
        number_count += len(profile.numbers)
    return {
        "top_tokens": token_counts.most_common(50),
        "intent_counts": intent_counts.most_common(),
        "literal_questions": literal_count,
        "numeric_questions": number_count,
    }
