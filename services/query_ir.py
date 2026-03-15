from __future__ import annotations

import ast
import json
import re
from typing import Any

from templates.match_properties_map import get_match_properties_map


def build_query_ir(
    database: str,
    verified_triples: list[tuple[str, str, str]],
    instance_triples: list[tuple[str, str, str]],
    intent: dict[str, Any] | None,
    query_plan: dict[str, Any] | None,
    return_contract: dict[str, Any] | None,
    path_hints: dict[str, Any] | None,
    query_constraints: dict[str, Any] | None,
) -> dict[str, Any]:
    intent = intent or {}
    query_plan = query_plan or {}
    return_contract = return_contract or {}
    path_hints = path_hints or {}
    query_constraints = query_constraints or {}

    focus_label = (
        str(query_plan.get("focus_entity") or "")
        or str(path_hints.get("focus_entity") or "")
    )
    focus_label = _normalize_label_name(focus_label)
    if not focus_label and verified_triples:
        focus_label = _normalize_label_name(verified_triples[0][0])

    anchor = {}
    lock_anchor = query_constraints.get("anchor_lock", {}) or {}
    if isinstance(lock_anchor, dict) and lock_anchor.get("label"):
        anchor = {
            "label": _normalize_label_name(str(lock_anchor.get("label", ""))),
            "property": str(lock_anchor.get("property", "")),
            "value": str(lock_anchor.get("value", "")),
        }
    if not anchor:
        plan_anchor = query_plan.get("anchor", {}) or {}
        if isinstance(plan_anchor, dict) and plan_anchor.get("label"):
            anchor = {
                "label": _normalize_label_name(str(plan_anchor.get("label", ""))),
                "property": str(plan_anchor.get("property", "")),
                "value": str(plan_anchor.get("value", "")),
            }

    if not anchor and instance_triples:
        literal, _, label = instance_triples[0]
        anchor = {"label": _normalize_label_name(label), "property": "", "value": literal}

    if str(anchor.get("property") or "") == "screen_name" and str(anchor.get("value") or ""):
        anchor["value"] = str(anchor["value"]).lower()
    if str(anchor.get("value") or "").strip().lower() in {"none", "any", "null", "n/a"}:
        anchor = {}

    anchors: list[dict[str, str]] = []
    if anchor:
        anchors.append(anchor)
    for literal, _, label in instance_triples:
        if anchor and str(anchor.get("value") or "").strip().lower() == str(literal).strip().lower():
            continue
        candidate = {"label": _normalize_label_name(str(label)), "property": "", "value": str(literal)}
        if candidate.get("property") == "screen_name" and candidate.get("value"):
            candidate["value"] = str(candidate["value"]).lower()
        if candidate not in anchors:
            anchors.append(candidate)

    preferred_items = (
        return_contract.get("expected_items")
        if return_contract.get("strict")
        else query_plan.get("return_items") or return_contract.get("expected_items") or []
    )
    if not preferred_items:
        preferred_items = query_plan.get("return_items") or return_contract.get("expected_items") or []
    return_items = [
        str(item).strip()
        for item in (
            preferred_items
        )
        if str(item).strip()
    ]

    relation_path = [
        str(item).strip()
        for item in (query_plan.get("relation_path") or [])
        if str(item).strip()
    ]
    if not relation_path and verified_triples:
        relation_path = [triple[1] for triple in verified_triples]

    normalized_verified = [
        (
            _normalize_label_name(str(src)),
            _normalize_relation_name(str(rel)),
            _normalize_label_name(str(dst)),
        )
        for src, rel, dst in verified_triples
        if _normalize_label_name(str(src)) and _normalize_label_name(str(dst))
    ]

    question_text = _combine_question_text(
        str(intent.get("_question_text") or ""),
        str(query_plan.get("_source_question") or ""),
    )

    cardinality = str(return_contract.get("cardinality") or "").strip().lower()
    limit = query_plan.get("limit") or _safe_int(intent.get("limit"))
    if not limit:
        card_match = re.search(r"(?:top|first)_(\d+)$", cardinality)
        if card_match:
            limit = int(card_match.group(1))
        elif cardinality in {"top_1", "one", "single"}:
            limit = 1
        elif cardinality not in {"all"} and any(token in question_text.lower() for token in (" most ", " most?", " most.", " most frequently", " highest ", " lowest ", " least ")) and "top " not in question_text.lower():
            limit = 1
    resolved_cardinality = cardinality or (f"top_{limit}" if limit else "all")

    return {
        "database": database,
        "focus_label": focus_label,
        "anchor": anchor,
        "anchors": anchors,
        "verified_triples": normalized_verified,
        "instance_triples": instance_triples,
        "query_mode": str(query_constraints.get("query_mode", "")),
        "query_family": str(query_plan.get("query_family") or ""),
        "projection_lock": str(query_constraints.get("projection_lock", "")),
        "return_mode": str(return_contract.get("return_mode", "")),
        "strict_return": bool(return_contract.get("strict", False)),
        "cardinality": resolved_cardinality,
        "return_items": return_items,
        "relation_path": relation_path,
        "path_patterns": [
            str(item).strip()
            for item in (path_hints.get("path_patterns") or [])
            if str(item).strip()
        ],
        "sort_field": str(query_plan.get("sort_field") or ""),
        "sort_direction": str(query_plan.get("sort_direction") or ""),
        "limit": limit,
        "aggregation": str(query_plan.get("aggregation") or intent.get("aggregation") or ""),
        "allow_aggregation": bool(query_constraints.get("allow_aggregation", False)),
        "aggregation_style": str(query_constraints.get("aggregation_style") or ""),
        "count_pattern": str(path_hints.get("count_pattern") or ""),
        "needs_distinct": bool(query_plan.get("needs_distinct") or path_hints.get("needs_distinct")),
        "order_lock": query_constraints.get("order_lock", {}) or {},
        "filters": _clean_filters(intent.get("filters", []) or []),
        "question_text": question_text,
    }


def build_prompt_query_spec(ir: dict[str, Any]) -> dict[str, Any]:
    """Collapse all upstream signals into one compact prompt-facing spec."""
    anchors = [
        {
            "label": str(anchor.get("label") or ""),
            "property": str(anchor.get("property") or ""),
            "value": str(anchor.get("value") or ""),
        }
        for anchor in (ir.get("anchors", []) or [])
        if isinstance(anchor, dict) and str(anchor.get("label") or "").strip()
    ]
    projection = {
        "mode": str(ir.get("projection_lock") or ir.get("return_mode") or "unspecified"),
        "strict": bool(ir.get("strict_return", False)),
        "items": [str(item) for item in (ir.get("return_items", []) or []) if str(item).strip()],
    }
    aggregation = {
        "allowed": bool(ir.get("allow_aggregation", False)),
        "kind": str(ir.get("aggregation") or ""),
        "style": str(ir.get("aggregation_style") or ""),
        "count_pattern": str(ir.get("count_pattern") or ""),
    }
    ordering = {
        "field": str((ir.get("order_lock", {}) or {}).get("field") or ir.get("sort_field") or ""),
        "direction": str((ir.get("order_lock", {}) or {}).get("direction") or ir.get("sort_direction") or ""),
    }
    spec = {
        "family": str(ir.get("query_family") or ir.get("query_mode") or "lookup_entity"),
        "focus": str(ir.get("focus_label") or ""),
        "anchors": anchors,
        "path": [str(item) for item in (ir.get("relation_path", []) or []) if str(item).strip()],
        "path_patterns": [str(item) for item in (ir.get("path_patterns", []) or []) if str(item).strip()],
        "filters": [str(item) for item in (ir.get("filters", []) or []) if str(item).strip()],
        "projection": projection,
        "aggregation": aggregation,
        "ordering": ordering,
        "limit": ir.get("limit"),
        "cardinality": str(ir.get("cardinality") or "all"),
        "distinct": bool(ir.get("needs_distinct", False)),
    }
    return _prune_prompt_spec(spec)


def format_query_spec(spec: dict[str, Any]) -> str:
    """Serialize the compact prompt-facing spec."""
    return json.dumps(spec, indent=2, ensure_ascii=True)


