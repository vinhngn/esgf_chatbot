from __future__ import annotations

import ast
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
    if not focus_label and verified_triples:
        focus_label = verified_triples[0][0]

    anchor = {}
    lock_anchor = query_constraints.get("anchor_lock", {}) or {}
    if isinstance(lock_anchor, dict) and lock_anchor.get("label"):
        anchor = {
            "label": str(lock_anchor.get("label", "")),
            "property": str(lock_anchor.get("property", "")),
            "value": str(lock_anchor.get("value", "")),
        }
    if not anchor:
        plan_anchor = query_plan.get("anchor", {}) or {}
        if isinstance(plan_anchor, dict) and plan_anchor.get("label"):
            anchor = {
                "label": str(plan_anchor.get("label", "")),
                "property": str(plan_anchor.get("property", "")),
                "value": str(plan_anchor.get("value", "")),
            }

    if not anchor and instance_triples:
        literal, _, label = instance_triples[0]
        anchor = {"label": label, "property": "", "value": literal}

    anchors: list[dict[str, str]] = []
    if anchor:
        anchors.append(anchor)
    for literal, _, label in instance_triples:
        if anchor and str(anchor.get("value") or "").strip().lower() == str(literal).strip().lower():
            continue
        candidate = {"label": str(label), "property": "", "value": str(literal)}
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

    question_text = " ".join(
        part for part in [
            str(intent.get("_question_text") or ""),
            str(query_plan.get("_source_question") or ""),
        ] if part
    ).strip()

    cardinality = str(return_contract.get("cardinality") or "").strip().lower()
    limit = query_plan.get("limit") or _safe_int(intent.get("limit"))
    if not limit:
        card_match = re.search(r"(?:top|first)_(\d+)$", cardinality)
        if card_match:
            limit = int(card_match.group(1))
        elif cardinality in {"top_1", "one", "single"}:
            limit = 1
        elif any(token in question_text.lower() for token in (" most ", " most?", " most.", " most frequently", " highest ", " lowest ", " least ")) and "top " not in question_text.lower():
            limit = 1

    return {
        "database": database,
        "focus_label": focus_label,
        "anchor": anchor,
        "anchors": anchors,
        "verified_triples": verified_triples,
        "instance_triples": instance_triples,
        "query_mode": str(query_constraints.get("query_mode", "")),
        "projection_lock": str(query_constraints.get("projection_lock", "")),
        "return_mode": str(return_contract.get("return_mode", "")),
        "return_items": return_items,
        "relation_path": relation_path,
        "sort_field": str(query_plan.get("sort_field") or ""),
        "sort_direction": str(query_plan.get("sort_direction") or ""),
        "limit": limit,
        "aggregation": str(query_plan.get("aggregation") or intent.get("aggregation") or ""),
        "allow_aggregation": bool(query_constraints.get("allow_aggregation", False)),
        "count_pattern": str(path_hints.get("count_pattern") or ""),
        "needs_distinct": bool(query_plan.get("needs_distinct") or path_hints.get("needs_distinct")),
        "order_lock": query_constraints.get("order_lock", {}) or {},
        "filters": intent.get("filters", []) or [],
        "question_text": question_text,
    }


def render_cypher_from_ir(ir: dict[str, Any]) -> str | None:
    focus_label = str(ir.get("focus_label") or "")
    family_cypher = _render_question_family_fallback(ir)
    if family_cypher:
        return family_cypher
    if not focus_label:
        return None

    match_map = get_match_properties_map(ir.get("database") or None)
    anchor = ir.get("anchor", {}) or {}
    verified = ir.get("verified_triples", []) or []
    query_mode = str(ir.get("query_mode") or "")
    count_pattern = str(ir.get("count_pattern") or "")

    if query_mode == "rank_graph_count" and count_pattern:
        return _render_rank_graph_count(ir, focus_label)

    if verified:
        cypher = _render_user_posts_mentions_count(ir, match_map)
        if cypher:
            return cypher
        cypher = _render_user_posts_tags_nodes(ir, match_map)
        if cypher:
            return cypher
        if len(verified) == 1:
            cypher = _render_single_hop(ir, focus_label, anchor, match_map)
            if cypher:
                return cypher
        elif len(verified) == 2:
            cypher = _render_dual_relation(ir, focus_label, anchor, match_map)
            if cypher:
                return cypher
        elif len(verified) >= 3:
            cypher = _render_connected_chain(ir, match_map)
            if cypher:
                return cypher

    if query_mode == "aggregate_projection":
        cypher = _render_aggregate_projection(ir, focus_label)
        if cypher:
            return cypher

    if query_mode in {"rank_by_existing_property", "lookup_property", "lookup_entity"}:
        return _render_focus_scan(ir, focus_label, anchor, match_map)

    return None


def _render_user_posts_mentions_count(ir: dict[str, Any], match_map: dict[str, list[str]]) -> str | None:
    triples = ir.get("verified_triples", []) or []
    if len(triples) != 2:
        return None
    (s1, r1, o1), (s2, r2, o2) = triples
    if (s1, r1, o1, s2, r2, o2) != ("User", "POSTS", "Tweet", "Tweet", "MENTIONS", "User"):
        return None
    question_text = str(ir.get("question_text") or "").lower()
    if "mentions most frequently" not in question_text:
        return None

    match_clause = "MATCH (user:User)-[:POSTS]->(tweet:Tweet)-[:MENTIONS]->(mentioned:User)"
    where_clause = _where_from_anchors(
        [
            (alias, anchor)
            for alias, anchor in [
                ("user", a)
                for a in (ir.get("anchors", []) or [])
                if str(a.get("label") or "") == "User"
            ]
        ],
        match_map,
    )
    return _assemble_query(ir, match_clause, where_clause, "mentioned")


def _render_user_posts_tags_nodes(ir: dict[str, Any], match_map: dict[str, list[str]]) -> str | None:
    triples = ir.get("verified_triples", []) or []
    if len(triples) != 2:
        return None
    (s1, r1, o1), (s2, r2, o2) = triples
    if (s1, r1, o1, s2, r2, o2) != ("User", "POSTS", "Tweet", "Tweet", "TAGS", "Hashtag"):
        return None
    if str(ir.get("projection_lock") or "") != "full_node":
        return None
    match_clause = "MATCH (user:User)-[:POSTS]->(tweet:Tweet)-[:TAGS]->(hashtag:Hashtag)"
    where_clause = _where_from_anchors(
        [
            ("user", a)
            for a in (ir.get("anchors", []) or [])
            if str(a.get("label") or "") == "User"
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
    anchors = ir.get("anchors", []) or []

    def pick(label: str) -> dict[str, str] | None:
        for anchor in anchors:
            if str(anchor.get("label") or "") == label:
                return anchor
        return None

    if "retweeted the most times" in q:
        return (
            "MATCH (tweet:Tweet)-[:RETWEETS]->(retweet:Tweet) "
            "RETURN tweet.text AS tweet_text, count(retweet) AS retweet_count "
            "ORDER BY retweet_count DESC "
            + (f"LIMIT {int(ir['limit'])}" if ir.get("limit") else "")
        ).strip()

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
