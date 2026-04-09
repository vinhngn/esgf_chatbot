"""
Query Plan — structured intermediate representation for Text-to-Cypher.

Separates logical planning (what to query) from syntactic generation (how to write Cypher).
The plan is a JSON-serializable dict that can be:
  1. Validated against the graph schema (before Cypher generation)
  2. Translated to Cypher deterministically (no LLM needed)
  3. Debugged and corrected field-by-field (targeted error fixing)

Plan format:
{
  "patterns": [
    {
      "from": {"label": "Person", "var": "p", "match": {"name": "Tom Hanks"}},
      "rel": "ACTED_IN",
      "rel_var": null,
      "direction": "->",
      "to": {"label": "Movie", "var": "m", "match": {}}
    }
  ],
  "where": [
    {"var": "m", "prop": "votes", "op": ">", "value": 100}
  ],
  "return": [
    {"var": "m", "prop": "title"},
    {"agg": "count", "var": "m", "alias": "cnt"}
  ],
  "order_by": {"field": "cnt", "dir": "DESC"},
  "limit": 5,
  "distinct": false
}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class NodeSpec:
    """A node in a pattern."""
    label: str
    var: str = ""
    match: dict[str, Any] = field(default_factory=dict)


@dataclass
class PatternSpec:
    """A relationship pattern: (from)-[rel]->(to)."""
    from_node: NodeSpec
    rel: str
    to_node: NodeSpec
    direction: str = "->"       # "->" or "<-"
    rel_var: str | None = None  # variable for relationship (e.g., "r" for r.roles)


@dataclass
class WhereCondition:
    """A WHERE filter condition."""
    var: str
    prop: str
    op: str       # >, <, =, >=, <=, <>, CONTAINS, STARTS WITH, ENDS WITH, IN, IS NOT NULL
    value: Any


@dataclass
class ReturnField:
    """A field in the RETURN clause."""
    var: str = ""
    prop: str = ""
    agg: str = ""       # count, avg, sum, collect, size, min, max
    alias: str = ""
    star: bool = False   # RETURN * or RETURN var (full node)


@dataclass
class OrderBySpec:
    """ORDER BY specification."""
    field: str       # alias or var.prop
    dir: str = "DESC"


@dataclass
class QueryPlan:
    """Complete query plan — everything needed to generate Cypher."""
    patterns: list[PatternSpec] = field(default_factory=list)
    where: list[WhereCondition] = field(default_factory=list)
    returns: list[ReturnField] = field(default_factory=list)
    order_by: OrderBySpec | None = None
    limit: int | None = None
    distinct: bool = False

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        result: dict[str, Any] = {}
        if self.patterns:
            result["patterns"] = []
            for p in self.patterns:
                pat: dict[str, Any] = {
                    "from": {"label": p.from_node.label, "var": p.from_node.var},
                    "rel": p.rel,
                    "direction": p.direction,
                    "to": {"label": p.to_node.label, "var": p.to_node.var},
                }
                if p.from_node.match:
                    pat["from"]["match"] = p.from_node.match
                if p.to_node.match:
                    pat["to"]["match"] = p.to_node.match
                if p.rel_var:
                    pat["rel_var"] = p.rel_var
                result["patterns"].append(pat)
        if self.where:
            result["where"] = [
                {"var": w.var, "prop": w.prop, "op": w.op, "value": w.value}
                for w in self.where
            ]
        if self.returns:
            result["return"] = []
            for r in self.returns:
                rf: dict[str, Any] = {}
                if r.star:
                    rf["star"] = True
                if r.var:
                    rf["var"] = r.var
                if r.prop:
                    rf["prop"] = r.prop
                if r.agg:
                    rf["agg"] = r.agg
                if r.alias:
                    rf["alias"] = r.alias
                result["return"].append(rf)
        if self.order_by:
            result["order_by"] = {"field": self.order_by.field, "dir": self.order_by.dir}
        if self.limit:
            result["limit"] = self.limit
        if self.distinct:
            result["distinct"] = True
        return result


def parse_plan_dict(data: dict) -> QueryPlan:
    """Parse a JSON dict into a QueryPlan object."""
    plan = QueryPlan()

    for pat_data in data.get("patterns", []):
        from_data = pat_data.get("from", {})
        to_data = pat_data.get("to", {})
        pattern = PatternSpec(
            from_node=NodeSpec(
                label=from_data.get("label", ""),
                var=from_data.get("var", ""),
                match=from_data.get("match", {}),
            ),
            rel=pat_data.get("rel", ""),
            to_node=NodeSpec(
                label=to_data.get("label", ""),
                var=to_data.get("var", ""),
                match=to_data.get("match", {}),
            ),
            direction=pat_data.get("direction", "->"),
            rel_var=pat_data.get("rel_var"),
        )
        plan.patterns.append(pattern)

    for w_data in data.get("where", []):
        plan.where.append(WhereCondition(
            var=w_data.get("var", ""),
            prop=w_data.get("prop", ""),
            op=w_data.get("op", "="),
            value=w_data.get("value"),
        ))

    for r_data in data.get("return", []):
        plan.returns.append(ReturnField(
            var=r_data.get("var", ""),
            prop=r_data.get("prop", ""),
            agg=r_data.get("agg", ""),
            alias=r_data.get("alias", ""),
            star=r_data.get("star", False),
        ))

    ob_data = data.get("order_by")
    if ob_data:
        plan.order_by = OrderBySpec(
            field=ob_data.get("field", ""),
            dir=ob_data.get("dir", "DESC"),
        )

    plan.limit = data.get("limit")
    plan.distinct = data.get("distinct", False)

    return plan
