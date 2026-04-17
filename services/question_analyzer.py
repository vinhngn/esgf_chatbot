"""
Question Analyzer — CODE-based question understanding.

Parses the user question to extract structured intent BEFORE sending to LLM.
Uses knowledge base to resolve ambiguities deterministically.

Extracts:
  1. Numbers + operators (above 90 → op: >, val: 90)
  2. Property references resolved against knowledge (rating → [:REVIEWED].rating)
  3. Entity references resolved against knowledge (Neo4j → Me.screen_name)
  4. Aggregation intent (how many → COUNT, top N → ORDER BY + LIMIT)
  5. Return hints (filter property → include in RETURN)

Output: enriched question string that LLM can translate more accurately.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def analyze_and_enrich(question: str, knowledge: dict) -> tuple[str, dict]:
    """
    Analyze question → enriched version + structured intents.
    Returns (enriched_question, intents_dict).
    """
    intents: dict = {
        "entities": [],
        "mentioned_properties": set(),
        "matched_labels": set(),
        "matched_rels": set(),
        "has_filter": False,
        "has_aggregation": False,
        "has_multi_condition": False,
        "has_limit": False,
        "has_count_pattern": False,
    }

    if not knowledge:
        return question, intents

    annotations: list[str] = []

    # 1. Extract and resolve numeric filters
    filters = _extract_filters(question)
    if filters:
        intents["has_filter"] = True
    for f in filters:
        if f["property_hint"]:
            intents["mentioned_properties"].add(f["property_hint"])
        resolved = _resolve_property(f["property_hint"], knowledge)
        if resolved:
            loc, label, prop, prop_type = resolved
            annotations.append(
                f"Filter: '{f['property_hint']}' {f['op']} {f['value']} "
                f"→ {loc} {label}.{prop} (type: {prop_type})"
            )

    # 2. Resolve entity references
    entities = _extract_entities(question)
    for ent in entities:
        resolved = _resolve_entity(ent, knowledge)
        if resolved:
            label, prop, exact_val = resolved
            intents["entities"].append({"value": ent, "label": label, "property": prop, "exact_value": exact_val})
            intents["matched_labels"].add(label)
            annotations.append(f"Entity: '{ent}' → :{label} {{{prop}: '{exact_val}'}}")

    # 3. Detect aggregation intent
    agg = _detect_aggregation(question)
    if agg:
        intents["has_aggregation"] = True
        if "LIMIT" in agg:
            intents["has_limit"] = True
        annotations.append(f"Aggregation: {agg}")

    # 4. Detect return hints from filters
    return_hints = _detect_return_hints(question, filters, knowledge)
    if return_hints:
        annotations.append(f"Include in RETURN: {', '.join(return_hints)}")

    # 5. Detect multi-condition patterns (X who Y AND Z)
    multi_hop = _detect_multi_condition(question)
    if multi_hop:
        intents["has_multi_condition"] = True
        annotations.append(multi_hop)

    # 6. Count pattern
    q_lower = question.lower()
    if "number of" in q_lower and not intents["has_aggregation"]:
        intents["has_count_pattern"] = True

    # 7. Detect property mentions even without explicit filter operators
    # "released year of 2008" → released is a property, 2008 is a value
    # "most votes" → votes is a property
    extra_props = _detect_property_mentions(question, knowledge)
    for prop in extra_props:
        intents["mentioned_properties"].add(prop)
        if not intents["has_filter"]:
            # Check if there's a number nearby → implicit filter
            if re.search(r'\b\d{4}\b', question) or re.search(r'\b\d+\b', question):
                intents["has_filter"] = True

    if not annotations:
        return question, intents

    enriched = question + "\n\n[ANALYSIS]\n" + "\n".join(annotations)
    logger.info("[QuestionAnalyzer] Enriched with %d annotations", len(annotations))
    return enriched, intents


# ---------------------------------------------------------------------------
# 1. Numeric filter extraction
# ---------------------------------------------------------------------------

_FILTER_PATTERNS = [
    # "above/over/more than/greater than N"
    (r'(?:above|over|more\s+than|greater\s+than|exceeding|higher\s+than)\s+(\$?[\d,]+\.?\d*)', '>'),
    # "below/under/less than/fewer than N"
    (r'(?:below|under|less\s+than|fewer\s+than|lower\s+than)\s+(\$?[\d,]+\.?\d*)', '<'),
    # "at least N"
    (r'(?:at\s+least|minimum)\s+(\$?[\d,]+\.?\d*)', '>='),
    # "at most N"
    (r'(?:at\s+most|maximum|no\s+more\s+than)\s+(\$?[\d,]+\.?\d*)', '<='),
    # "exactly N" or "equal to N"
    (r'(?:exactly|equal\s+to)\s+(\$?[\d,]+\.?\d*)', '='),
    # "in YYYY" (year)
    (r'\bin\s+((?:19|20)\d{2})\b', '='),
    # "released/born/year YYYY"
    (r'(?:released|born|year)\s+(?:in\s+)?(\d{4})', '='),
    # "after YYYY"
    (r'(?:after|since|from)\s+((?:19|20)\d{2})', '>'),
    # "before YYYY"
    (r'(?:before|until|prior\s+to)\s+((?:19|20)\d{2})', '<'),
]


def _extract_filters(question: str) -> list[dict]:
    """Extract numeric filter conditions from question."""
    q_lower = question.lower()
    filters = []

    for pattern, op in _FILTER_PATTERNS:
        for m in re.finditer(pattern, q_lower):
            raw_val = m.group(1).replace('$', '').replace(',', '')
            try:
                value = int(raw_val) if '.' not in raw_val else float(raw_val)
            except ValueError:
                continue

            # Find what property this filter refers to
            # Look at words BEFORE the number for property hints
            before = q_lower[:m.start()].strip().split()
            property_hint = _guess_property_from_context(before, q_lower)

            filters.append({
                "op": op,
                "value": value,
                "raw": m.group(0),
                "property_hint": property_hint,
            })

    return filters


def _guess_property_from_context(words_before: list[str], full_question: str) -> str:
    """Guess which property a numeric filter refers to."""
    # Common property keywords
    prop_keywords = {
        'rating': 'rating', 'ratings': 'rating', 'rated': 'rating',
        'vote': 'votes', 'votes': 'votes', 'voted': 'votes',
        'favorite': 'favorites', 'favorites': 'favorites', 'likes': 'favorites',
        'follower': 'followers', 'followers': 'followers',
        'following': 'following',
        'budget': 'budget', 'revenue': 'revenue',
        'runtime': 'runtime', 'duration': 'runtime',
        'year': 'year', 'released': 'released', 'born': 'born',
        'price': 'unitPrice', 'unitprice': 'unitPrice',
        'stock': 'unitsInStock', 'quantity': 'quantity',
        'discount': 'discount', 'freight': 'freight',
        'score': 'score', 'similarity': 'score',
        'imdbrating': 'imdbRating', 'imdb': 'imdbRating',
    }

    # Check last few words before the number
    for w in reversed(words_before[-5:]):
        w_clean = w.strip('.,;:!?()').lower()
        if w_clean in prop_keywords:
            return prop_keywords[w_clean]

    # Check full question for property keywords
    for keyword, prop in prop_keywords.items():
        if keyword in full_question:
            return prop

    return ""


# ---------------------------------------------------------------------------
# 2. Property resolution against knowledge
# ---------------------------------------------------------------------------

def _resolve_property(prop_hint: str, knowledge: dict) -> tuple | None:
    """
    Resolve a property hint to its exact location using knowledge base.
    Returns: (location, label_or_rel, property_name, type) or None
    """
    if not prop_hint:
        return None

    hint_lower = prop_hint.lower()

    # Check relationship properties first (more specific)
    for rel, props in knowledge.get("relationship_properties", {}).items():
        for prop_name, info in props.items():
            if prop_name.lower() == hint_lower:
                return ("REL", f"[:{rel}]", prop_name, info.get("type", "?"))

    # Check node properties
    for label, props in knowledge.get("node_properties", {}).items():
        for prop_name, info in props.items():
            if prop_name.lower() == hint_lower:
                return ("NODE", f":{label}", prop_name, info.get("type", "?"))

    return None


# ---------------------------------------------------------------------------
# 3. Entity resolution
# ---------------------------------------------------------------------------

def _extract_entities(question: str) -> list[str]:
    """Extract potential entity names from question."""
    entities = []
    # Quoted strings
    for m in re.finditer(r"""['"]([^'"]+)['"]""", question):
        entities.append(m.group(1))
    return entities


def _resolve_entity(value: str, knowledge: dict) -> tuple | None:
    """
    Resolve entity value to (label, property, exact_value) using knowledge samples.
    Prefer more specific labels (fewer nodes).
    """
    val_lower = value.lower()
    node_props = knowledge.get("node_properties", {})
    node_counts = knowledge.get("node_counts", {})

    candidates = []
    for label, props in node_props.items():
        for prop_name, info in props.items():
            sample = info.get("sample", "")
            if sample and str(sample).lower() == val_lower:
                count = node_counts.get(label, 1)  # rare labels default low
                candidates.append((label, prop_name, str(sample), count))

    if not candidates:
        return None

    # Prefer label with fewest nodes (most specific)
    candidates.sort(key=lambda x: x[3])
    best = candidates[0]
    return (best[0], best[1], best[2])


# ---------------------------------------------------------------------------
# 4. Aggregation detection
# ---------------------------------------------------------------------------

def _detect_aggregation(question: str) -> str | None:
    """Detect aggregation intent from question."""
    q = question.lower()

    # Top N / First N
    m = re.search(r'(?:top|first|last)\s+(\d+)', q)
    if m:
        return f"ORDER BY ... DESC/ASC LIMIT {m.group(1)}"

    if 'how many' in q or 'count' in q or 'number of' in q:
        return "COUNT"
    if 'average' in q or 'avg' in q or 'mean' in q:
        return "AVG"
    if 'total' in q or 'sum' in q:
        return "SUM"
    if 'most' in q or 'highest' in q or 'maximum' in q:
        return "ORDER BY ... DESC LIMIT 1"
    if 'least' in q or 'lowest' in q or 'minimum' in q or 'fewest' in q:
        return "ORDER BY ... ASC LIMIT 1"

    return None


# ---------------------------------------------------------------------------
# 5. Return hints
# ---------------------------------------------------------------------------

def _detect_return_hints(
    question: str,
    filters: list[dict],
    knowledge: dict,
) -> list[str]:
    """Detect which properties should be in RETURN based on filters and question."""
    hints = []

    # Rule: if filtering by a property, include it in RETURN
    for f in filters:
        resolved = _resolve_property(f["property_hint"], knowledge)
        if resolved:
            loc, label_or_rel, prop, _ = resolved
            if loc == "REL":
                hints.append(f"relationship_var.{prop}")
            else:
                hints.append(f"node_var.{prop}")

    return hints


# ---------------------------------------------------------------------------
# 6. Multi-condition detection
# ---------------------------------------------------------------------------

def _detect_multi_condition(question: str) -> str | None:
    """
    Detect "X who Y AND Z" patterns where the same entity does multiple things.
    These need separate MATCH clauses with shared variable.
    """
    q = question.lower()

    # Patterns: "who/that/which ... and ... " or "who/that/which ... and have ..."
    multi_patterns = [
        r'(?:users?|people|persons?|actors?|directors?)\s+who\s+.+?\s+and\s+(?:have\s+)?(?:also\s+)?',
        r'(?:users?|people|persons?)\s+that\s+.+?\s+and\s+(?:have\s+)?',
        r'(?:tweets?|movies?|films?)\s+(?:that|which)\s+.+?\s+and\s+(?:have\s+)?(?:also\s+)?',
    ]

    for pattern in multi_patterns:
        if re.search(pattern, q):
            return (
                "MULTI-CONDITION: The same entity has multiple relationships. "
                "Use SEPARATE MATCH clauses with the SAME variable, not one long chain. "
                "Example: MATCH (u)-[:R1]->(a) MATCH (u)-[:R2]->(b)"
            )

    return None


# ---------------------------------------------------------------------------
# 7. Property mention detection
# ---------------------------------------------------------------------------

def _detect_property_mentions(question: str, knowledge: dict) -> set[str]:
    """Detect property names mentioned in question, even without filter operators."""
    q_lower = question.lower()
    found = set()

    node_props = knowledge.get("node_properties", {})
    rel_props = knowledge.get("relationship_properties", {})

    # Check all known property names
    all_props = set()
    for label, props in node_props.items():
        all_props.update(props.keys())
    for rel, props in rel_props.items():
        all_props.update(props.keys())

    for prop in all_props:
        prop_lower = prop.lower()
        # Direct mention
        if prop_lower in q_lower:
            found.add(prop)
        # camelCase split: "unitPrice" → "unit price"
        import re as _re
        split = _re.sub(r'([a-z])([A-Z])', r'\1 \2', prop).lower()
        if split != prop_lower and split in q_lower:
            found.add(prop)

    # Common aliases
    aliases = {
        'vote': 'votes', 'rating': 'rating', 'release': 'released',
        'born': 'born', 'title': 'title', 'name': 'name',
        'favorite': 'favorites', 'follower': 'followers',
        'budget': 'budget', 'revenue': 'revenue', 'runtime': 'runtime',
        'year': 'year', 'price': 'unitPrice', 'stock': 'unitsInStock',
    }
    for alias, prop in aliases.items():
        if alias in q_lower and prop in all_props:
            found.add(prop)

    return found