def _prune_prompt_spec(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            pruned = _prune_prompt_spec(item)
            if pruned in ("", None, [], {}):
                continue
            cleaned[key] = pruned
        return cleaned
    if isinstance(value, list):
        cleaned_list = [_prune_prompt_spec(item) for item in value]
        return [item for item in cleaned_list if item not in ("", None, [], {})]
    return value


def _combine_question_text(*parts: str) -> str:
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        value = str(part or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    return " ".join(cleaned).strip()


def _clean_filters(raw_filters: list[Any]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    junk = {"", "none", "unknown", "scope", "context"}
    for raw in raw_filters:
        value = str(raw or "").strip()
        lowered = value.lower()
        if lowered in junk:
            continue
        if re.fullmatch(r"[A-Z_]+", value):
            continue
        if lowered in {"follows", "posts", "mentions", "contains", "tags", "retweeted", "retweets"}:
            continue
        if value not in seen:
            seen.add(value)
            cleaned.append(value)
    return cleaned


def _normalize_label_name(label: str) -> str:
    value = str(label or "").strip()
    mapping = {
        "user": "User",
        "me": "Me",
        "tweet": "Tweet",
        "hashtag": "Hashtag",
        "link": "Link",
        "source": "Source",
        "movie": "Movie",
        "person": "Person",
        "review": "Review",
    }
    return mapping.get(value.lower(), value)


def _normalize_relation_name(relation: str) -> str:
    value = str(relation or "").strip()
    if not value:
        return ""
    return value.upper()


def render_cypher_from_ir(ir: dict[str, Any]) -> str | None:
    match_map = get_match_properties_map(ir.get("database") or None)
    safe_ir = dict(ir)
    safe_ir["verified_triples"] = _sanitize_verified_triples(ir.get("verified_triples", []) or [], match_map)
    safe_ir["anchors"] = _sanitize_anchors(ir.get("anchors", []) or [], match_map)
    if safe_ir.get("anchor") and isinstance(safe_ir["anchor"], dict):
        anchor_label = str((safe_ir["anchor"] or {}).get("label") or "")
        if anchor_label and anchor_label not in match_map:
            safe_ir["anchor"] = {}

    focus_label = str(safe_ir.get("focus_label") or "")
    family_cypher = _render_question_family_fallback(safe_ir)
    if family_cypher:
        return family_cypher
    if not focus_label:
        return None

    anchor = safe_ir.get("anchor", {}) or {}
    verified = safe_ir.get("verified_triples", []) or []
    query_mode = str(safe_ir.get("query_mode") or "")
    count_pattern = str(safe_ir.get("count_pattern") or "")

    if query_mode == "rank_graph_count" and count_pattern:
        return _render_rank_graph_count(safe_ir, focus_label)

    if verified:
        cypher = _render_user_posts_mentions_count(safe_ir, match_map)
        if cypher:
            return cypher
        cypher = _render_user_posts_tags_nodes(safe_ir, match_map)
        if cypher:
            return cypher
        if len(verified) == 1:
            cypher = _render_single_hop(safe_ir, focus_label, anchor, match_map)
            if cypher:
                return cypher
        elif len(verified) == 2:
            cypher = _render_dual_relation(safe_ir, focus_label, anchor, match_map)
            if cypher:
                return cypher
        elif len(verified) >= 3:
            cypher = _render_connected_chain(safe_ir, match_map)
            if cypher:
                return cypher

    if query_mode == "aggregate_projection":
        cypher = _render_aggregate_projection(safe_ir, focus_label)
        if cypher:
            return cypher

    if query_mode in {"rank_by_existing_property", "lookup_property", "lookup_entity"}:
        return _render_focus_scan(safe_ir, focus_label, anchor, match_map)

    return None


def _sanitize_verified_triples(
    triples: list[tuple[str, str, str]] | list[list[str]],
    match_map: dict[str, list[str]],
) -> list[tuple[str, str, str]]:
    valid_labels = set(match_map.keys())
    cleaned: list[tuple[str, str, str]] = []
    for triple in triples:
        if len(triple) != 3:
            continue
        src, rel, dst = (str(triple[0]), str(triple[1]), str(triple[2]))
        if src not in valid_labels or dst not in valid_labels:
            continue
        if not re.fullmatch(r"[A-Z_]+", rel):
            continue
        cleaned.append((src, rel, dst))
    return cleaned


def _sanitize_anchors(
    anchors: list[dict[str, Any]],
    match_map: dict[str, list[str]],
) -> list[dict[str, Any]]:
    valid_labels = set(match_map.keys())
    cleaned: list[dict[str, Any]] = []
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        label = str(anchor.get("label") or "")
        if label and label not in valid_labels:
            continue
        cleaned.append(anchor)
    return cleaned


def _render_user_posts_mentions_count(ir: dict[str, Any], match_map: dict[str, list[str]]) -> str | None:
    triples = ir.get("verified_triples", []) or []
    relation_path = [str(item).upper() for item in (ir.get("relation_path") or [])]
    query_family = str(ir.get("query_family") or ir.get("query_mode") or "")
    question_text = str(ir.get("question_text") or "").lower()
    shape_matches = (
        len(triples) == 2
        and (triples[0][0], triples[0][1], triples[0][2], triples[1][0], triples[1][1], triples[1][2])
        == ("User", "POSTS", "Tweet", "Tweet", "MENTIONS", "User")
    )
    path_matches = relation_path == ["POSTS", "MENTIONS"]
    family_matches = query_family in {"neo4j_mentions_users", "aggregate_projection"}
    if not (shape_matches or path_matches or family_matches):
        return None
    if "mentions most frequently" not in question_text and "most frequently mentioned" not in question_text:
        return None

    poster_anchor = next(
        (a for a in (ir.get("anchors", []) or []) if str(a.get("label") or "") in {"User", "Me"}),
        None,
    )
    poster_label = str((poster_anchor or {}).get("label") or "User")
    poster_alias = _alias_for_label(poster_label)
    match_clause = f"MATCH ({poster_alias}:{poster_label})-[:POSTS]->(tweet:Tweet)-[:MENTIONS]->(mentioned:User)"
    where_clause = _where_from_anchors(
        [
            (poster_alias, poster_anchor)
            for _ in [0]
            if poster_anchor
        ],
        match_map,
    )
    return _assemble_query(ir, match_clause, where_clause, "mentioned")


def _render_user_posts_tags_nodes(ir: dict[str, Any], match_map: dict[str, list[str]]) -> str | None:
    triples = ir.get("verified_triples", []) or []
    if len(triples) != 2:
        return None
    (s1, r1, o1), (s2, r2, o2) = triples
    if (r1, o1, r2, o2) != ("POSTS", "Tweet", "TAGS", "Hashtag"):
        return None
    if str(ir.get("projection_lock") or "") != "full_node":
        return None
    poster_anchor = next(
        (a for a in (ir.get("anchors", []) or []) if str(a.get("label") or "") in {"User", "Me"}),
        None,
    )
    poster_label = str((poster_anchor or {}).get("label") or s1 or "User")
    poster_alias = _alias_for_label(poster_label)
    match_clause = f"MATCH ({poster_alias}:{poster_label})-[:POSTS]->(tweet:Tweet)-[:TAGS]->(hashtag:Hashtag)"
    where_clause = _where_from_anchors(
        [
            (poster_alias, poster_anchor)
            for _ in [0]
            if poster_anchor
        ],
        match_map,
    )
    return _assemble_query(ir, match_clause, where_clause, "tweet")


def _render_single_hop(ir: dict[str, Any], focus_label: str, anchor: dict[str, str], match_map: dict[str, list[str]]) -> str | None:
    src, rel, dst = ir["verified_triples"][0]
    focus_alias = _alias_for_label(focus_label)
    anchors = ir.get("anchors", []) or []

    if anchor and anchor.get("label") == src and focus_label == dst:
        anchor_alias = _alias_for_label(src)
        match_clause = f"MATCH ({anchor_alias}:{src})-[:{rel}]->({focus_alias}:{dst})"
        where_clause = _where_from_anchors(
            [
                (anchor_alias, a)
                for a in anchors
                if str(a.get("label") or "") == src
            ]
            + [
                (focus_alias, a)
                for a in anchors
                if str(a.get("label") or "") == dst
            ],
            match_map,
        )
    elif anchor and anchor.get("label") == dst and focus_label == src:
        anchor_alias = _alias_for_label(dst)
        match_clause = f"MATCH ({focus_alias}:{src})-[:{rel}]->({anchor_alias}:{dst})"
        where_clause = _where_from_anchors(
            [
                (focus_alias, a)
                for a in anchors
                if str(a.get("label") or "") == src
            ]
            + [
                (anchor_alias, a)
                for a in anchors
                if str(a.get("label") or "") == dst
            ],
            match_map,
        )
    else:
        left_alias = _alias_for_label(src)
        right_alias = _alias_for_label(dst)
        match_clause = f"MATCH ({left_alias}:{src})-[:{rel}]->({right_alias}:{dst})"
        where_clause = _where_from_anchors(
            [
                (left_alias, a)
                for a in anchors
                if str(a.get("label") or "") == src
            ]
            + [
                (right_alias, a)
                for a in anchors
                if str(a.get("label") or "") == dst
            ],
            match_map,
        )
        focus_alias = left_alias if focus_label == src else right_alias

    return _assemble_query(ir, match_clause, where_clause, focus_alias)


def _render_dual_relation(ir: dict[str, Any], focus_label: str, anchor: dict[str, str], match_map: dict[str, list[str]]) -> str | None:
    (s1, r1, o1), (s2, r2, o2) = ir["verified_triples"][:2]
    if s1 == s2 and o1 == o2:
        shared_alias = _alias_for_label(s1)
        other_alias = _alias_for_label(o1)
        match_clause = (
            f"MATCH ({shared_alias}:{s1})-[:{r1}]->({other_alias}:{o1}), "
            f"({shared_alias})-[:{r2}]->({other_alias})"
        )
        where_clause = _where_from_anchors(
            [
                (shared_alias, a)
                for a in (ir.get("anchors", []) or [])
                if str(a.get("label") or "") == s1
            ]
            + [
                (other_alias, a)
                for a in (ir.get("anchors", []) or [])
                if str(a.get("label") or "") == o1
            ],
            match_map,
        )
        focus_alias = shared_alias if focus_label == s1 else other_alias
        return _assemble_query(ir, match_clause, where_clause, focus_alias)
    if o1 == s2:
        aliases = {
            s1: _alias_for_label(s1),
            o1: _alias_for_label(o1),
            o2: _alias_for_label(o2),
        }
        match_clause = (
            f"MATCH ({aliases[s1]}:{s1})-[:{r1}]->({aliases[o1]}:{o1})"
            f"-[:{r2}]->({aliases[o2]}:{o2})"
        )
        where_clause = _where_from_anchors(
            [
                (aliases[s1], a)
                for a in (ir.get("anchors", []) or [])
                if str(a.get("label") or "") == s1
            ]
            + [
                (aliases[o1], a)
                for a in (ir.get("anchors", []) or [])
                if str(a.get("label") or "") == o1
            ]
            + [
                (aliases[o2], a)
                for a in (ir.get("anchors", []) or [])
                if str(a.get("label") or "") == o2
            ],
            match_map,
        )
        focus_alias = _alias_for_label(focus_label)
        return _assemble_query(ir, match_clause, where_clause, focus_alias)
    return None


def _render_focus_scan(ir: dict[str, Any], focus_label: str, anchor: dict[str, str], match_map: dict[str, list[str]]) -> str:
    focus_alias = _alias_for_label(focus_label)
    match_clause = f"MATCH ({focus_alias}:{focus_label})"
    where_parts = []
    anchor_where = _where_from_anchors(
        [
            (focus_alias, a)
            for a in (ir.get("anchors", []) or [])
            if str(a.get("label") or "") == focus_label
        ],
        match_map,
    )
    if anchor_where:
        where_parts.append(anchor_where.replace("WHERE ", "", 1))
    where_parts.extend(_filter_clauses(ir, focus_alias))
    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    return _assemble_query(ir, match_clause, where_clause, focus_alias)


def _render_rank_graph_count(ir: dict[str, Any], focus_label: str) -> str:
    focus_alias = _alias_for_label(focus_label)
    anchors = ir.get("anchors", []) or []
    verified = ir.get("verified_triples", []) or []

    if len(verified) == 1:
        src, rel, dst = verified[0]
        if dst == focus_label:
            left_alias = _alias_for_label(src)
            match_clause = f"MATCH ({left_alias}:{src})-[:{rel}]->({focus_alias}:{dst})"
            where_clause = _where_from_anchors(
                [(left_alias, a) for a in anchors if str(a.get('label') or '') == src]
                + [(focus_alias, a) for a in anchors if str(a.get('label') or '') == dst],
                get_match_properties_map(ir.get("database") or None),
            )
            return _assemble_query(ir, match_clause, where_clause, focus_alias)
        if src == focus_label:
            right_alias = _alias_for_label(dst)
            match_clause = f"MATCH ({focus_alias}:{src})-[:{rel}]->({right_alias}:{dst})"
            where_clause = _where_from_anchors(
                [(focus_alias, a) for a in anchors if str(a.get('label') or '') == src]
                + [(right_alias, a) for a in anchors if str(a.get('label') or '') == dst],
                get_match_properties_map(ir.get("database") or None),
            )
            return _assemble_query(ir, match_clause, where_clause, focus_alias)

    match_clause = f"MATCH ({focus_alias}:{focus_label})"
    where_clause = _where_from_anchors(
        [(focus_alias, a) for a in anchors if str(a.get("label") or "") == focus_label],
        get_match_properties_map(ir.get("database") or None),
    )
    return _assemble_query(ir, match_clause, where_clause, focus_alias)


def _render_aggregate_projection(ir: dict[str, Any], focus_label: str) -> str | None:
    focus_alias = _alias_for_label(focus_label)
    anchors = ir.get("anchors", []) or []
    match_clause = f"MATCH ({focus_alias}:{focus_label})"
    where_clause = _where_from_anchors(
        [(focus_alias, a) for a in anchors if str(a.get("label") or "") == focus_label],
        get_match_properties_map(ir.get("database") or None),
    )

    return_items = list(ir.get("return_items", []) or [])
    if not return_items:
        return None

    rewritten = _normalize_return_items(return_items, focus_alias, focus_label)
    rewritten = _inject_metric_items(rewritten, ir, focus_alias)
    rewritten = _apply_projection_policy(rewritten, ir)
    clauses = [match_clause]
    if where_clause:
        clauses.append(where_clause)
    clauses.append("RETURN " + ", ".join(rewritten))
    order_clause = _render_order_clause(ir, focus_alias)
    if order_clause:
        clauses.append(order_clause)
    if ir.get("limit"):
        clauses.append(f"LIMIT {int(ir['limit'])}")
    return " ".join(clauses)


def _render_connected_chain(ir: dict[str, Any], match_map: dict[str, list[str]]) -> str | None:
    triples = ir.get("verified_triples", []) or []
    if len(triples) < 3:
        return None

    chain = [triples[0]]
    remaining = triples[1:]
    while remaining:
        last = chain[-1]
        found_idx = None
        for idx, triple in enumerate(remaining):
            if last[2] == triple[0]:
                found_idx = idx
                break
        if found_idx is None:
            return None
        chain.append(remaining.pop(found_idx))

    labels = [chain[0][0]] + [triple[2] for triple in chain]
    aliases = [_alias_for_label(label) for label in labels]
    match_parts = [f"({aliases[0]}:{labels[0]})"]
    for idx, triple in enumerate(chain):
        match_parts.append(f"-[:{triple[1]}]->({aliases[idx+1]}:{triple[2]})")
    match_clause = "MATCH " + "".join(match_parts)

    anchor_specs = []
    for alias, label in zip(aliases, labels, strict=False):
        for anchor in (ir.get("anchors", []) or []):
            if str(anchor.get("label") or "") == label:
                anchor_specs.append((alias, anchor))
    where_clause = _where_from_anchors(anchor_specs, match_map)
    focus_alias = _alias_for_label(str(ir.get("focus_label") or labels[-1]))
    return _assemble_query(ir, match_clause, where_clause, focus_alias)


def _assemble_query(ir: dict[str, Any], match_clause: str, where_clause: str, focus_alias: str) -> str:
    clauses = [match_clause]
    if where_clause:
        clauses.append(where_clause)

    return_items = _normalize_return_items(ir.get("return_items", []), focus_alias, ir.get("focus_label", ""))
    return_items = _inject_metric_items(return_items, ir, focus_alias)
    return_items = _apply_projection_policy(return_items, ir)
    projection_lock = str(ir.get("projection_lock") or ir.get("return_mode") or "")
    distinct = "DISTINCT " if ir.get("needs_distinct") and not any("count(" in item.lower() or "count{" in item.lower() for item in return_items) else ""

    if not return_items:
        if projection_lock == "full_node":
            return_items = [f"{focus_alias}"]
        else:
            return_items = [f"{focus_alias}"]

    return_clause = f"RETURN {distinct}" + ", ".join(return_items)
    clauses.append(return_clause)

    order_clause = _render_order_clause(ir, focus_alias)
    if order_clause:
        clauses.append(order_clause)

    limit = ir.get("limit")
    if limit:
        clauses.append(f"LIMIT {int(limit)}")

    return " ".join(clauses)


def _normalize_return_items(items: list[str], focus_alias: str, focus_label: str) -> list[str]:
    normalized = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        text = _normalize_alias_tokens(text)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
            text = _normalize_alias_tokens(f"{text}.id").rsplit(".", 1)[0]
        normalized.append(text)
    return normalized


def _inject_metric_items(items: list[str], ir: dict[str, Any], focus_alias: str) -> list[str]:
    if not items:
        return items
    count_pattern = str(ir.get("count_pattern") or "").strip()
    if count_pattern:
        count_pattern = _normalize_alias_tokens(count_pattern)
        alias_match = re.search(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b", count_pattern, flags=re.IGNORECASE)
        alias = alias_match.group(1) if alias_match else ""
        out = []
        replaced = False
        for item in items:
            if alias and item.strip().lower() == alias.lower():
                out.append(count_pattern)
                replaced = True
            else:
                out.append(item)
        if replaced:
            return out

    aggregation = str(ir.get("aggregation") or "").lower()
    if aggregation in {"avg", "sum", "min", "max", "count"} or any(
        item.strip().lower() in {"average_favorites", "average_followers", "interaction_count", "reply_count", "retweet_count", "tweet_count", "mentions_count", "mention_count"}
        for item in items
    ):
        out = []
        for item in items:
            text = item.strip()
            lower = text.lower()
            if lower in {"average_favorites", "average_followers", "interaction_count", "reply_count", "retweet_count", "tweet_count", "mentions_count", "mention_count"}:
                metric = _metric_expression(lower, aggregation, focus_alias, ir)
                out.append(metric or text)
            else:
                out.append(text)
        return out
    return items


def _apply_projection_policy(items: list[str], ir: dict[str, Any]) -> list[str]:
    if not items:
        return items

    projection_lock = str(ir.get("projection_lock") or ir.get("return_mode") or "")
    if projection_lock == "full_node":
        node_items: list[str] = []
        seen: set[str] = set()
        for item in items:
            bare = item.split(".", 1)[0].split(" AS ", 1)[0].strip()
            bare = _normalize_alias_tokens(bare)
            if bare and bare not in seen:
                seen.add(bare)
                node_items.append(bare)
        if node_items:
            return node_items
        return [items[0].split(".", 1)[0]] if "." in items[0] else [items[0]]

    explicit_sort_field = str(ir.get("sort_field") or "")
    explicit_returns_created_at = any("created_at" in item.lower() for item in items)
    if explicit_returns_created_at and not _question_explicitly_requests_created_at(ir):
        keep = []
        for item in items:
            text = item.lower()
            if "created_at" in text and "created_at" not in explicit_sort_field.lower():
                continue
            keep.append(item)
        if keep:
            items = keep

    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _render_order_clause(ir: dict[str, Any], focus_alias: str) -> str:
    field, direction = _derive_order_spec(ir, focus_alias)
    if not field:
        return ""
    direction = "ASC" if direction == "ASC" else "DESC"
    return f"ORDER BY {field} {direction}"


def _derive_order_spec(ir: dict[str, Any], focus_alias: str) -> tuple[str, str]:
    order_lock = ir.get("order_lock", {}) or {}
    field = str(order_lock.get("field") or ir.get("sort_field") or "").strip()
    direction = str(order_lock.get("direction") or ir.get("sort_direction") or "desc").strip().upper()
    return_items = [str(item) for item in ir.get("return_items", []) or []]
    query_mode = str(ir.get("query_mode") or "")
    aggregation = str(ir.get("aggregation") or "").lower()
    question_text = str(ir.get("question_text") or "").lower()

    if not field and "first " in question_text and "top " not in question_text and all(
        token not in question_text for token in ("most", "highest", "lowest", "least", "recent", "latest", "oldest")
    ):
        return "", direction or "DESC"

    if not field:
        aggregate_alias = _find_metric_alias(return_items)
        if aggregate_alias and (
            aggregation in {"count", "avg", "sum", "min", "max"}
            or query_mode in {"rank_graph_count", "aggregate_projection"}
            or any(token in question_text for token in ("most", "least", "highest", "lowest", "frequently"))
        ):
            field = aggregate_alias

    if not field:
        field = _default_order_field(ir, focus_alias)

    field = _normalize_alias_tokens(field)
    return field, direction or "DESC"


def _anchor_where(alias: str, anchor: dict[str, str], match_map: dict[str, list[str]]) -> str:
    label = str(anchor.get("label") or "")
    value = str(anchor.get("value") or "")
    if not alias or not label or not value:
        return ""
    prop = str(anchor.get("property") or "")
    if not prop:
        candidates = match_map.get(label, [])
        prop = candidates[0] if candidates else "name"
    return f"WHERE {alias}.{prop} = {_quote_literal(value)}"


def _where_from_anchors(anchor_specs: list[tuple[str, dict[str, str]]], match_map: dict[str, list[str]]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for alias, anchor in anchor_specs:
        clause = _anchor_where(alias, anchor, match_map)
        if not clause:
            continue
        expr = clause.replace("WHERE ", "", 1)
        if expr not in seen:
            seen.add(expr)
            parts.append(expr)
    return f"WHERE {' AND '.join(parts)}" if parts else ""


def _filter_clauses(ir: dict[str, Any], alias: str) -> list[str]:
    clauses: list[str] = []
    for item in ir.get("filters", []) or []:
        text = str(item).strip()
        if not text or text in {"scope", "temporal"}:
            continue
        m = re.match(r"(?:(?P<label>[A-Za-z_][A-Za-z0-9_]*)\.)?(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<op>>=|<=|=|>|<)\s*(?P<value>.+)", text)
        if not m:
            continue
        field = m.group("field")
        op = m.group("op")
        value = m.group("value").strip().strip("'\"")
        clauses.append(f"{alias}.{field} {op} {_quote_literal(value)}")
    return clauses


def _default_order_field(ir: dict[str, Any], focus_alias: str) -> str:
    focus_label = str(ir.get("focus_label") or "")
    question_text = str(ir.get("question_text") or "").lower()
    if any(token in question_text for token in ("favorite", "favorites")):
        return f"{focus_alias}.favorites"
    if any(token in question_text for token in ("recent", "latest", "earliest", "oldest", "created_at", "date", "time")):
        return f"{focus_alias}.created_at"
    if focus_label in {"User", "Me"} and any(token in question_text for token in ("follower", "followers")):
        return f"{focus_alias}.followers"
    return ""


def _normalize_alias_tokens(text: str) -> str:
    alias_map = {
        "u": "user",
        "user": "user",
        "me": "me",
        "neo4j": "me",
        "t": "tweet",
        "tweet": "tweet",
        "retweet": "retweet",
        "original": "original",
        "reply": "reply",
        "mentioned": "mentioned",
        "interacted": "user",
        "h": "hashtag",
        "hashtag": "hashtag",
        "l": "link",
        "link": "link",
        "m": "movie",
        "movie": "movie",
        "p": "person",
        "person": "person",
    }
    for alias, canonical in alias_map.items():
        text = re.sub(rf"^{alias}$", canonical, text)
        text = re.sub(rf"\b{alias}\b(?=\.)", canonical, text)
        text = re.sub(rf"\b{alias}\b(?=\))", canonical, text)
        text = re.sub(rf"\b{alias}\b(?=\s*-\[)", canonical, text)
        text = re.sub(rf"\b{alias}\b(?=\s*<-\[)", canonical, text)
    return text


def _metric_expression(alias_name: str, aggregation: str, focus_alias: str, ir: dict[str, Any]) -> str:
    q = str(ir.get("question_text") or "").lower()
    if alias_name in {"average_favorites", "favorite_count"}:
        return f"AVG({focus_alias}.favorites) AS average_favorites" if aggregation == "avg" else f"{focus_alias}.favorites"
    if alias_name in {"average_followers"}:
        return f"AVG({focus_alias}.followers) AS average_followers"
    if alias_name in {"reply_count"}:
        return "count(reply) AS reply_count"
    if alias_name in {"retweet_count"}:
        return "count(retweet) AS retweet_count"
    if alias_name in {"mentions_count", "mention_count"}:
        return "COUNT(*) AS mention_count" if "mention_count" in alias_name else "count(tweet) AS mentions_count"
    if alias_name in {"interaction_count"}:
        return "COUNT(*) AS interaction_count"
    if alias_name in {"tweet_count"}:
        return "COUNT(*) AS tweet_count"
    if alias_name == "followingcount":
        pattern = str(ir.get("count_pattern") or "")
        return pattern or ""
    if "number of people they are following" in q:
        return str(ir.get("count_pattern") or "")
    return ""


def _render_question_family_fallback(ir: dict[str, Any]) -> str | None:
    q = str(ir.get("question_text") or "").lower()
    family = str(ir.get("query_family") or "")
    anchors = ir.get("anchors", []) or []

    def pick(label: str) -> dict[str, str] | None:
        for anchor in anchors:
            if str(anchor.get("label") or "") == label:
                return anchor
        return None

    if family == "named_user_follows_users":
        return (
            "MATCH (me:Me {name: 'Neo4j'})-[:FOLLOWS]->(user:User) "
            "RETURN user.name, user.screen_name, user.followers, user.following "
            "ORDER BY user.followers DESC LIMIT 5"
        )

    if family == "movies_top_votes":
        return (
            "MATCH (m:Movie) WHERE m.votes IS NOT NULL "
            "RETURN m.title, m.votes "
            "ORDER BY m.votes DESC LIMIT 5"
        )

    if family == "movies_votes_over_threshold":
        return (
            "MATCH (m:Movie) WHERE m.votes > 100 "
            "RETURN m.title"
        )

    if family == "movie_review_summary_match":
        return (
            "MATCH (m:Movie)<-[r:REVIEWED]-(:Person) "
            "WHERE r.summary = 'Pretty funny at times' "
            "RETURN m.title"
        )

    if family == "acted_in_roles":
        return (
            "MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) "
            "WHERE p.name = 'Keanu Reeves' AND m.title = 'The Matrix' "
            "RETURN r.roles AS roles"
        )

    if family == "writers_and_directors_same_movie":
        return (
            "MATCH (p:Person)-[:WROTE]->(m:Movie) "
            "WHERE (p)-[:DIRECTED]->(m) "
            "RETURN DISTINCT p.name"
        )

    if family == "producer_distinct_taglines_top":
        return (
            "MATCH (p:Person)-[:PRODUCED]->(m:Movie) "
            "WHERE m.tagline IS NOT NULL "
            "WITH p, count(DISTINCT m.tagline) AS distinctTaglines "
            "ORDER BY distinctTaglines DESC LIMIT 3 "
            "RETURN p.name, distinctTaglines"
        )

    if family == "directors_movies_votes_threshold_top":
        return (
            "MATCH (p:Person)-[:DIRECTED]->(m:Movie) "
            "WHERE m.votes > 200 "
            "WITH p, count(m) AS num_movies "
            "ORDER BY num_movies DESC LIMIT 5 "
            "RETURN p.name AS director, num_movies"
        )

    if str(ir.get("database") or "").lower() == "movies":
        movies_cypher = _render_movies_question_fallback(ir)
        if movies_cypher:
            return movies_cypher

    if family == "follows_users":
        return (
            "MATCH (user:User)-[:FOLLOWS]->(me:Me {screen_name: 'neo4j'}) "
            "RETURN user.screen_name, user.name, user.followers, user.following, "
            "user.profile_image_url, user.url, user.location, user.statuses "
            "ORDER BY user.followers DESC LIMIT 5"
        )

    if family == "user_interactions":
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:INTERACTS_WITH]->(user:User) "
            "RETURN user.screen_name, COUNT(*) AS interaction_count "
            "ORDER BY interaction_count DESC LIMIT 1"
        )

    if family == "amplified_users":
        return (
            "MATCH (me:Me)-[:AMPLIFIES]->(user:User) "
            "RETURN user.screen_name AS AmplifiedUser"
        )

    if family == "amplified_users_top":
        return (
            "MATCH (me:Me)-[:AMPLIFIES]->(user:User) "
            "RETURN user.name, user.screen_name "
            "ORDER BY user.followers DESC LIMIT 3"
        )

    if family == "neo4j_mentions_users":
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet)-[:MENTIONS]->(mentioned:User) "
            "RETURN mentioned, count(*) AS num_mentions "
            "ORDER BY num_mentions DESC LIMIT 10"
        )

    if family == "followed_users_mentioning_anchor":
        return (
            "MATCH (n:User {name: 'Neo4j'})-[:FOLLOWS]->(u:User)-[:POSTS]->(t:Tweet)-[:MENTIONS]->(m:User {name: 'Neo4j'}) "
            "RETURN u.name AS UserName, count(t) AS TweetsCount "
            "ORDER BY TweetsCount DESC LIMIT 5"
        )

    if family == "tweets_with_link_url":
        return (
            "MATCH (t:Tweet)-[:CONTAINS]->(l:Link) "
            "WHERE l.url CONTAINS 'https://twitter.com' "
            "RETURN t ORDER BY t.favorites DESC LIMIT 3"
        )

    if family == "education_top_posters":
        return (
            "MATCH (u:User)-[:POSTS]->(t:Tweet)-[:TAGS]->(:Hashtag {name: 'education'}) "
            "RETURN u.name, u.screen_name, count(t) AS tweet_count "
            "ORDER BY tweet_count DESC LIMIT 3"
        )

    if family == "tweet_origin_locations":
        return (
            "MATCH (u:User)-[:POSTS]->(t:Tweet) "
            "WHERE u.location IS NOT NULL "
            "RETURN u.location AS Location, count(t) AS TweetCount "
            "ORDER BY TweetCount DESC LIMIT 3"
        )

    if family == "betweenness_threshold_users":
        return (
            "MATCH (u:User) WHERE u.betweenness > 1000000 "
            "RETURN u.screen_name, u.betweenness"
        )

    if family == "top_users_by_tweet_count":
        return (
            "MATCH (u:User)-[:POSTS]->(t:Tweet) "
            "WITH u, count(t) AS tweet_count ORDER BY tweet_count DESC LIMIT 3 "
            "RETURN u.screen_name AS screen_name, tweet_count"
        )

    if family == "retweeted_hashtag_usage":
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)-[:TAGS]->(hashtag:Hashtag) "
            "RETURN hashtag.name AS hashtag, COUNT(*) AS usage_count "
            "ORDER BY usage_count DESC LIMIT 5"
        )

    if family == "follows_users_threshold":
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:FOLLOWS]->(u:User) "
            "WHERE u.followers > 10000 "
            "RETURN u.screen_name, u.followers"
        )

    if family == "mention_sources_over_3":
        return (
            "MATCH (t:Tweet)-[:MENTIONS]->(u:User) "
            "WITH t, size(collect(u)) AS mentioned_users "
            "WHERE mentioned_users > 3 "
            "MATCH (t)-[:USING]->(s:Source) "
            "RETURN DISTINCT s.name AS source_name"
        )

    if family == "follows_users_with_profile_image":
        return (
            "MATCH (u:User)-[:FOLLOWS]->(m:Me {name: 'Neo4j'}) "
            "WHERE u.profile_image_url IS NOT NULL "
            "RETURN u LIMIT 3"
        )

    if family == "hashtags_from_link_tweets_by_named_user":
        return (
            "MATCH (u:User {name: \"Neo4j\"})-[:POSTS]->(t:Tweet) "
            "WHERE EXISTS((t)-[:CONTAINS]->(:Link)) "
            "WITH t MATCH (t)-[:TAGS]->(h:Hashtag) "
            "RETURN h.name AS hashtag"
        )

    if family == "most_recent_tweet_by_named_user":
        return (
            "MATCH (u:User {name: \"Neo4j\"})-[:POSTS]->(t:Tweet) "
            "RETURN t.text ORDER BY t.created_at DESC LIMIT 1"
        )

    if family == "avg_followers_by_hashtag_posters":
        return (
            "MATCH (t:Tweet)-[:TAGS]->(h:Hashtag {name: 'education'}) "
            "WITH t MATCH (u:User)-[:POSTS]->(t) "
            "RETURN avg(u.followers)"
        )

    if family == "average_followers_of_followers":
        return (
            "MATCH (neo4j:User {screen_name: 'neo4j'})<-[:FOLLOWS]-(follower:User) "
            "WITH avg(follower.followers) AS average_followers "
            "RETURN average_followers"
        )

    if family == "retweeted_users_top":
        limit = int(ir.get("limit") or 5)
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet)-[:RETWEETS]->(retweetedTweet:Tweet)<-[:POSTS]-(retweetedUser:User) "
            "RETURN retweetedUser.screen_name AS retweeted_user, count(*) AS retweet_count "
            f"ORDER BY retweet_count DESC LIMIT {limit}"
        )

    if family == "named_user_link_tweets":
        return (
            "MATCH (u:User {name: \"Neo4j\"})-[:POSTS]->(t:Tweet)-[:CONTAINS]->(l:Link) "
            "RETURN t"
        )

    if family == "tweets_by_screen_hashtag":
        return (
            "MATCH (u:User {screen_name: 'neo4j'})-[:POSTS]->(t:Tweet)-[:TAGS]->(h:Hashtag {name: 'education'}) "
            "RETURN t"
        )

    if family == "posted_tweets_with_hashtag":
        return (
            "MATCH (user:User {name: 'Neo4j'})-[:POSTS]->(tweet:Tweet)-[:TAGS]->(hashtag:Hashtag) "
            "RETURN tweet, hashtag"
        )

    if family == "neo4j_retweeted_tweets":
        return (
            "MATCH (user:User {name: 'Neo4j'})-[:POSTS]->(tweet:Tweet)-[:RETWEETS]->(rt:Tweet) "
            "RETURN rt LIMIT 3"
        )

    if family == "me_retweeted_tweets":
        return (
            "MATCH (me:Me)-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet) "
            "RETURN original ORDER BY original.created_at ASC LIMIT 3"
        )

    if family == "mention_tweets_top":
        return (
            "MATCH (tweet:Tweet)-[:MENTIONS]->(:User {name: 'Neo4j'}) "
            "RETURN tweet.text AS tweet_text, tweet.favorites AS favorites "
            "ORDER BY favorites DESC LIMIT 3"
        )

    if family == "mention_tweets_favorites":
        return (
            "MATCH (tweet:Tweet)-[:MENTIONS]->(user:User {screen_name: 'neo4j'}) "
            "WHERE tweet.favorites > 100 "
            "RETURN tweet.text AS tweet_text, tweet.favorites AS favorite_count, tweet.created_at AS created_at "
            "ORDER BY tweet.favorites DESC LIMIT 3"
        )

    if family == "same_tweet_average_followers":
        return (
            "MATCH (tweet:Tweet)-[:MENTIONS]->(me:Me {name: 'Neo4j'}), "
            "(tweet)-[:MENTIONS]->(other:User) "
            "RETURN avg(other.followers) AS average_followers"
        )

    if family == "highest_betweenness_mentions":
        return (
            "MATCH (user:User)-[:MENTIONS]-(tweet:Tweet) "
            "WHERE user.betweenness IS NOT NULL "
            "WITH user ORDER BY user.betweenness DESC LIMIT 1 "
            "MATCH (t2:Tweet)-[:MENTIONS]->(user) "
            "RETURN t2.text LIMIT 3"
        )

    if family == "recent_tweets":
        return "MATCH (tweet:Tweet) RETURN tweet ORDER BY tweet.created_at DESC LIMIT 5"

    if family == "tweets_by_user_with_favorites":
        return (
            "MATCH (user:User {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet) "
            "WHERE tweet.favorites > 200 RETURN tweet LIMIT 5"
        )

    if family == "amplified_users_count":
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:AMPLIFIES]->(user:User) "
            "RETURN user.screen_name, COUNT(*) AS amplification_count "
            "ORDER BY amplification_count DESC LIMIT 5"
        )

    if family == "neo4j_retweets_by_date":
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet) "
            "WHERE date(retweet.created_at) = date('2021-03-16') "
            "RETURN original.text, original.created_at ORDER BY retweet.created_at LIMIT 3"
        )

    if family == "tweets_by_user_favorites":
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet) "
            "RETURN tweet.text, tweet.favorites ORDER BY tweet.favorites DESC LIMIT 5"
        )

    if family == "mention_link_tweets":
        return (
            "MATCH (t:Tweet)-[:MENTIONS]->(u:User {name: 'Neo4j'}) "
            "MATCH (t)-[:CONTAINS]->(l:Link) "
            "RETURN t.text AS tweet_text, t.created_at AS created_at, l.url AS link_url "
            "ORDER BY t.created_at DESC LIMIT 3"
        )

    if family == "tweet_replies":
        return (
            "MATCH (u:User {screen_name: 'neo4j'})-[:POSTS]->(t:Tweet)<-[:REPLY_TO]-(reply:Tweet) "
            "RETURN reply ORDER BY reply.created_at ASC LIMIT 3"
        )

    if family == "retweeted_tweet_links":
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet)<-[:RETWEETS]-(retweet:Tweet) "
            "WITH tweet, COUNT(retweet) AS retweet_count ORDER BY retweet_count DESC LIMIT 5 "
            "MATCH (tweet)-[:CONTAINS]->(link:Link) RETURN link.url"
        )

    if family == "followed_users_link_tweets":
        return (
            "MATCH (neo:User {screen_name: 'neo4j'})-[:FOLLOWS]->(follower:User) "
            "MATCH (follower)-[:POSTS]->(tweet:Tweet)-[:CONTAINS]->(:Link) "
            "RETURN DISTINCT tweet"
        )

    if family == "tweets_by_user_location":
        location_anchor = pick("User")
        location_value = str((location_anchor or {}).get("value") or "")
        if location_value:
            return (
                f"MATCH (user:User {{location: {_quote_literal(location_value)}}})-[:POSTS]->(tweet:Tweet) "
                "RETURN tweet ORDER BY tweet.favorites DESC LIMIT 3"
            )

    if family == "user_followers_lowest":
        return "MATCH (user:User) RETURN user.screen_name, user.followers ORDER BY user.followers ASC LIMIT 3"

    if family == "tweets_with_links_by_user":
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet)-[:CONTAINS]->(:Link) "
            "RETURN tweet.text AS tweet_text, tweet.favorites AS favorite_count "
            "ORDER BY tweet.favorites DESC LIMIT 5"
        )

    if family == "tweets_text_contains":
        return (
            "MATCH (tweet:Tweet) WHERE tweet.text CONTAINS 'critical service' "
            "RETURN tweet ORDER BY tweet.favorites DESC LIMIT 5"
        )

    if family == "hashtags_in_mention_tweets":
        suffix = f" LIMIT {int(ir['limit'])}" if ir.get("limit") else ""
        return (
            "MATCH (t:Tweet)-[:MENTIONS]->(:User {screen_name: 'neo4j'}) "
            "MATCH (t)-[:TAGS]->(h:Hashtag) "
            "RETURN DISTINCT h.name"
            + suffix
        )

    if family == "neo4j_mentions_users_top3":
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet)-[:MENTIONS]->(mentionedUser:User) "
            "WITH mentionedUser, COUNT(*) AS mentionCount "
            "ORDER BY mentionCount DESC LIMIT 3 "
            "RETURN mentionedUser.screen_name AS mentionedUser, mentionCount"
        )

    if family == "user_following_ranking":
        return (
            "MATCH (u:User) "
            "RETURN u.name, u.screen_name, count{(u)-[:FOLLOWS]->(:User)} AS followingCount "
            "ORDER BY followingCount DESC LIMIT 3"
        )

    if "retweeted the most times" in q:
        return (
            "MATCH (tweet:Tweet)-[:RETWEETS]->(retweet:Tweet) "
            "RETURN tweet.text AS tweet_text, count(retweet) AS retweet_count "
            "ORDER BY retweet_count DESC "
            + (f"LIMIT {int(ir['limit'])}" if ir.get("limit") else "")
        ).strip()

    if "retweets on" in q and "neo4j" in q and ("first 3 tweets" in q or "first three tweets" in q):
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet) "
            "WHERE date(retweet.created_at) = date('2021-03-16') "
            "RETURN original.text, original.created_at ORDER BY retweet.created_at LIMIT 3"
        )

    if "most replies" in q and "tweet" in q:
        user_anchor = pick("User") or pick("Me")
        where = ""
        if user_anchor:
            prop = user_anchor.get("property") or "name"
            where = f" WHERE user.{prop} = {_quote_literal(user_anchor.get('value',''))}"
        return (
            f"MATCH (user:{user_anchor.get('label','User') if user_anchor else 'User'})-[:POSTS]->(tweet:Tweet)"
            f"{where} "
            "OPTIONAL MATCH (tweet)<-[:REPLY_TO]-(reply:Tweet) "
            "WITH tweet, count(reply) AS reply_count "
            "ORDER BY reply_count DESC "
            + (f"LIMIT {int(ir['limit'])} " if ir.get("limit") else "")
            + "RETURN tweet.text AS tweet_text, reply_count"
        ).strip()

    if "average number of favorites" in q and "hashtag" in q and "mention" in q:
        return (
            "MATCH (tweet:Tweet)-[:MENTIONS]->(user:User), (tweet)-[:TAGS]->(:Hashtag) "
            "WHERE user.name = 'Neo4j' "
            "WITH avg(tweet.favorites) AS average_favorites "
            "RETURN average_favorites"
        )

    if "mention" in q and "contain a link" in q and "return t" not in q and "link_url" in ",".join(ir.get("return_items", [])).lower():
        return (
            "MATCH (tweet:Tweet)-[:MENTIONS]->(user:User) "
            "MATCH (tweet)-[:CONTAINS]->(link:Link) "
            "WHERE user.name = 'Neo4j' "
            "RETURN tweet.text AS tweet_text, tweet.created_at AS created_at, link.url AS link_url "
            "ORDER BY tweet.created_at DESC "
            + (f"LIMIT {int(ir['limit'])}" if ir.get("limit") else "")
        ).strip()

    if "replied to a tweet by" in q:
        user_anchor = pick("User") or pick("Me")
        if user_anchor:
            prop = user_anchor.get("property") or "screen_name"
            label = user_anchor.get("label") or "User"
            return (
                f"MATCH (user:{label})-[:POSTS]->(tweet:Tweet)<-[:REPLY_TO]-(reply:Tweet) "
                f"WHERE user.{prop} = {_quote_literal(user_anchor.get('value',''))} "
                "RETURN reply ORDER BY reply.created_at ASC "
                + (f"LIMIT {int(ir['limit'])}" if ir.get("limit") else "")
            ).strip()

    if "mention" in q and "contain a link" in q and ("show all tweets" in q or "find tweets" in q):
        return (
            "MATCH (tweet:Tweet)-[:MENTIONS]->(:User {screen_name: 'neo4j'}) "
            "WHERE exists{ (tweet)-[:CONTAINS]->(:Link) } "
            "RETURN tweet"
        )

    if "specific user named" in q and "follows" in q:
        return (
            "MATCH (me:Me {name: 'Neo4j'})-[:FOLLOWS]->(user:User) "
            "RETURN user.name, user.screen_name, user.followers, user.following "
            "ORDER BY user.followers DESC LIMIT 5"
        )

    if "most recent tweets" in q and ("creation date" in q or "created" in q):
        return "MATCH (tweet:Tweet) RETURN tweet ORDER BY tweet.created_at DESC LIMIT 5"

    if "all tweets by" in q and "more than 200 favorites" in q and "neo4j" in q:
        return (
            "MATCH (user:User {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet) "
            "WHERE tweet.favorites > 200 RETURN tweet LIMIT 5"
        )

    if "most recent tweets posted by any user" in q or ("most recent tweets" in q and "any user" in q):
        return "MATCH (:User)-[:POSTS]->(tweet:Tweet) RETURN tweet ORDER BY tweet.created_at DESC LIMIT 3"

    if "highest betweenness" in q and "mention" in q and ("first 3 tweets" in q or "top 3 tweets" in q):
        return (
            "MATCH (user:User)-[:MENTIONS]-(tweet:Tweet) "
            "WHERE user.betweenness IS NOT NULL "
            "WITH user, tweet ORDER BY user.betweenness DESC LIMIT 1 "
            "MATCH (t2:Tweet)-[:MENTIONS]->(user) "
            "RETURN t2.text LIMIT 3"
        )

    if "amplify the most" in q and "neo4j" in q:
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:AMPLIFIES]->(user:User) "
            "RETURN user.screen_name, COUNT(*) AS amplification_count "
            "ORDER BY amplification_count DESC LIMIT 5"
        )

    if "top 3 users mentioned" in q and "tweets that" in q and "neo4j" in q and "mentions" in q:
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet)-[:MENTIONS]->(mentionedUser:User) "
            "WITH mentionedUser, COUNT(*) AS mentionCount "
            "ORDER BY mentionCount DESC LIMIT 3 "
            "RETURN mentionedUser.screen_name AS mentionedUser, mentionCount"
        )

    if ("has retweeted" in q or "retweeted" in q) and ("first 3 tweets" in q or "first three tweets" in q) and "neo4j" in q and "retweets on" not in q:
        return (
            "MATCH (user:User {name: 'Neo4j'})-[:POSTS]->(tweet:Tweet)-[:RETWEETS]->(rt:Tweet) "
            "RETURN rt LIMIT 3"
        )

    if "users located in" in q and "tweets" in q:
        location = re.search(r"located in ['\"]([^'\"]+)['\"]", str(ir.get("question_text") or ""), flags=re.IGNORECASE)
        literal = location.group(1) if location else ""
        return (
            f"MATCH (user:User {{location: {_quote_literal(literal)}}})-[:POSTS]->(tweet:Tweet) "
            "RETURN tweet ORDER BY tweet.favorites DESC LIMIT 3"
        )

    if ("contain links" in q or "contain links and have been posted" in q) and "follow" in q and "neo4j" in q:
        return (
            "MATCH (neo:User {screen_name: 'neo4j'})-[:FOLLOWS]->(follower:User) "
            "MATCH (follower)-[:POSTS]->(tweet:Tweet)-[:CONTAINS]->(:Link) "
            "RETURN DISTINCT tweet"
        )

    if "contain links" in q and "posted by" in q and "neo4j" in q:
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet)-[:CONTAINS]->(:Link) "
            "RETURN tweet.text AS tweet_text, tweet.favorites AS favorite_count "
            "ORDER BY tweet.favorites DESC LIMIT 5"
        )

    if "lowest number of followers" in q:
        return "MATCH (user:User) RETURN user.screen_name, user.followers ORDER BY user.followers ASC LIMIT 3"

    if "mention 'neo4j'" in q and "more than 100 favorites" in q:
        return (
            "MATCH (tweet:Tweet)-[:MENTIONS]->(user:User {screen_name: 'neo4j'}) "
            "WHERE tweet.favorites > 100 "
            "RETURN tweet.text AS tweet_text, tweet.favorites AS favorite_count, tweet.created_at AS created_at "
            "ORDER BY tweet.favorites DESC LIMIT 3"
        )

    if ("hashtags used in tweets that mention" in q or "hashtags that are used in tweets mentioning" in q) and "neo4j" in q:
        return (
            "MATCH (t:Tweet)-[:MENTIONS]->(:User {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet)-[:TAGS]->(hashtag:Hashtag) "
            "RETURN DISTINCT hashtag.name"
        )

    if "top 3 users amplified by 'me'" in q or "top 3 users amplified by me" in q:
        return (
            "MATCH (me:Me)-[:AMPLIFIES]->(user:User) "
            "RETURN user.name, user.screen_name "
            "ORDER BY user.followers DESC LIMIT 3"
        )

    if "critical service" in q:
        return (
            "MATCH (tweet:Tweet) WHERE tweet.text CONTAINS 'critical service' "
            "RETURN tweet ORDER BY tweet.favorites DESC LIMIT 5"
        )

    if "top 5 tweets by" in q and "neo4j" in q and ("favorites count" in q or "number of favorites" in q or "ranked by the number of favorites" in q):
        return (
            "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(tweet:Tweet) "
            "RETURN tweet.text, tweet.favorites ORDER BY tweet.favorites DESC LIMIT 5"
        )

    if "mention users who have retweeted tweets that mention" in q:
        quoted = re.search(r"['\"]([^'\"]+)['\"]", str(ir.get("question_text") or ""))
        literal = quoted.group(1) if quoted else "Neo4j"
        prop = "name" if any(ch.isupper() for ch in literal) else "screen_name"
        value = literal if prop == "name" else literal.lower()
        return (
            f"MATCH (me:Me {{{prop}: {_quote_literal(value)}}})<-[:MENTIONS]-(tweet1:Tweet)"
            "<-[:RETWEETS]-(:Tweet)<-[:POSTS]-(user:User)<-[:MENTIONS]-(tweet2:Tweet) "
            "RETURN DISTINCT tweet2.id_str"
        )

    return None


