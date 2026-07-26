"""Lossless question evidence extraction without semantic word lists."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

_TERM_PATTERN = re.compile(r"[^\W_]+(?:_[^\W_]+)*", flags=re.UNICODE)
_NUMBER_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\b")


@dataclass
class QuestionEvidence:
    """Surface evidence retained for retrieval; semantics remain the LLM's job."""

    terms: list[str] = field(default_factory=list)
    literals: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "terms": self.terms,
            "literals": self.literals,
            "numbers": self.numbers,
        }


def surface_terms(text: str) -> list[str]:
    """Return case-folded terms without stopword removal or domain normalization."""
    return [term.casefold() for term in _TERM_PATTERN.findall(text or "")]


def extract_question_evidence(question: str) -> QuestionEvidence:
    literals = [
        value
        for match in re.findall(r"'([^']+)'|\"([^\"]+)\"", question or "")
        for value in match
        if value
    ]
    return QuestionEvidence(
        terms=surface_terms(question),
        literals=literals,
        numbers=_NUMBER_PATTERN.findall(question or ""),
    )


def summarize_question_evidence(questions: list[str]) -> dict:
    term_counts: Counter[str] = Counter()
    literal_count = 0
    number_count = 0
    for question in questions:
        evidence = extract_question_evidence(question)
        term_counts.update(evidence.terms)
        literal_count += len(evidence.literals)
        number_count += len(evidence.numbers)
    return {
        "top_terms": term_counts.most_common(50),
        "literal_count": literal_count,
        "number_count": number_count,
    }
