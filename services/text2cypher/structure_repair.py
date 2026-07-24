"""Repair query-level execution structure without changing intent."""

from __future__ import annotations

import os
import re

from services.text2cypher.query_plan import _query_plan_from_context


def _ensure_ordered_property_not_null(cypher: str) -> str:
    if os.getenv("T2C_ADD_ORDER_NOT_NULL_GUARDS", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return cypher
    if not cypher or not re.search(r"(?i)\bORDER\s+BY\b", cypher):
        return cypher
    return_match = re.search(r"(?i)\bRETURN\b", cypher)
    if not return_match:
        return cypher
    first_order = re.search(r"(?i)\bORDER\s+BY\b", cypher)
    if first_order:
        # Nested top-k queries often rank an inner entity, pass it through WITH,
        # then MATCH the outer target. Injecting an AND into an earlier WHERE can
        # corrupt the later MATCH chain, so leave those planned queries intact.
        with_between_order_and_return = re.search(
            r"(?is)\bORDER\s+BY\b.*?\bWITH\b",
            cypher[first_order.start() : return_match.start()],
        )
        limit_between_order_and_return = re.search(
            r"(?is)\bORDER\s+BY\b.*?\bLIMIT\b",
            cypher[first_order.start() : return_match.start()],
        )
        if with_between_order_and_return or limit_between_order_and_return:
            return cypher
    ordered_props = [
        item
        for item in re.findall(
            r"(?is)\bORDER\s+BY\b\s+(.*?)(?:\bSKIP\b|\bLIMIT\b|$)",
            cypher,
        )
        for item in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b", item)
    ]
    if not ordered_props:
        return cypher
    guards = [
        f"{prop} IS NOT NULL"
        for prop in dict.fromkeys(ordered_props)
        if not re.search(rf"(?i)\b{re.escape(prop)}\s+IS\s+NOT\s+NULL\b", cypher)
    ]
    if not guards:
        return cypher
    before_return = cypher[: return_match.start()].rstrip()
    after_return = cypher[return_match.start() :]
    where_matches = list(re.finditer(r"(?i)\bWHERE\b", before_return))
    guard_text = " AND ".join(guards)
    if where_matches:
        last_where = where_matches[-1]
        before_return = (
            before_return[: last_where.end()]
            + " "
            + before_return[last_where.end() :].strip()
            + f" AND {guard_text}"
        )
    else:
        before_return = f"{before_return} WHERE {guard_text}"
    return f"{before_return} {after_return}"


def _quoted_literals(text: str) -> set[str]:
    return {
        value.lower()
        for match in re.findall(r"'([^']+)'|\"([^\"]+)\"", text or "")
        for value in match
        if value
    }


def _preserve_same_entity_scaffold(cypher: str, learned_context: str, question: str) -> str:
    lowered = (question or "").lower()
    if "same" not in lowered or " as " not in f" {lowered} ":
        return cypher
    plan = _query_plan_from_context(learned_context)
    scaffold = plan.get("scaffold_cypher", "") if isinstance(plan, dict) else ""
    if not scaffold:
        return cypher
    if len(re.findall(r"(?i)\bMATCH\b", scaffold)) < 2:
        return cypher
    scaffold_literals = _quoted_literals(scaffold)
    question_literals = _quoted_literals(question)
    if scaffold_literals and not scaffold_literals.issubset(question_literals):
        return cypher
    if len(re.findall(r"(?i)\bMATCH\b", cypher or "")) >= len(
        re.findall(r"(?i)\bMATCH\b", scaffold)
    ):
        return cypher
    return scaffold