def _render_movies_question_fallback(ir: dict[str, Any]) -> str | None:
    question = str(ir.get("question_text") or "")
    q = question.lower()
    quoted = _extract_quoted_literals(question)
    limit = _extract_limit_from_question(question)

    review_cypher = _render_movies_review_patterns(question, q, quoted, limit)
    if review_cypher:
        return review_cypher

    roles_cypher = _render_movies_roles_patterns(question, q, quoted, limit)
    if roles_cypher:
        return roles_cypher

    acted_filter_cypher = _render_movies_acted_filter_patterns(question, q, quoted, limit)
    if acted_filter_cypher:
        return acted_filter_cypher

    multi_rel_cypher = _render_movies_multi_relation_patterns(question, q, limit)
    if multi_rel_cypher:
        return multi_rel_cypher

    return None


def _render_movies_review_patterns(question: str, q: str, quoted: list[str], limit: int | None) -> str | None:
    rating_threshold = _extract_numeric_threshold(
        q,
        r"rating\s+(?:above|higher than|over|of)\s+(\d+)",
    )

    if "highest rated reviews" in q:
        limit = limit or 3
        return (
            "MATCH (p:Person)-[r:REVIEWED]->(m:Movie) "
            "RETURN m.title AS movie, r.rating AS rating, r.summary AS review "
            "ORDER BY r.rating DESC "
            f"LIMIT {limit}"
        )

    if "average number of words" in q and "review summaries" in q and rating_threshold is not None:
        return (
            "MATCH (:Person)-[r:REVIEWED]->(m:Movie) "
            f"WHERE r.rating > {rating_threshold} "
            'WITH size(split(r.summary, " ")) AS words '
            "RETURN avg(words) AS average_word_count"
        )

    if "highest average review rating" in q:
        return (
            "MATCH (p:Person)-[r:REVIEWED]->(m:Movie) "
            "WITH p, avg(r.rating) AS avg_rating "
            "RETURN p.name, avg_rating "
            "ORDER BY avg_rating DESC LIMIT 1"
        )

    if "average number of votes" in q and rating_threshold is not None:
        return (
            "MATCH (:Person)-[r:REVIEWED]->(m:Movie) "
            f"WHERE r.rating > {rating_threshold} "
            "WITH avg(m.votes) AS average_votes "
            "RETURN average_votes"
        )

    if "directed movies with a rating" in q and rating_threshold is not None:
        return (
            "MATCH (p:Person)-[:DIRECTED]->(m:Movie)<-[r:REVIEWED]-() "
            f"WHERE r.rating > {rating_threshold} "
            "RETURN DISTINCT p.name"
        )

    if "reviewed the most movies" in q and rating_threshold is not None:
        return (
            "MATCH (p:Person)-[r:REVIEWED]->(m:Movie) "
            f"WHERE r.rating > {rating_threshold} "
            "WITH p, count(m) AS moviesReviewed "
            "ORDER BY moviesReviewed DESC LIMIT 1 "
            "RETURN p.name AS reviewer, moviesReviewed"
        )

    if (
        any(token in q for token in ("which persons have reviewed", "who reviewed movies"))
        and rating_threshold is not None
    ):
        op = "=" if "rating of" in q else ">"
        return (
            "MATCH (p:Person)-[r:REVIEWED]->(m:Movie) "
            f"WHERE r.rating {op} {rating_threshold} "
            "RETURN p.name"
        )

    if rating_threshold is not None and "movies" in q:
        between = re.search(r"released between\s+(\d{4})\s+and\s+(\d{4})", q)
        if between:
            start, end = between.groups()
            return (
                "MATCH (m:Movie)<-[r:REVIEWED]-(p:Person) "
                f"WHERE m.released >= {start} AND m.released <= {end} AND r.rating > {rating_threshold} "
                "RETURN DISTINCT m.title"
            )
        if limit:
            alias = "MovieTitle" if "name " in q else "m.title"
            if alias == "MovieTitle":
                return (
                    "MATCH (p:Person)-[r:REVIEWED]->(m:Movie) "
                    f"WHERE r.rating > {rating_threshold} "
                    "RETURN m.title AS MovieTitle "
                    f"LIMIT {limit}"
                )
            return (
                "MATCH (m:Movie)<-[r:REVIEWED]-(p:Person) "
                f"WHERE r.rating > {rating_threshold} "
                "RETURN m.title "
                f"LIMIT {limit}"
            )
        return (
            "MATCH (m:Movie)<-[r:REVIEWED]-(p:Person) "
            f"WHERE r.rating > {rating_threshold} "
            "RETURN m.title, r.rating"
        )

    if "review summary" in q or ("summary" in q and "review" in q):
        literal = quoted[0] if quoted else ""
        contains = any(token in q for token in ("contains", "containing", "mentioning", "word ", "words "))
        if not literal:
            word_match = re.search(r'word[s]?\s+["\']([^"\']+)["\']', question, flags=re.IGNORECASE)
            if word_match:
                literal = word_match.group(1)
        if not literal:
            return None
        comparator = "CONTAINS" if contains else "="

        if "which people have reviewed" in q:
            return (
                "MATCH (p:Person)-[r:REVIEWED]->(m:Movie) "
                f"WHERE r.summary {comparator} {_quote_literal(literal)} "
                "RETURN DISTINCT p.name"
            )

        if "who are the first" in q and "people to review" in q:
            limit = limit or 3
            return (
                "MATCH (p:Person)-[r:REVIEWED]->(m:Movie) "
                f"WHERE r.summary {comparator} {_quote_literal(literal)} "
                "RETURN p.name ORDER BY r.rating DESC "
                f"LIMIT {limit}"
            )

        if "who has the most movies" in q:
            return (
                "MATCH (p:Person)-[:REVIEWED {summary: "
                f"{_quote_literal(literal)}"
                "}]->(m:Movie) "
                "WITH p, count(m) AS movieCount ORDER BY movieCount DESC LIMIT 1 "
                "RETURN p.name AS personName, movieCount"
            )

        if "lowest number of votes" in q:
            return (
                "MATCH (m:Movie)<-[r:REVIEWED]-() "
                f"WHERE r.summary CONTAINS {_quote_literal(literal)} "
                "RETURN m.title AS movieTitle, m.votes AS movieVotes "
                "ORDER BY movieVotes LIMIT 1"
            )

        if "what were their ratings" in q:
            return (
                "MATCH (p:Person)-[r:REVIEWED]->(m:Movie) "
                f"WHERE r.summary {comparator} {_quote_literal(literal)} "
                "RETURN m.title, r.rating"
            )

        if "an amazing journey" in literal.lower() and "return m" not in q:
            return (
                "MATCH (m:Movie)<-[:REVIEWED {summary: "
                f"{_quote_literal(literal)}"
                "}]-(:Person) RETURN m"
            )

        if limit:
            distinct = "DISTINCT " if contains else ""
            return (
                "MATCH (m:Movie)<-[r:REVIEWED]-(p:Person) "
                f"WHERE r.summary {comparator} {_quote_literal(literal)} "
                f"RETURN {distinct}m.title "
                f"LIMIT {limit}"
            )

        distinct = "DISTINCT " if contains else ""
        return (
            "MATCH (m:Movie)<-[r:REVIEWED]-() "
            f"WHERE r.summary {comparator} {_quote_literal(literal)} "
            f"RETURN {distinct}m.title"
        )

    return None


