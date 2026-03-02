"""
Database Router -- auto-detects which Neo4j database a question belongs to.

Algorithm: Schema Superset Aware Routing (SSAR)

Key insight: Some databases are SUPERSETS of others (e.g., recommendations
contains Movie+Person+Actor+Director+Genre+User while movies only has
Movie+Person). When a question matches both, prefer the superset.

Phases:
  1. Build keyword index from schema (labels, rels, properties)
  2. Detect superset relationships between DBs
  3. Score question tokens against each DB
  4. If top-2 DBs have superset relationship AND shared keywords dominate,
     prefer the superset

Public API:
    route_question(question) -> str
    route_question_with_scores(question) -> dict
    get_db_config(db_name) -> dict
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

from templates.schema_loader import load_schema, get_available_databases

logger = logging.getLogger(__name__)

_DEMO_URI = "neo4j+s://demo.neo4jlabs.com"


def get_db_config(db_name: str) -> dict[str, str]:
    """Return Neo4j connection config for demo databases."""
    return {
        "url": _DEMO_URI,
        "username": db_name,
        "password": db_name,
        "database": db_name,
    }


# ---------------------------------------------------------------------------
# Schema structures
# ---------------------------------------------------------------------------

class _DBSchema:
    """Processed schema for one database."""
    __slots__ = ("name", "labels", "rels", "props", "keywords",
                 "exclusive_kws", "supersets")

    def __init__(self, name: str):
        self.name = name
        self.labels: set[str] = set()      # node labels (lowercase)
        self.rels: set[str] = set()        # relationship types (lowercase)
        self.props: set[str] = set()       # property names (lowercase)
        self.keywords: set[str] = set()    # all searchable keywords
        self.exclusive_kws: set[str] = set()
        self.supersets: list[str] = []     # DBs that this is a superset of


_db_schemas: dict[str, _DBSchema] | None = None
_shared_keywords: set[str] = set()

# English stop words that should NEVER be schema keywords
_STOP_WORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "any",
    "can", "had", "has", "her", "his", "its", "may", "new", "now",
    "old", "one", "our", "out", "own", "say", "she", "too", "use",
    "was", "who", "how", "man", "did", "get", "got", "let", "put",
    "set", "top", "two", "way", "yet", "also", "back", "been",
    "call", "come", "each", "find", "from", "give", "good", "have",
    "help", "here", "high", "just", "know", "last", "like", "long",
    "look", "made", "make", "many", "most", "much", "must", "name",
    "next", "only", "over", "part", "some", "such", "sure", "take",
    "tell", "than", "that", "them", "then", "they", "this", "time",
    "very", "want", "well", "were", "what", "when", "will", "with",
    "word", "work", "year", "your", "about", "after", "being",
    "could", "every", "first", "found", "great", "house", "large",
    "later", "never", "other", "place", "point", "right", "small",
    "still", "their", "there", "these", "think", "those", "three",
    "under", "water", "where", "which", "world", "would", "write",
    "number", "people", "should", "before",
    # Common query words
    "list", "show", "what", "which", "find", "give",
}


def _is_noisy(val: str) -> bool:
    """Filter noisy sample values."""
    val = val.strip()
    if len(val) < 4 or len(val) > 60:
        return True
    if re.match(r'^[\d.\-E+]+$', val, re.IGNORECASE):
        return True
    if re.match(r'^\d{4}-\d{2}', val):
        return True
    if val.startswith('http') or val.startswith('www'):
        return True
    if val.lower() in _STOP_WORDS:
        return True
    return False


def _build_schemas() -> tuple[dict[str, _DBSchema], set[str]]:
    dbs = get_available_databases()
    schemas: dict[str, _DBSchema] = {}

    for db_name in dbs:
        raw = load_schema(db_name)
        s = _DBSchema(db_name)

        # Node labels
        for label in raw.get("node_labels", []):
            if label.startswith("_Bloom"):
                continue
            s.labels.add(label.lower())
            s.keywords.add(label.lower())
            for part in re.findall(r'[A-Z][a-z]+', label):
                if len(part) >= 3:
                    s.labels.add(part.lower())
                    s.keywords.add(part.lower())

        # Relationship types
        for rel in raw.get("relationship_types", []):
            if rel.startswith("_Bloom"):
                continue
            s.rels.add(rel.lower())
            s.keywords.add(rel.lower())
            for part in rel.split("_"):
                if len(part) >= 3:
                    s.rels.add(part.lower())
                    s.keywords.add(part.lower())

        # Property names (from nodes)
        for label_props in raw.get("node_properties", {}).values():
            for p in label_props:
                pname = p.get("property", "").lower()
                if len(pname) >= 3:
                    s.props.add(pname)
                    s.keywords.add(pname)

        # Property names (from relationships)
        for rel_props in raw.get("relationship_properties", {}).values():
            for p in rel_props:
                pname = p.get("property", "").lower()
                if len(pname) >= 3:
                    s.props.add(pname)
                    s.keywords.add(pname)

        # Sample data (filtered)
        for label_name, samples in raw.get("sample_data", {}).items():
            for sample in samples[:3]:
                for key, val in sample.items():
                    if isinstance(val, str) and not _is_noisy(val):
                        s.keywords.add(val.lower())

        schemas[db_name] = s

    # --- Detect superset relationships ---
    for a_name, a in schemas.items():
        for b_name, b in schemas.items():
            if a_name == b_name:
                continue
            # a is superset of b if a.labels contains all of b.labels
            if b.labels and b.labels.issubset(a.labels):
                a.supersets.append(b_name)
                logger.info(
                    "[Router] '%s' is a SUPERSET of '%s' "
                    "(labels %s contains %s)",
                    a_name, b_name, sorted(a.labels), sorted(b.labels),
                )

    # --- Classify exclusive vs shared ---
    kw_to_dbs: dict[str, list[str]] = defaultdict(list)
    for db_name, s in schemas.items():
        for kw in s.keywords:
            kw_to_dbs[kw].append(db_name)

    shared: set[str] = set()
    for kw, containing in kw_to_dbs.items():
        if len(containing) == 1:
            schemas[containing[0]].exclusive_kws.add(kw)
        else:
            shared.add(kw)

    for db_name, s in schemas.items():
        logger.info(
            "[Router] '%s': %d kws, %d exclusive, %d labels, %d rels, "
            "supersets=%s",
            db_name, len(s.keywords), len(s.exclusive_kws),
            len(s.labels), len(s.rels), s.supersets or "none",
        )

    return schemas, shared


def _get_schemas():
    global _db_schemas, _shared_keywords
    if _db_schemas is None:
        _db_schemas, _shared_keywords = _build_schemas()
    return _db_schemas, _shared_keywords


def _tokenize(text: str) -> set[str]:
    words = re.findall(r'[a-zA-Z0-9_]+', text.lower())
    return {w for w in words if len(w) >= 3 and w not in _STOP_WORDS}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

_EXCLUSIVE_WEIGHT = 10.0
_LABEL_WEIGHT = 5.0
_REL_WEIGHT = 3.0
_SHARED_WEIGHT = 1.0


def _score_db(tokens: set[str], s: _DBSchema, shared: set[str]) -> dict:
    matched = tokens & s.keywords
    excl = matched & s.exclusive_kws
    shrd = matched & shared
    label_match = matched & s.labels
    rel_match = matched & s.rels

    score = (
        len(excl) * _EXCLUSIVE_WEIGHT
        + len(shrd) * _SHARED_WEIGHT
        + len(label_match) * _LABEL_WEIGHT
        + len(rel_match) * _REL_WEIGHT
    )

    return {
        "database": s.name,
        "score": round(score, 2),
        "exclusive": sorted(excl),
        "shared": sorted(shrd),
        "labels": sorted(label_match),
        "rels": sorted(rel_match),
    }


def _select_best(results: list[dict], schemas: dict[str, _DBSchema]) -> str:
    """
    Select best DB with superset-aware tiebreaking.

    If the top-2 DBs have a superset relationship, prefer the superset
    because it can answer anything the subset can + more.
    """
    if not results:
        return "movies"

    results.sort(key=lambda x: -x["score"])
    best = results[0]
    second = results[1] if len(results) > 1 else None

    if not second or best["score"] == 0:
        # No keyword matches -- prefer DB that is superset of most others
        # (recommendations covers movies, so it's a better default)
        richest = max(
            schemas.values(),
            key=lambda s: (len(s.supersets), len(s.labels) + len(s.rels))
        )
        return richest.name

    # Check superset relationships across ALL results
    # If any DB in results is a superset of the current best,
    # and has a reasonable score, prefer the superset
    best_db_obj = schemas.get(best["database"])
    for candidate in results[1:]:
        cand_db = schemas.get(candidate["database"])
        if not cand_db:
            continue
        # candidate is superset of best?
        if best["database"] in cand_db.supersets:
            if candidate["score"] >= best["score"] * 0.2:
                logger.info(
                    "[Router] Superset override: '%s' (%.1f) -> '%s' (%.1f)",
                    best["database"], best["score"],
                    candidate["database"], candidate["score"],
                )
                return candidate["database"]

    # If best is a superset of any other, that's fine -- keep best
    if best_db_obj and best_db_obj.supersets:
        return best["database"]

    # No superset relationship -- handle ties
    if second and best["score"] == second["score"]:
        tied = [r for r in results if r["score"] == best["score"]]
        winner = max(
            tied,
            key=lambda r: (
                len(schemas[r["database"]].labels)
                + len(schemas[r["database"]].rels)
            )
        )
        return winner["database"]

    return best["database"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def route_question(question: str) -> str:
    """Route a question to the most relevant database."""
    schemas, shared = _get_schemas()
    tokens = _tokenize(question)

    results = [_score_db(tokens, s, shared) for s in schemas.values()]
    selected = _select_best(results, schemas)

    logger.info(
        "[Router] '%s' -> '%s'. Scores: %s",
        question[:60], selected,
        {r["database"]: r["score"] for r in sorted(results, key=lambda x: -x["score"])},
    )

    return selected


def route_question_with_scores(question: str) -> dict[str, Any]:
    """Route question and return detailed scoring info."""
    schemas, shared = _get_schemas()
    tokens = _tokenize(question)

    results = [_score_db(tokens, s, shared) for s in schemas.values()]
    selected = _select_best(results, schemas)
    results.sort(key=lambda x: -x["score"])

    return {
        "selected": selected,
        "scores": results,
        "question_tokens": sorted(tokens),
    }
