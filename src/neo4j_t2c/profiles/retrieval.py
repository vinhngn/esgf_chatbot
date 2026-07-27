from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from neo4j_t2c.planning.models import SemanticContract
from neo4j_t2c.profiles import load_profile_file
from neo4j_t2c.profiles.question_evidence import (
    extract_question_evidence,
    surface_terms,
)


def _text_tokens(text: str) -> set[str]:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text or "").replace("_", " ")
    return set(surface_terms(separated))


def _character_ngrams(term: str, width: int = 3) -> set[str]:
    normalized = f"^{term.casefold()}$"
    if len(normalized) <= width:
        return {normalized}
    return {
        normalized[index : index + width]
        for index in range(len(normalized) - width + 1)
    }


def _term_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    left_grams = _character_ngrams(left)
    right_grams = _character_ngrams(right)
    union = left_grams | right_grams
    if not union:
        return 0.0
    return len(left_grams & right_grams) / len(union)


def _document_frequencies(examples: list[dict]) -> Counter[str]:
    frequencies: Counter[str] = Counter()
    for example in examples:
        evidence = example.get("question_evidence", {})
        terms = set(evidence.get("terms", []))
        terms |= _text_tokens(example.get("question", ""))
        frequencies.update(terms)
    return frequencies


def _weighted_coverage(
    query_terms: set[str],
    document_terms: set[str],
    document_frequencies: Counter[str],
    document_count: int,
) -> float:
    if not query_terms or not document_terms:
        return 0.0
    total_weight = 0.0
    matched_weight = 0.0
    for term in query_terms:
        weight = math.log((document_count + 1) / (document_frequencies.get(term, 0) + 1)) + 1
        total_weight += weight
        similarity = max((_term_similarity(term, candidate) for candidate in document_terms), default=0)
        if similarity >= 0.5:
            matched_weight += weight * similarity
    return matched_weight / total_weight if total_weight else 0.0


def _cypher_tokens(cypher: str) -> set[str]:
    """Extract schema-ish tokens from a recipe Cypher without parsing it fully."""
    tokens: set[str] = set()
    for label in re.findall(r":`?([A-Za-z_][A-Za-z0-9_]*)`?", cypher or ""):
        tokens |= _text_tokens(label)
    for prop in re.findall(r"\.(`?[A-Za-z_][A-Za-z0-9_]*`?)", cypher or ""):
        tokens |= _text_tokens(prop.strip("`"))
    return tokens


def _return_tokens(cypher: str) -> set[str]:
    match = re.search(
        r"(?is)\bRETURN\b\s+(.*?)(?:\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)", cypher or ""
    )
    if not match:
        return set()
    return _cypher_tokens(match.group(1)) | _text_tokens(match.group(1))


def _example_score(
    example: dict,
    q_tokens: set[str],
    document_frequencies: Counter[str],
    document_count: int,
) -> float:
    example_evidence = example.get("question_evidence", {})
    example_tokens = set(example_evidence.get("terms", []))
    example_tokens |= _text_tokens(example.get("question", ""))
    cypher_tokens = _cypher_tokens(example.get("cypher", ""))
    return_tokens = _return_tokens(example.get("cypher", ""))
    token_score = _weighted_coverage(
        q_tokens, example_tokens, document_frequencies, document_count
    )
    cypher_score = _weighted_coverage(
        q_tokens, cypher_tokens, document_frequencies, document_count
    )
    return_score = _weighted_coverage(
        q_tokens, return_tokens, document_frequencies, document_count
    )
    return (
        token_score * 3.0
        + cypher_score * 1.5
        + return_score * 1.5
    )