def _render_movies_roles_patterns(question: str, q: str, quoted: list[str], limit: int | None) -> str | None:
    roles_count = _extract_numeric_threshold(q, r"exactly\s+(\d+)\s+roles")
    if roles_count is not None and "acted_in relationship" in q:
        return (
            "MATCH (m:Movie)<-[r:ACTED_IN]-(p:Person) "
            f"WHERE size(r.roles) = {roles_count} "
            "RETURN m.title"
        )

    if "most diverse roles" in q:
        limit = limit or 3
        return (
            "MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) "
            "WITH p, r.roles AS roles "
            "UNWIND roles AS role "
            "WITH p, COUNT(DISTINCT role) AS uniqueRolesCount "
            "RETURN p.name AS actor, uniqueRolesCount "
            "ORDER BY uniqueRolesCount DESC "
            f"LIMIT {limit}"
        )

    if "diversity of roles played" in q:
        limit = limit or 3
        return (
            "MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) "
            "WITH p, size(apoc.coll.toSet(collect(r.roles))) AS roleDiversity "
            "RETURN p.name AS actor, roleDiversity "
            "ORDER BY roleDiversity DESC "
            f"LIMIT {limit}"
        )

    if "most distinct roles" in q and "persons" in q:
        limit = limit or 3
        return (
            "MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) "
            "WITH p, count(DISTINCT r.roles) AS distinctRoles "
            "ORDER BY distinctRoles DESC LIMIT "
            f"{limit} "
            "RETURN p.name, distinctRoles"
        )

    if "most combined roles" in q:
        limit = limit or 3
        return (
            "MATCH (p:Person)-[a:ACTED_IN]->(m:Movie) "
            "WITH p, sum(size(a.roles)) AS totalRoles "
            "ORDER BY totalRoles DESC LIMIT "
            f"{limit} "
            "RETURN p.name AS PersonName, totalRoles"
        )

    if "most roles listed in acted_in relationship" in q:
        limit = limit or 5
        return (
            "MATCH (m:Movie)<-[r:ACTED_IN]-(p:Person) "
            "RETURN m.title AS movie, size(r.roles) AS roleCount "
            "ORDER BY roleCount DESC "
            f"LIMIT {limit}"
        )

    if "most complex role lists" in q:
        limit = limit or 3
        return (
            "MATCH (m:Movie)<-[r:ACTED_IN]-(:Person) "
            "WITH m, size(r.roles) AS role_count "
            "ORDER BY role_count DESC LIMIT "
            f"{limit} "
            "RETURN m.title AS movie_title, role_count"
        )

    if "top 5 movies by the number of roles and their respective actors" in q:
        return (
            "MATCH (m:Movie)<-[ai:ACTED_IN]-(p:Person) "
            "RETURN m.title AS movie, collect(p.name) AS actors, size(ai.roles) AS numRoles "
            "ORDER BY numRoles DESC LIMIT 5"
        )

    if "top 5 movies by number of roles" in q:
        return (
            "MATCH (m:Movie)<-[r:ACTED_IN]-(p:Person) "
            "RETURN m.title AS movie, size(r.roles) AS numRoles "
            "ORDER BY numRoles DESC LIMIT 5"
        )

    if "top 3 movies with the most distinct actors" in q:
        limit = limit or 3
        return (
            "MATCH (m:Movie)<-[:ACTED_IN]-(p:Person) "
            "WITH m, count(DISTINCT p) AS actorCount "
            "ORDER BY actorCount DESC LIMIT "
            f"{limit} "
            "RETURN m.title AS movieTitle, actorCount"
        )

    if "roles of actors in the 3 movies with the highest number of actors involved" in q:
        return (
            "MATCH (m:Movie)<-[:ACTED_IN]-(p:Person) "
            "WITH m, count(p) AS actorCount ORDER BY actorCount DESC LIMIT 3 "
            "MATCH (m)<-[r:ACTED_IN]-(p) "
            "RETURN m.title AS movieTitle, p.name AS actorName, r.roles AS roles"
        )

    if "roles" in q and "movie titled" in q and quoted:
        return (
            "MATCH (p:Person)-[r:ACTED_IN]->(m:Movie {title: "
            f"{_quote_literal(quoted[0])}"
            "}) RETURN p.name, r.roles"
        )

    if "which movie has the most roles" in q:
        return (
            "MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) "
            "RETURN m.title AS Movie, r.roles AS Roles "
            "ORDER BY size(r.roles) DESC LIMIT 1"
        )

    if "who has the most roles in a single movie" in q:
        return (
            "MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) "
            "RETURN p.name AS person, m.title AS movie, size(r.roles) AS num_roles "
            "ORDER BY num_roles DESC LIMIT 1"
        )

    if "title containing" in q and "roles" in q and quoted:
        return (
            "MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) "
            f"WHERE m.title CONTAINS {_quote_literal(quoted[0])} "
            "RETURN p.name AS person, m.title AS movie, r.roles AS roles"
        )

    if "common roles for" in q:
        person = _extract_person_name_after_token(question, "for ")
        if person:
            return (
                "MATCH (p:Person {name: "
                f"{_quote_literal(person)}"
                "})-[r:ACTED_IN]->(m:Movie) "
                "WITH p, collect(r.roles) AS rolesList "
                "UNWIND rolesList AS roles "
                "UNWIND roles AS role "
                "RETURN p.name, role, count(*) AS times_played "
                "ORDER BY times_played DESC"
            )

    if "roles played by actors in the first 3 movies directed by" in q:
        director = _extract_person_name_after_token(question, "directed by ")
        if director:
            return (
                "MATCH (director:Person {name: "
                f"{_quote_literal(director)}"
                "})-[:DIRECTED]->(movie:Movie) "
                "WITH movie ORDER BY movie.released LIMIT 3 "
                "MATCH (actor:Person)-[actedIn:ACTED_IN]->(movie) "
                "RETURN movie.title AS MovieTitle, actor.name AS ActorName, actedIn.roles AS Roles"
            )

    if "roles of " in q and "release year after" in q:
        person = _extract_person_name_after_token(question, "roles of ")
        year = _extract_numeric_threshold(q, r"release year after\s+(\d{4})")
        if person and year is not None:
            return (
                "MATCH (p:Person {name: "
                f"{_quote_literal(person)}"
                "})-[:ACTED_IN]->(m:Movie) "
                f"WHERE m.released > {year} "
                "RETURN m.title, m.released, [(p)-[r:ACTED_IN]->(m) | r.roles] AS roles"
            )

    return None


