from __future__ import annotations

import json
import re
from pathlib import Path

from services.profile_analyzer.question_profile import profile_question


def _overlap_score(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _text_tokens(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    expanded = set(tokens)
    for token in tokens:
        if token.endswith("ies") and len(token) > 3:
            expanded.add(token[:-3] + "y")
        if token.endswith("s") and len(token) > 3:
            expanded.add(token[:-1])
        if token.endswith("ed") and len(token) > 4:
            expanded.add(token[:-2])
    return expanded


def _cypher_tokens(cypher: str) -> set[str]:
    """Extract schema-ish tokens from a recipe Cypher without parsing it fully."""
    tokens: set[str] = set()
    for label in re.findall(r":`?([A-Za-z_][A-Za-z0-9_]*)`?", cypher or ""):
        tokens |= _text_tokens(label)
    for prop in re.findall(r"\.(`?[A-Za-z_][A-Za-z0-9_]*`?)", cypher or ""):
        tokens |= _text_tokens(prop.strip("`"))
    return tokens


def _return_tokens(cypher: str) -> set[str]:
    match = re.search(r"(?is)\bRETURN\b\s+(.*?)(?:\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)", cypher or "")
    if not match:
        return set()
    return _cypher_tokens(match.group(1)) | _text_tokens(match.group(1))


def _example_score(example: dict, q_profile: dict, q_tokens: set[str], motif_scores: dict[str, float]) -> float:
    example_profile = example.get("question_profile", {})
    example_tokens = set(example_profile.get("tokens", [])) | _text_tokens(example.get("question", ""))
    cypher_tokens = _cypher_tokens(example.get("cypher", ""))
    return_tokens = _return_tokens(example.get("cypher", ""))
    token_score = _overlap_score(list(q_tokens), list(example_tokens))
    cypher_score = _overlap_score(list(q_tokens), list(cypher_tokens))
    return_score = _overlap_score(list(q_tokens), list(return_tokens))
    intent_score = _overlap_score(q_profile["intents"], example_profile.get("intents", []))
    motif_bonus = 0.0
    for motif in example.get("cypher_shape", {}).get("path_motifs", []):
        motif_bonus += motif_scores.get(motif, 0.0)
    exact_schema_hits = len(q_tokens & cypher_tokens)
    return (
        token_score * 2.0
        + cypher_score * 2.5
        + return_score * 1.4
        + intent_score
        + min(motif_bonus / 20.0, 1.0)
        + min(exact_schema_hits * 0.12, 0.72)
    )


def _extract_recipe_contract(question: str, recipe: dict) -> dict:
    cypher = recipe.get("cypher", "")
    labels = list(dict.fromkeys(
        re.findall(r"\([^)]*:`?([A-Za-z_][A-Za-z0-9_]*)`?", cypher)
    ))
    relationships = list(dict.fromkeys(
        re.findall(r"\[[^\]]*:`?([A-Za-z_][A-Za-z0-9_]*)`?[^\]]*\]", cypher)
    ))
    ordered_question_tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", (question or "").lower())
        if token
    ]
    ranked_targets = []
    for label in labels:
        label_forms = _text_tokens(label)
        matching_positions = [
            index for index, token in enumerate(ordered_question_tokens)
            if _text_tokens(token) & label_forms
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
        re.sub(r"(?i)^\s*DISTINCT\s+", "", item).strip()
        for item in return_items
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


def _build_query_plan_contract(question: str, selected_recipes: list[dict], q_profile: dict) -> dict:
    if not selected_recipes:
        return {}
    primary = selected_recipes[0]
    contract = primary.get("contract", {})
    intents = q_profile.get("intents", [])
    plan = {
        "target_label": contract.get("primary_target_label") or "",
        "primary_question": primary.get("question", ""),
        "primary_score": primary.get("score", 0.0),
        "candidate_labels": contract.get("labels", []),
        "required_relationships": contract.get("relationships", []),
        "scaffold_cypher": primary.get("cypher", ""),
        "question_intents": intents,
        "must_preserve": [
            "Use the selected scaffold traversal unless a shorter equivalent path is required by the question.",
            "Return the target_label identity when target_label is present.",
        ],
        "adaptable_slots": [
            "literal filters",
            "numeric/date filters",
            "aggregation expression",
            "ORDER BY direction",
            "LIMIT",
            "RETURN aliases",
        ],
        "return_policy": "For ranking/count questions, return target identity plus metric. For entity-list questions, return target identity/properties only.",
        "return_contract": {
            "body": contract.get("return_body", ""),
            "items": contract.get("return_items", []),
            "distinct": contract.get("return_has_distinct", False),
            "node_return_labels": contract.get("node_return_labels", []),
        },
    }
    if "nested_topk_filter" in intents:
        plan["expected_operation"] = "nested_topk_filter"
        plan["must_preserve"].append(
            "For nested top-k filters, compute the inner top-k entity set first, collect stable ids, then match the outer target entity."
        )
        plan["must_preserve"].append(
            "Do not apply LIMIT to the outer target rows after joining; LIMIT belongs to the inner top-k selection."
        )
    elif any(intent.startswith("ranking") for intent in intents):
        plan["expected_operation"] = "rank"
    elif any(intent.startswith("aggregation") for intent in intents):
        plan["expected_operation"] = "aggregate"
    elif "filter_literal" in intents or "filter_numeric" in intents:
        plan["expected_operation"] = "filter"
    else:
        plan["expected_operation"] = "retrieve"
    backups = []
    for recipe in selected_recipes[1:3]:
        backups.append({
            "score": recipe.get("score"),
            "scaffold_cypher": recipe.get("cypher", ""),
            "target_label": recipe.get("contract", {}).get("primary_target_label", ""),
        })
    if backups:
        plan["backup_scaffolds"] = backups
    return plan


def _path_score(path: dict, question_tokens: set[str]) -> float:
    signature = path.get("signature", "")
    path_tokens = _text_tokens(signature)
    if not path_tokens:
        return 0.0
    score = float(len(path_tokens & question_tokens))
    # Prefer the shortest path that covers the question terms; long cyclic
    # schema walks are kept as fallback context, not primary guidance.
    if score > 0:
        score -= min(float(path.get("hops", 1)) * 0.15, 0.6)
    return score


def load_profile(profile_path: str | Path) -> dict:
    return json.loads(Path(profile_path).read_text(encoding="utf-8"))


def select_profile_context(question: str, profile: dict, *, top_k: int = 5) -> dict:
    """Select compact learned context for one question from an analyzer profile."""
    q_profile = profile_question(question).to_dict()
    q_tokens = set(q_profile.get("tokens", [])) | _text_tokens(question)
    motif_scores: dict[str, float] = {}
    for token in q_tokens:
        for motif, count in profile.get("token_to_path_motifs", {}).get(token, []):
            motif_scores[motif] = motif_scores.get(motif, 0.0) + float(count)

    shape_scores: dict[str, float] = {}
    for intent in q_profile["intents"]:
        for signature, count in profile.get("intent_to_shape_signatures", {}).get(intent, []):
            shape_scores[signature] = shape_scores.get(signature, 0.0) + float(count)

    scored_examples = []
    for example in profile.get("examples", []):
        score = _example_score(example, q_profile, q_tokens, motif_scores)
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
            "contract": _extract_recipe_contract(question, {
                "cypher": example["cypher"],
                "shape": example["cypher_shape"]["signature"],
            }),
        }
        for score, example in scored_examples[:3]
    ]
    query_plan_contract = _build_query_plan_contract(question, selected_recipes, q_profile)

    return {
        "question_profile": q_profile,
        "schema_profile_summary": (
            profile.get("schema_profile", {}).get("summary", {})
            if isinstance(profile.get("schema_profile"), dict)
            else {}
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
            key=lambda path: (-_path_score(path, q_tokens), path.get("hops", 0), path.get("signature", "")),
        )[:20],
        "query_recipe_profile": profile.get("query_recipe_profile", {}),
        "value_profile": profile.get("value_profile", {}),
        "selected_path_motifs": sorted(
            motif_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:top_k],
        "selected_shape_signatures": sorted(
            shape_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:top_k],
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
    lines = ["=== LEARNED DATA/QUERY PROFILE CONTEXT ==="]
    profile_summary = context.get("schema_profile_summary") or {}
    if profile_summary:
        lines.append(
            "Live schema profile: "
            f"{profile_summary.get('label_count', 0)} labels, "
            f"{profile_summary.get('relationship_type_count', 0)} relationship types."
        )
        if profile_summary.get("top_labels"):
            labels = ", ".join(
                f"{item['label']}(count={item['count']})"
                for item in profile_summary["top_labels"][:8]
            )
            lines.append(f"High-signal labels: {labels}")
        if profile_summary.get("top_relationship_patterns"):
            lines.append("High-signal relationship patterns:")
            for item in profile_summary["top_relationship_patterns"][:8]:
                count = item.get("count")
                count_text = count if count is not None else "unknown"
                lines.append(
                    f"- ({item['from']})-[:{item['type']}]->({item['to']}) "
                    f"count={count_text}"
                )
    qp = context.get("question_profile", {})
    lines.append(f"Question intents: {', '.join(qp.get('intents', [])) or 'none'}")
    lines.append(f"Core tokens: {', '.join(qp.get('tokens', [])[:30]) or 'none'}")
    if context.get("query_plan_contract"):
        lines.append("QUERY PLAN CONTRACT JSON:")
        lines.append(json.dumps(context["query_plan_contract"], ensure_ascii=True, separators=(",", ":")))
    if context.get("selected_recipes"):
        primary = context["selected_recipes"][0]
        contract = primary.get("contract", {})
        lines.append("Primary selected query recipe:")
        lines.append(f"- source row {primary['row']} score={primary['score']}: {primary['question']}")
        lines.append(f"- scaffold: {primary['cypher']}")
        if contract.get("labels") or contract.get("relationships"):
            lines.append(
                "- schema contract: "
                f"labels={contract.get('labels', [])}; "
                f"relationships={contract.get('relationships', [])}; "
                f"question_target_labels={contract.get('target_labels', [])}; "
                f"primary_target_label={contract.get('primary_target_label', '')}"
            )
        lines.append(
            "- adaptation contract: keep the scaffold traversal unless entity resolution or the "
            "schema proves a shorter equivalent path; adapt literals, filters, ordering, "
            "aggregation, and LIMIT to the user question."
        )
        lines.append(
            "- return contract: return the entity type requested by the question, using identity "
            "properties when available; for ranking/count questions return both the entity identity "
            "and the metric."
        )
        if len(context["selected_recipes"]) > 1:
            lines.append("Backup query recipes:")
            for recipe in context["selected_recipes"][1:3]:
                lines.append(f"- row {recipe['row']} score={recipe['score']}: {recipe['cypher']}")
    if context.get("selected_path_motifs"):
        lines.append("Likely graph motifs:")
        for motif, score in context["selected_path_motifs"]:
            lines.append(f"- {motif} (profile_score={score:g})")
    if context.get("selected_shape_signatures"):
        lines.append("Likely query shapes:")
        for signature, score in context["selected_shape_signatures"]:
            lines.append(f"- {signature} (profile_score={score:g})")
    if context.get("schema_paths"):
        lines.append("Reusable schema traversal paths:")
        for path in context["schema_paths"][:8]:
            lines.append(f"- hops={path.get('hops')}: {path.get('signature')}")
    recipe_profile = context.get("query_recipe_profile") or {}
    if recipe_profile:
        lines.append(
            "Query recipe inventory: "
            f"label={recipe_profile.get('label_recipe_count', 0)}, "
            f"relationship={recipe_profile.get('relationship_recipe_count', 0)}, "
            f"path={recipe_profile.get('path_recipe_count', 0)}."
        )
        for policy in recipe_profile.get("recipe_policy", [])[:4]:
            lines.append(f"- {policy}")
    value_profile = context.get("value_profile") or {}
    if value_profile:
        lines.append("Observed value/profile hints:")
        emitted = 0
        for label, properties in value_profile.items():
            for prop, values in (properties or {}).items():
                if emitted >= 8:
                    break
                preview_values = []
                for item in (values or [])[:3]:
                    if isinstance(item, dict) and "value" in item:
                        preview_values.append(str(item["value"]))
                if preview_values:
                    lines.append(f"- {label}.{prop}: {', '.join(preview_values)}")
                    emitted += 1
            if emitted >= 8:
                break
    if context.get("selected_examples"):
        lines.append("Closest learned examples:")
        for example in context["selected_examples"]:
            lines.append(f"- row {example['row']} score={example['score']}: {example['question']}")
            lines.append(f"  {example['cypher']}")
    return "\n".join(lines)