def _extract_recipe_contract(question: str, recipe: dict) -> dict:
    cypher = recipe.get("cypher", "")
    labels = list(dict.fromkeys(re.findall(r"\([^)]*:`?([A-Za-z_][A-Za-z0-9_]*)`?", cypher)))
    relationships = list(
        dict.fromkeys(re.findall(r"\[[^\]]*:`?([A-Za-z_][A-Za-z0-9_]*)`?[^\]]*\]", cypher))
    )
    ordered_question_tokens = surface_terms(question)
    ranked_targets = []
    for label in labels:
        label_forms = _text_tokens(label)
        matching_positions = [
            index
            for index, token in enumerate(ordered_question_tokens)
            if max(
                (
                    _term_similarity(question_term, label_term)
                    for question_term in _text_tokens(token)
                    for label_term in label_forms
                ),
                default=0.0,
            )
            >= 0.5
        ]
        if matching_positions:
            ranked_targets.append((min(matching_positions), label))
    target_labels = [label for _, label in sorted(ranked_targets)]
    return_body_match = re.search(
        r"(?is)\bRETURN\b\s+(.*?)(?:\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)",
        cypher,
    )
    return_body = return_body_match.group(1).strip() if return_body_match else ""
    return_items = _split_return_items(return_body)
    return_has_distinct = bool(re.search(r"(?i)^\s*DISTINCT\b", return_body))
    normalized_return_items = [
        re.sub(r"(?i)^\s*DISTINCT\s+", "", item).strip() for item in return_items
    ]
    node_return_labels = []
    for item in normalized_return_items:
        bare_alias = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item)
        if bare_alias:
            alias_label = _alias_label_map(cypher).get(bare_alias.group(0))
            if alias_label:
                node_return_labels.append(alias_label)

    return {
        "labels": labels[:8],
        "relationships": relationships[:8],
        "target_labels": target_labels[:4],
        "primary_target_label": target_labels[0] if target_labels else "",
        "return_shape": recipe.get("shape", ""),
        "return_body": return_body,
        "return_items": normalized_return_items[:8],
        "return_has_distinct": return_has_distinct,
        "node_return_labels": node_return_labels[:4],
    }