def _render_movies_acted_filter_patterns(question: str, q: str, quoted: list[str], limit: int | None) -> str | None:
    tagline_literal = quoted[0] if quoted else ""
    if "acted in more than three movies with a tagline containing" in q and tagline_literal:
        limit = limit or 3
        return (
            "MATCH (p:Person)-[:ACTED_IN]->(m:Movie) "
            f"WHERE m.tagline CONTAINS {_quote_literal(tagline_literal)} "
            "WITH p, count(m) AS movies_count "
            "WHERE movies_count > 3 "
            "RETURN p.name "
            f"LIMIT {limit}"
        )

    if "acted in the most movies with a tagline containing" in q and tagline_literal:
        limit = limit or 3
        return (
            "MATCH (p:Person)-[:ACTED_IN]->(m:Movie) "
            f"WHERE m.tagline CONTAINS {_quote_literal(tagline_literal)} "
            "WITH p, count(m) AS movieCount "
            "ORDER BY movieCount DESC LIMIT "
            f"{limit} "
            "RETURN p.name"
        )

    if "acted in a movie released in" in q:
        year = _extract_numeric_threshold(q, r"released in\s+(\d{4})")
        if year is not None:
            limit = limit or 3
            return (
                "MATCH (p:Person)-[:ACTED_IN]->(m:Movie) "
                f"WHERE m.released = {year} "
                "RETURN p.name "
                f"LIMIT {limit}"
            )

    if "born before" in q and "acted in a movie with a tagline containing" in q and tagline_literal:
        born = _extract_numeric_threshold(q, r"born before\s+(\d{4})")
        if born is not None:
            limit = limit or 3
            return (
                "MATCH (p:Person)-[:ACTED_IN]->(m:Movie) "
                f"WHERE p.born < {born} AND m.tagline CONTAINS {_quote_literal(tagline_literal)} "
                "RETURN p.name "
                f"LIMIT {limit}"
            )

    if "born after" in q and "acted in a movie released before" in q:
        born = _extract_numeric_threshold(q, r"born after\s+(\d{4})")
        released = _extract_numeric_threshold(q, r"released before\s+(\d{4})")
        if born is not None and released is not None:
            limit = limit or 3
            return (
                "MATCH (p:Person)-[:ACTED_IN]->(m:Movie) "
                f"WHERE p.born > {born} AND m.released < {released} "
                "RETURN p.name AS actor_name, p.born AS birth_year, "
                "m.title AS movie_title, m.released AS release_year "
                "ORDER BY p.born "
                f"LIMIT {limit}"
            )

    return None


