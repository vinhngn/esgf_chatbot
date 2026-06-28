from __future__ import annotations

import json
from pathlib import Path

from services.profile_analyzer.question_profile import profile_question


def _overlap_score(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def load_profile(profile_path: str | Path) -> dict:
    return json.loads(Path(profile_path).read_text(encoding="utf-8"))


def select_profile_context(question: str, profile: dict, *, top_k: int = 5) -> dict:
    """Select compact learned context for one question from an analyzer profile."""
    q_profile = profile_question(question).to_dict()
    motif_scores: dict[str, float] = {}
    for token in q_profile["tokens"]:
        for motif, count in profile.get("token_to_path_motifs", {}).get(token, []):
            motif_scores[motif] = motif_scores.get(motif, 0.0) + float(count)

    shape_scores: dict[str, float] = {}
    for intent in q_profile["intents"]:
        for signature, count in profile.get("intent_to_shape_signatures", {}).get(intent, []):
            shape_scores[signature] = shape_scores.get(signature, 0.0) + float(count)

    scored_examples = []
    for example in profile.get("examples", []):
        example_profile = example.get("question_profile", {})
        token_score = _overlap_score(q_profile["tokens"], example_profile.get("tokens", []))
        intent_score = _overlap_score(q_profile["intents"], example_profile.get("intents", []))
        motif_bonus = 0.0
        for motif in example.get("cypher_shape", {}).get("path_motifs", []):
            motif_bonus += motif_scores.get(motif, 0.0)
        score = token_score * 2.0 + intent_score + min(motif_bonus / 20.0, 1.0)
        if score > 0:
            scored_examples.append((score, example))
    scored_examples.sort(key=lambda item: item[0], reverse=True)

    return {
        "question_profile": q_profile,
        "schema_profile_summary": (
            profile.get("schema_profile", {}).get("summary", {})
            if isinstance(profile.get("schema_profile"), dict)
            else {}
        ),
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
    if context.get("selected_path_motifs"):
        lines.append("Likely graph motifs:")
        for motif, score in context["selected_path_motifs"]:
            lines.append(f"- {motif} (profile_score={score:g})")
    if context.get("selected_shape_signatures"):
        lines.append("Likely query shapes:")
        for signature, score in context["selected_shape_signatures"]:
            lines.append(f"- {signature} (profile_score={score:g})")
    if context.get("selected_examples"):
        lines.append("Closest learned examples:")
        for example in context["selected_examples"]:
            lines.append(f"- row {example['row']} score={example['score']}: {example['question']}")
            lines.append(f"  {example['cypher']}")
    return "\n".join(lines)
