from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class ReturnItem:
    expression: str
    alias: str = ""
    kind: str = "expression"
    owner: str = ""
    property: str = ""

    def to_dict(self) -> dict:
        return {
            "expression": self.expression,
            "alias": self.alias,
            "kind": self.kind,
            "owner": self.owner,
            "property": self.property,
        }


@dataclass
class CypherShape:
    labels: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    path_motifs: list[str] = field(default_factory=list)
    clauses: list[str] = field(default_factory=list)
    return_items: list[ReturnItem] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    signature: str = ""

    def to_dict(self) -> dict:
        return {
            "labels": self.labels,
            "relationships": self.relationships,
            "path_motifs": self.path_motifs,
            "clauses": self.clauses,
            "return_items": [item.to_dict() for item in self.return_items],
            "filters": self.filters,
            "signature": self.signature,
        }


def split_top_level_commas(text: str) -> list[str]:
    items: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    for idx, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in {"'", '"'}:
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            items.append(text[start:idx].strip())
            start = idx + 1
    tail = text[start:].strip()
    if tail:
        items.append(tail)
    return items


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _clause_body(cypher: str, clause: str) -> str:
    pattern = rf"(?is)\b{clause}\b\s+(.*?)(?=\bMATCH\b|\bOPTIONAL\s+MATCH\b|\bWHERE\b|\bWITH\b|\bUNWIND\b|\bRETURN\b|\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)"
    match = re.search(pattern, cypher)
    return match.group(1).strip() if match else ""


def _return_body(cypher: str) -> str:
    match = re.search(r"(?is)\bRETURN\b\s+(.*?)(?=\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)", cypher)
    return match.group(1).strip() if match else ""


def _extract_variable_maps(cypher: str) -> tuple[dict[str, str], dict[str, str]]:
    node_vars: dict[str, str] = {}
    rel_vars: dict[str, str] = {}
    for match in re.finditer(r"\(\s*([A-Za-z_][A-Za-z0-9_]*)?\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?", cypher):
        var, label = match.group(1), match.group(2)
        if var:
            node_vars[var] = label
    for match in re.finditer(r"\[\s*([A-Za-z_][A-Za-z0-9_]*)?\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?", cypher):
        var, rel = match.group(1), match.group(2)
        if var:
            rel_vars[var] = rel
    return node_vars, rel_vars


def _parse_return_item(item: str, node_vars: dict[str, str], rel_vars: dict[str, str]) -> ReturnItem:
    parts = re.split(r"(?i)\s+AS\s+", item, maxsplit=1)
    expression = parts[0].strip()
    alias = parts[1].strip() if len(parts) > 1 else ""
    if re.search(r"(?i)\b(count|avg|sum|min|max|collect|size)\s*\(", expression):
        kind = "aggregate"
    elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expression):
        kind = "node" if expression in node_vars else "relationship" if expression in rel_vars else "variable"
    elif match := re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)", expression):
        owner, prop = match.group(1), match.group(2)
        kind = "node_property" if owner in node_vars else "relationship_property" if owner in rel_vars else "property"
        return ReturnItem(expression=expression, alias=alias, kind=kind, owner=owner, property=prop)
    else:
        kind = "expression"
    return ReturnItem(expression=expression, alias=alias, kind=kind)


def _extract_path_motifs(cypher: str, node_vars: dict[str, str]) -> list[str]:
    motifs: list[str] = []
    pattern = r"\(([^)]*)\)\s*(<-)?-\s*\[[^\]]*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?[^\]]*\]\s*-(>)?\s*\(([^)]*)\)"
    for match in re.finditer(pattern, cypher):
        left, incoming, rel, outgoing, right = match.groups()
        left_label = _node_label(left, node_vars)
        right_label = _node_label(right, node_vars)
        if incoming:
            motifs.append(f"({right_label})-[:{rel}]->({left_label})")
        else:
            arrow = "->" if outgoing else "-"
            motifs.append(f"({left_label})-[:{rel}]{arrow}({right_label})")
    return _unique(motifs)


def _node_label(node_text: str, node_vars: dict[str, str]) -> str:
    if label_match := re.search(r":\s*`?([A-Za-z_][A-Za-z0-9_]*)`?", node_text):
        return label_match.group(1)
    var = node_text.strip().split()[0] if node_text.strip() else ""
    var = var.split(":")[0].strip("`")
    return node_vars.get(var, "?")


def _extract_filters(cypher: str) -> list[str]:
    filters: list[str] = []
    for where_body in re.findall(r"(?is)\bWHERE\b\s+(.*?)(?=\bMATCH\b|\bWITH\b|\bRETURN\b|\bORDER\s+BY\b|\bLIMIT\b|$)", cypher):
        comparisons = re.findall(
            r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\s*(?:=|<>|<=|>=|<|>|CONTAINS|STARTS WITH|ENDS WITH|IS NOT NULL|IS NULL)",
            where_body,
            flags=re.IGNORECASE,
        )
        filters.extend(comp.strip() for comp in comparisons)
    return _unique(filters)


def parse_cypher_shape(cypher: str) -> CypherShape:
    node_vars, rel_vars = _extract_variable_maps(cypher)
    labels = _unique(list(node_vars.values()) + re.findall(r"\([^)]+:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?", cypher))
    relationships = _unique(list(rel_vars.values()) + re.findall(r"\[[^\]]*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?", cypher))
    clauses = [
        name
        for name, pattern in {
            "MATCH": r"\bMATCH\b",
            "OPTIONAL_MATCH": r"\bOPTIONAL\s+MATCH\b",
            "WHERE": r"\bWHERE\b",
            "WITH": r"\bWITH\b",
            "UNWIND": r"\bUNWIND\b",
            "RETURN": r"\bRETURN\b",
            "ORDER_BY": r"\bORDER\s+BY\b",
            "LIMIT": r"\bLIMIT\b",
        }.items()
        if re.search(pattern, cypher, flags=re.IGNORECASE)
    ]
    return_items = [
        _parse_return_item(item, node_vars, rel_vars)
        for item in split_top_level_commas(_return_body(cypher))
    ]
    shape = CypherShape(
        labels=labels,
        relationships=relationships,
        path_motifs=_extract_path_motifs(cypher, node_vars),
        clauses=clauses,
        return_items=return_items,
        filters=_extract_filters(cypher),
    )
    shape.signature = build_signature(shape)
    return shape


def build_signature(shape: CypherShape) -> str:
    return "|".join(
        [
            "clauses=" + ",".join(shape.clauses),
            "motifs=" + ";".join(shape.path_motifs),
            "returns=" + ",".join(item.kind for item in shape.return_items),
        ]
    )


def summarize_shapes(shapes: list[CypherShape]) -> dict:
    label_counts: Counter[str] = Counter()
    rel_counts: Counter[str] = Counter()
    motif_counts: Counter[str] = Counter()
    signature_counts: Counter[str] = Counter()
    return_kind_counts: Counter[str] = Counter()
    filter_counts: Counter[str] = Counter()
    for shape in shapes:
        label_counts.update(shape.labels)
        rel_counts.update(shape.relationships)
        motif_counts.update(shape.path_motifs)
        signature_counts.update([shape.signature])
        return_kind_counts.update(item.kind for item in shape.return_items)
        filter_counts.update(shape.filters)
    return {
        "labels": label_counts.most_common(),
        "relationships": rel_counts.most_common(),
        "path_motifs": motif_counts.most_common(50),
        "shape_signatures": signature_counts.most_common(50),
        "return_kinds": return_kind_counts.most_common(),
        "filters": filter_counts.most_common(50),
    }