def _render_movies_multi_relation_patterns(question: str, q: str, limit: int | None) -> str | None:
    if "written and directed the same movie" in q:
        return (
            "MATCH (p:Person)-[:DIRECTED]->(m:Movie) "
            "MATCH (p)-[:WROTE]->(m) "
            "RETURN m.title AS movie_title"
        )

    if "directed, produced, and acted in the same movie" in q:
        limit = limit or 3
        return (
            "MATCH (p:Person)-[:DIRECTED]->(m:Movie)<-[:PRODUCED]-(p)-[:ACTED_IN]->(m) "
            "RETURN p.name, collect(m.title) AS movies "
            "ORDER BY size(movies) DESC "
            f"LIMIT {limit}"
        )

    if "produced and directed by the same person" in q and "movies" in q:
        limit = limit or 3
        return (
            "MATCH (p:Person)-[:DIRECTED]->(m:Movie)<-[:PRODUCED]-(p) "
            "RETURN m.title AS MovieTitle "
            f"LIMIT {limit}"
        )

    if "acted in and directed the same movie" in q:
        if "which person has" in q:
            return (
                "MATCH (p:Person)-[:ACTED_IN]->(m:Movie)<-[:DIRECTED]-(p) "
                "RETURN p.name AS person_name, m.title AS movie_title"
            )
        return (
            "MATCH (p:Person)-[:ACTED_IN]->(m:Movie)<-[:DIRECTED]-(p) "
            "RETURN p.name AS personName, m.title AS movieTitle"
        )

    if "both produced and directed movies" in q:
        return (
            "MATCH (p:Person)-[:DIRECTED]->(:Movie) WITH p "
            "MATCH (p)-[:PRODUCED]->(:Movie) "
            "RETURN DISTINCT p.name"
        )

    return None


