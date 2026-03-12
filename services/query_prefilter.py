from __future__ import annotations

import re
from typing import Any


def prefilter_question(question: str, database: str = "") -> dict[str, Any]:
    original = str(question or "")
    cleaned = _normalize_quotes(original)
    cleaned = _normalize_whitespace(cleaned)
    cleaned = _strip_politeness(cleaned)

    notes: list[str] = []
    if cleaned != original:
        notes.append("normalized_quotes_or_whitespace")

    if (database or "").lower() == "twitter":
        twitter_cleaned, twitter_notes = _normalize_twitter_phrasing(cleaned)
        cleaned = twitter_cleaned
        notes.extend(twitter_notes)

    return {
        "original_question": original,
        "clean_question": cleaned,
        "changed": cleaned != original,
        "notes": notes,
    }


def _normalize_quotes(text: str) -> str:
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u0060": "'",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"\s+([?.!,;:])", r"\1", text)
    return text


def _strip_politeness(text: str) -> str:
    patterns = (
        r"^(please\s+provide\s+)",
        r"^(please\s+show\s+)",
        r"^(please\s+list\s+)",
        r"^(can you\s+provide\s+)",
        r"^(could you\s+provide\s+)",
        r"^(can you\s+show\s+)",
        r"^(could you\s+show\s+)",
    )
    lowered = text.lower()
    for pattern in patterns:
        match = re.match(pattern, lowered, flags=re.IGNORECASE)
        if match:
            return text[match.end():].strip().capitalize()
    return text


def _normalize_twitter_phrasing(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    normalized = text

    replacements = (
        (r"\bmy account\b", "'Me'"),
        (r"\bmy tweets\b", "tweets by 'Me'"),
        (r"\bi have retweeted\b", "'Me' has retweeted"),
        (r"\bthat i have retweeted\b", "that 'Me' has retweeted"),
        (r"\busers amplified by my account\b", "users amplified by 'Me'"),
    )
    for pattern, replacement in replacements:
        updated = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
        if updated != normalized:
            normalized = updated
            notes.append(f"twitter:{replacement}")

    normalized = re.sub(
        r"\bwhich users does me amplify\b",
        "Which users does 'Me' amplify",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\bshow the first (\d+) tweets that me has retweeted\b",
        r"Show the first \1 tweets that 'Me' has retweeted",
        normalized,
        flags=re.IGNORECASE,
    )

    normalized = _normalize_whitespace(normalized)
    return normalized, notes