def _split_return_items(return_body: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for char in return_body:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
            current.append(char)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth > 0:
            depth -= 1
        if char == "," and depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
        else:
            current.append(char)
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


def _alias_label_map(cypher: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for alias, label in re.findall(
        r"\(([A-Za-z_][A-Za-z0-9_]*)\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?",
        cypher or "",
    ):
        aliases[alias] = label
    return aliases


def _operation_from_cypher(cypher: str) -> str:
    if re.search(r"(?i)\b(count|avg|sum|min|max|collect)\s*\(", cypher):
        return "aggregate"
    if re.search(r"(?i)\bORDER\s+BY\b", cypher):
        return "rank"
    if re.search(r"(?i)\bWHERE\b", cypher):
        return "filter"
    return "retrieve"


def _build_query_plan_contract(question: str, selected_recipes: list[dict]) -> dict:
    if not selected_recipes:
        return {}
    primary = selected_recipes[0]
    contract = primary.get("contract", {})
    plan = {
        "target_label": contract.get("primary_target_label") or "",
        "primary_question": primary.get("question", ""),
        "candidate_labels": contract.get("labels", []),
        "required_relationships": contract.get("relationships", []),
        "scaffold_cypher": primary.get("cypher", ""),
        "return_contract": {
            "body": contract.get("return_body", ""),
            "items": contract.get("return_items", []),
            "distinct": contract.get("return_has_distinct", False),
            "node_return_labels": contract.get("node_return_labels", []),
        },
    }
    plan["expected_operation"] = _operation_from_cypher(primary.get("cypher", ""))
    return plan


class _RerankDecision(BaseModel):
    selected_row: int | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    reason: str = ""
    semantic_contract: SemanticContract | None = None


_RERANK_SYSTEM_PROMPT = """First express the current question as a schema-independent
semantic contract. Then select the learned query example whose semantic structure best
matches that contract. You are interpreting language and selecting evidence, not writing
Cypher or inventing Neo4j schema.

Compare:
1. target entity and relationship roles,
2. requested return projection,
3. retrieval vs aggregation vs ranking,
4. filters and literals as replaceable slots,
5. requested cardinality.

Surface wording may differ completely. Do not prefer a candidate merely because it
shares words. An extra returned property, aggregation, ordering, or limit that the
question did not request is a structural mismatch. Among otherwise equivalent
candidates, select the minimum sufficient contract. Candidate order and lexical
score are not evidence. Select null when no candidate is structurally reliable.
Return one JSON object only:
{
  "semantic_contract": {
    "target_concepts": ["domain concepts requested as output"],
    "operation": "retrieve|count|aggregate|rank|compare|path|exists",
    "metric": "quantity being measured or empty",
    "relations": ["semantic relationships between concepts"],
    "grouping": ["grouping concepts"],
    "projections": ["requested output concepts or attributes"],
    "constraints": [
      {"subject": "concept", "attribute": "attribute or empty",
       "operator": "semantic operator", "value": "literal or empty"}
    ],
    "order": "none|ascending|descending",
    "limit": null,
    "ambiguities": ["unresolved meanings only"],
    "confidence": 0.0
  },
  "selected_row": integer_or_null,
  "confidence": 0.0,
  "reason": "brief"
}"""


def _json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*", "", text or "", flags=re.IGNORECASE)
    cleaned = cleaned.replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Profile reranker did not return a JSON object")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Profile reranker returned a non-object JSON value")
    return payload


def _recipes_from_examples(question: str, examples: list[dict]) -> list[dict]:
    return [
        {
            "score": example.get("score", 0),
            "row": example.get("row"),
            "question": example.get("question", ""),
            "cypher": example.get("cypher", ""),
            "shape": example.get("shape", ""),
            "contract": _extract_recipe_contract(question, example),
        }
        for example in examples[:3]
    ]


def rerank_profile_context(
    question: str,
    context: dict,
    model: Any,
    *,
    minimum_confidence: float = 0.6,
) -> dict:
    """Cross-rank lexical candidates by semantic query structure."""
    candidates = list(context.get("selected_examples", []))

    payload = {
        "question": question,
        "candidates": [],
    }
    for candidate in candidates:
        contract = _extract_recipe_contract(question, candidate)
        payload["candidates"].append(
            {
                "row": candidate.get("row"),
                "question": candidate.get("question", ""),
                "cypher": candidate.get("cypher", ""),
                "operation": _operation_from_cypher(candidate.get("cypher", "")),
                "target_label": contract.get("primary_target_label", ""),
                "relationships": contract.get("relationships", []),
                "return_items": contract.get("return_items", []),
                "distinct": contract.get("return_has_distinct", False),
            }
        )
    try:
        response = model.invoke(
            [
                SystemMessage(content=_RERANK_SYSTEM_PROMPT),
                HumanMessage(
                    content=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                ),
            ]
        )
        decision = _RerankDecision.model_validate(
            _json_object(str(response.content))
        )
    except Exception as exc:
        unchanged = dict(context)
        unchanged["reranking"] = {
            "applied": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        return unchanged

    candidate_rows = {candidate.get("row") for candidate in candidates}
    selected_row = (
        decision.selected_row
        if decision.confidence >= minimum_confidence
        and decision.selected_row in candidate_rows
        else None
    )
    reranked = dict(context)
    reranked["reranking"] = {
        "applied": True,
        "selected_row": selected_row,
        "confidence": decision.confidence,
        "reason": decision.reason,
    }
    if decision.semantic_contract is not None:
        reranked["semantic_contract"] = decision.semantic_contract.model_dump(
            mode="json"
        )
    if selected_row is None:
        reranked["selected_recipes"] = []
        reranked["query_plan_contract"] = {}
        return reranked

    ordered = sorted(
        candidates,
        key=lambda candidate: candidate.get("row") != selected_row,
    )
    reranked["selected_examples"] = ordered
    reranked["selected_recipes"] = _recipes_from_examples(question, ordered)
    reranked["query_plan_contract"] = _build_query_plan_contract(
        question,
        reranked["selected_recipes"],
    )
    return reranked


def _path_score(path: dict, question_tokens: set[str]) -> float:
    signature = path.get("signature", "")
    path_tokens = _text_tokens(signature)
    if not path_tokens:
        return 0.0
    score = sum(
        max((_term_similarity(term, candidate) for candidate in path_tokens), default=0.0)
        for term in question_tokens
    )
    # Prefer the shortest path that covers the question terms; long cyclic
    # schema walks are kept as fallback context, not primary guidance.
    if score > 0:
        score -= min(float(path.get("hops", 1)) * 0.15, 0.6)
    return score


def load_profile(profile_path: str | Path) -> dict:
    return load_profile_file(profile_path)


def select_profile_context(question: str, profile: dict, *, top_k: int = 5) -> dict:
    """Select compact learned context for one question from an analyzer profile."""
    question_evidence = extract_question_evidence(question).to_dict()
    q_tokens = set(question_evidence.get("terms", [])) | _text_tokens(question)
    source_type = str(profile.get("source_type") or "")
    examples = (
        profile.get("examples", [])
        if source_type in {"benchmark_csv", "hybrid"}
        else []
    )
    document_frequencies = _document_frequencies(examples)
    document_count = max(1, len(examples))

    scored_examples = []
    for example in examples:
        score = _example_score(
            example,
            q_tokens,
            document_frequencies,
            document_count,
        )
        if score > 0:
            scored_examples.append((score, example))
    scored_examples.sort(key=lambda item: item[0], reverse=True)
    selected_recipes = [
        {
            "score": round(score, 4),
            "row": example["row"],
            "question": example["question"],
            "cypher": example["cypher"],
            "shape": example["cypher_shape"]["signature"],
            "contract": _extract_recipe_contract(
                question,
                {
                    "cypher": example["cypher"],
                    "shape": example["cypher_shape"]["signature"],
                },
            ),
        }
        for score, example in scored_examples[:3]
    ]
    query_plan_contract = _build_query_plan_contract(question, selected_recipes)

    return {
        "question_evidence": question_evidence,
        "schema_profile_summary": (
            profile.get("schema_profile", {}).get("summary", {})
            if isinstance(profile.get("schema_profile"), dict)
            else {}
        ),
        "planner_profile": (
            profile.get("planner_profile", {})
            if isinstance(profile.get("planner_profile"), dict)
            else {}
        ),
        "vector_indexes": (
            profile.get("schema_profile", {}).get("vector_indexes", [])
            if isinstance(profile.get("schema_profile"), dict)
            else []
        ),
        "schema_paths": sorted(
            [
                path
                for path in (
                    profile.get("schema_profile", {}).get("paths", [])
                    if isinstance(profile.get("schema_profile"), dict)
                    else []
                )
                if _path_score(path, q_tokens) > 0
            ],
            key=lambda path: (
                -_path_score(path, q_tokens),
                path.get("hops", 0),
                path.get("signature", ""),
            ),
        )[:20],
        "query_recipe_profile": profile.get("query_recipe_profile", {}),
        "value_profile": profile.get("value_profile", {}),
        "selected_examples": [
            {
                "score": round(score, 4),
                "row": example["row"],
                "question": example["question"],
                "cypher": example["cypher"],
                "shape": example["cypher_shape"]["signature"],
            }
            for score, example in scored_examples[:top_k]
        ],
        "selected_recipes": selected_recipes,
        "query_plan_contract": query_plan_contract,
    }


def format_profile_context(context: dict) -> str:
    """Format selected learned context as a compact prompt block."""
    lines = ["=== LEARNED QUERY CONTRACT ==="]
    if context.get("query_plan_contract"):
        lines.append("QUERY PLAN CONTRACT JSON:")
        lines.append(
            json.dumps(context["query_plan_contract"], ensure_ascii=True, separators=(",", ":"))
        )
    if context.get("schema_paths"):
        lines.append("QUESTION-RELEVANT SCHEMA PATHS:")
        for path in context["schema_paths"][:3]:
            lines.append(f"- {path.get('signature')}")
    return "\n".join(lines) if len(lines) > 1 else ""