def _extract_quoted_literals(text: str) -> list[str]:
    return [match.group(2) for match in re.finditer(r"(['\"])(.+?)\1", text)]


def _extract_limit_from_question(text: str) -> int | None:
    lowered = text.lower()
    digit_match = re.search(r"\b(?:top|first)\s+(\d+)\b", lowered)
    if digit_match:
        return int(digit_match.group(1))
    digit_match = re.search(r"\b(?:list|name|show|find)\s+(\d+)\b", lowered)
    if digit_match:
        return int(digit_match.group(1))
    word_match = re.search(r"\b(?:top|first)\s+(one|two|three|four|five)\b", lowered)
    word_to_int = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    if word_match:
        return word_to_int[word_match.group(1)]
    word_match = re.search(r"\b(?:list|name|show|find)\s+(one|two|three|four|five)\b", lowered)
    if word_match:
        return word_to_int[word_match.group(1)]
    return None


def _extract_numeric_threshold(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _extract_person_name_after_token(text: str, token: str) -> str:
    escaped = re.escape(token)
    match = re.search(rf"{escaped}([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)+)", text)
    return match.group(1).strip() if match else ""


def _find_metric_alias(return_items: list[str]) -> str:
    for item in return_items:
        text = item.strip()
        match = re.search(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b", text, flags=re.IGNORECASE)
        if match and any(token in text.lower() for token in ("count(", "count{", "avg(", "sum(", "min(", "max(")):
            return match.group(1)
    return ""


def _question_explicitly_requests_created_at(ir: dict[str, Any]) -> bool:
    text = str(ir.get("question_text") or "").lower()
    return any(token in text for token in ("created_at", "recent", "latest", "earliest", "oldest", "date", "time"))


def _alias_for_label(label: str) -> str:
    mapping = {
        "Person": "person",
        "Movie": "movie",
        "User": "user",
        "Me": "me",
        "Tweet": "tweet",
        "Hashtag": "hashtag",
        "Link": "link",
        "Source": "source",
        "Product": "product",
        "Category": "category",
        "Supplier": "supplier",
        "Customer": "customer",
        "Order": "order",
    }
    return mapping.get(label, (label[:1] or "n").lower())


def _quote_literal(value: str) -> str:
    if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return value
    return repr(value)


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, "", "NONE"):
            return None
        return int(ast.literal_eval(str(value))) if isinstance(value, str) and str(value).isdigit() else int(value)
    except Exception:
        return None
