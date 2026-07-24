"""Fast profile-recipe lookup used before LLM synthesis."""

from __future__ import annotations

import os
import re

from config import get_settings
from services.profile_analyzer.context import load_profile
from services.profile_analyzer.store import profile_path
from services.text2cypher.query_plan import _query_plan_from_context


def _normalise_question_for_match(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _profile_first_enabled() -> bool:
    return os.getenv("T2C_PROFILE_FIRST", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _profile_first_cypher(question: str, learned_context: str) -> str:
    if not _profile_first_enabled():
        return ""

    plan = _query_plan_from_context(learned_context)
    if not isinstance(plan, dict):
        return ""

    scaffold = (plan.get("scaffold_cypher") or "").strip()
    primary_question = (plan.get("primary_question") or "").strip()
    if not scaffold or not primary_question:
        return ""
    if _normalise_question_for_match(question) != _normalise_question_for_match(primary_question):
        return ""
    return scaffold


def _exact_profile_cypher(question: str) -> str:
    if not _profile_first_enabled():
        return ""

    path = profile_path(get_settings().profile_database_name)
    if not path.exists():
        return ""

    target = _normalise_question_for_match(question)
    if not target:
        return ""

    try:
        profile = load_profile(path)
    except Exception:
        return ""

    for example in profile.get("examples", []):
        if _normalise_question_for_match(example.get("question", "")) == target:
            return (example.get("cypher") or "").strip()
    return ""
