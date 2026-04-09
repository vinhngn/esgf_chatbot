"""
Cypher Translator — deterministic conversion from QueryPlan to Cypher.

NO LLM involved. Pure string formatting.
This eliminates all Cypher syntax errors by construction:
  - WHERE always comes after MATCH
  - count{} subquery syntax used correctly
  - Variable reuse handled by plan's var field
  - Relationship directions from plan's direction field
"""

from __future__ import annotations

import logging
from typing import Any

from services.query_plan import QueryPlan, PatternSpec, WhereCondition, ReturnField

logger = logging.getLogger(__name__)


def translate(plan: QueryPlan) -> str:
    """Convert a QueryPlan to a Cypher query string."""
    parts: list[str] = []

    # --- MATCH clauses ---
    match_clauses = _build_match_clauses(plan.patterns)
    if match_clauses:
        parts.append(match_clauses)

    # --- WHERE clause ---
    where_clause = _build_where(plan.where)
    if where_clause:
        parts.append(where_clause)

    # --- RETURN clause ---
    return_clause = _build_return(plan.returns, plan.distinct)
    if return_clause:
        parts.append(return_clause)

    # --- ORDER BY ---
    if plan.order_by:
        parts.append(f"ORDER BY {plan.order_by.field} {plan.order_by.dir}")

    # --- LIMIT ---
    if plan.limit:
        parts.append(f"LIMIT {plan.limit}")

    cypher = "\n".join(parts)
    logger.info("[Translator] Generated Cypher:\n%s", cypher)
    return cypher


def _build_match_clauses(patterns: list[PatternSpec]) -> str:
    """Build MATCH clauses from patterns."""
    if not patterns:
        return ""

    clauses: list[str] = []
    for pat in patterns:
        if pat.rel:
            # Relationship pattern
            match_str = _pattern_to_cypher(pat)
        else:
            # Single node pattern (no relationship)
            match_str = _node_to_cypher(pat.from_node)
        clauses.append(f"MATCH {match_str}")

    return "\n".join(clauses)


def _pattern_to_cypher(pat: PatternSpec) -> str:
    """Convert a single PatternSpec to a Cypher pattern string."""
    from_str = _node_to_cypher(pat.from_node)
    to_str = _node_to_cypher(pat.to_node)

    rel_str = f":{pat.rel}" if pat.rel else ""
    rel_var = pat.rel_var or ""
    rel_part = f"[{rel_var}{rel_str}]"

    direction = pat.direction.strip().lower()
    if direction in ("<-", "in", "incoming"):
        return f"{from_str}<-{rel_part}-{to_str}"
    else:
        return f"{from_str}-{rel_part}->{to_str}"


def _node_to_cypher(node) -> str:
    """Convert a NodeSpec to a Cypher node string like (p:Person {name: 'Tom'})."""
    parts = []
    if node.var:
        parts.append(node.var)
    if node.label:
        parts.append(f":{node.label}")

    props_str = ""
    if node.match:
        props = []
        for k, v in node.match.items():
            # Only scalar values go in MATCH inline props
            # Dicts (like {"$gt": 90}) are invalid — skip them
            if isinstance(v, dict):
                logger.warning("[Translator] Skipping non-scalar match value for %s: %s", k, v)
                continue
            props.append(f"{k}: {_format_value(v)}")
        if props:
            props_str = " {" + ", ".join(props) + "}"

    inner = "".join(parts) + props_str
    return f"({inner})"


def _build_where(conditions: list[WhereCondition]) -> str:
    """Build WHERE clause from conditions."""
    if not conditions:
        return ""

    parts = []
    for cond in conditions:
        ref = f"{cond.var}.{cond.prop}" if cond.var else cond.prop

        if cond.op.upper() == "IS NOT NULL":
            parts.append(f"{ref} IS NOT NULL")
        elif cond.op.upper() == "IS NULL":
            parts.append(f"{ref} IS NULL")
        elif cond.op.upper() == "IN":
            parts.append(f"{ref} IN {_format_value(cond.value)}")
        elif cond.op.upper() in ("CONTAINS", "STARTS WITH", "ENDS WITH"):
            parts.append(f"{ref} {cond.op.upper()} {_format_value(cond.value)}")
        else:
            parts.append(f"{ref} {cond.op} {_format_value(cond.value)}")

    return "WHERE " + " AND ".join(parts)


def _build_return(fields: list[ReturnField], distinct: bool) -> str:
    """Build RETURN clause."""
    if not fields:
        return ""

    parts = []
    for f in fields:
        expr = _return_field_to_expr(f)
        if expr:
            parts.append(expr)

    if not parts:
        return ""

    keyword = "RETURN DISTINCT" if distinct else "RETURN"
    return f"{keyword} {', '.join(parts)}"


def _return_field_to_expr(f: ReturnField) -> str:
    """Convert a ReturnField to a Cypher expression."""
    if f.star:
        return f.var if f.var else "*"

    base = ""
    if f.var and f.prop:
        base = f"{f.var}.{f.prop}"
    elif f.var:
        base = f.var
    elif f.prop:
        base = f.prop

    if f.agg:
        agg_upper = f.agg.upper()
        if agg_upper == "COUNT" and not base:
            expr = "count(*)"
        elif agg_upper == "COUNT_PATTERN":
            # count{} subquery syntax for counting relationships
            expr = f"count{{{base}}}"
        elif agg_upper == "SIZE":
            expr = f"size({base})"
        else:
            expr = f"{agg_upper.lower()}({base})"
    else:
        expr = base

    if f.alias and f.alias != expr:
        return f"{expr} AS {f.alias}"
    return expr


def _format_value(value: Any) -> str:
    """Format a value for Cypher."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"
    if isinstance(value, list):
        items = ", ".join(_format_value(v) for v in value)
        return f"[{items}]"
    return f"'{value}'"
