"""Align generated projections with the selected profile contract."""

from __future__ import annotations

import re
from typing import Any

from services.text2cypher.query_plan import (
    _expected_operation_from_context,
    _primary_target_label_from_context,
    _query_plan_from_context,
)
from services.universal.schema_parser import parse_schema_text


def _quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _label_forms(label: str) -> set[str]:
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[0-9]+", label)
    forms = {label.lower()}
    for word in words:
        lowered = word.lower()
        forms.add(lowered)
        if lowered.endswith("y"):
            forms.add(lowered[:-1] + "ies")
        else:
            forms.add(lowered + "s")
    return forms


def _preferred_identity_property(properties: dict[str, Any]) -> str:
    priority = (
        "title",
        "name",
        "screen_name",
        "accountId",
        "userId",
        "id",
    )
    lowered = {prop.lower(): prop for prop in properties}
    for prop in priority:
        if prop.lower() in lowered:
            return lowered[prop.lower()]
    return ""


def _ensure_rank_entity_projection(cypher: str, question: str, runtime_schema: str) -> str:
    """When ranking entities by a metric, keep an identity column with the metric."""
    if not cypher or not re.search(r"(?i)\bORDER\s+BY\b", cypher):
        return cypher
    if not re.search(r"(?i)\b(top|list|show|highest|most|lowest|least)\b", question or ""):
        return cypher
    return_match = re.search(r"(?is)\bRETURN\b\s+(.*?)(?:\bORDER\s+BY\b|\bLIMIT\b|$)", cypher)
    if not return_match:
        return cypher
    return_body = return_match.group(1).strip()
    if "," in return_body or re.search(
        r"(?i)\bcount\s*\(|\bcollect\s*\(|\bavg\s*\(|\bsum\s*\(", return_body
    ):
        return cypher
    returned_prop = re.fullmatch(
        r"([A-Za-z_][A-Za-z0-9_]*)\.(`?)([A-Za-z_][A-Za-z0-9_]*)\2(?:\s+AS\s+[A-Za-z_][A-Za-z0-9_]*)?",
        return_body,
        flags=re.IGNORECASE,
    )
    if not returned_prop:
        return cypher
    alias = returned_prop.group(1)
    returned_property = returned_prop.group(3)
    label_match = re.search(
        rf"\({re.escape(alias)}\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?\)",
        cypher,
    )
    if not label_match:
        return cypher
    label = label_match.group(1)
    question_tokens = set(re.findall(r"[a-z0-9]+", (question or "").lower()))
    if not (question_tokens & _label_forms(label)):
        return cypher
    try:
        schema_graph = parse_schema_text(runtime_schema)
        properties = schema_graph.get_node_properties(label)
    except Exception:
        properties = {}
    identity_property = _preferred_identity_property(properties)
    if not identity_property or identity_property == returned_property:
        return cypher
    replacement = (
        f"{alias}.{_quote_ident(identity_property)} AS {identity_property}, "
        f"{alias}.{_quote_ident(returned_property)} AS {returned_property}"
    )
    suffix = cypher[return_match.end(1) :]
    if suffix and not suffix[0].isspace():
        suffix = " " + suffix
    return cypher[: return_match.start(1)] + replacement + suffix


def _alias_labels(cypher: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for alias, label in re.findall(
        r"\(([A-Za-z_][A-Za-z0-9_]*)\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?",
        cypher or "",
    ):
        labels[alias] = label
    return labels


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


def _return_body_span(cypher: str) -> re.Match[str] | None:
    return re.search(
        r"(?is)\bRETURN\b\s+(.*?)(?:\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)", cypher or ""
    )


def _item_references_alias(item: str, alias: str) -> bool:
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?:\s*$|\.|\s+AS\b)",
            item or "",
            re.IGNORECASE,
        )
    )


def _is_aggregate_return_item(item: str) -> bool:
    return bool(
        re.search(
            r"(?i)\b(count|avg|sum|min|max|collect|percentileCont|percentileDisc)\s*\(",
            item or "",
        )
    )


def _projection_for_label_alias(alias: str, label: str, runtime_schema: str) -> str:
    try:
        schema_graph = parse_schema_text(runtime_schema)
        properties = schema_graph.get_node_properties(label)
    except Exception:
        properties = {}
    identity_property = _preferred_identity_property(properties)
    if identity_property:
        return f"{alias}.{_quote_ident(identity_property)} AS {alias}_{identity_property}"
    return alias


def _ensure_primary_target_return(cypher: str, learned_context: str, runtime_schema: str) -> str:
    """Keep RETURN aligned with the recipe's primary target label.
    This is intentionally conservative: it only rewrites when the query already
    matched a primary target alias but the RETURN clause does not reference it.
    """
    target_label = _primary_target_label_from_context(learned_context)
    expected_operation = _expected_operation_from_context(learned_context)
    if expected_operation == "aggregate":
        return cypher
    if not target_label or not cypher or not re.search(r"(?i)\bRETURN\b", cypher):
        return cypher
    aliases = _alias_labels(cypher)
    target_aliases = [
        alias for alias, label in aliases.items() if label.lower() == target_label.lower()
    ]
    if not target_aliases:
        return cypher
    return_match = _return_body_span(cypher)
    if not return_match:
        return cypher
    return_body = return_match.group(1).strip()
    items = _split_return_items(return_body)
    if not items:
        return cypher
    if any(_item_references_alias(item, alias) for alias in target_aliases for item in items):
        return cypher
    target_alias = target_aliases[0]
    target_projection = _projection_for_label_alias(target_alias, target_label, runtime_schema)
    aggregate_items = [item for item in items if _is_aggregate_return_item(item)]
    replacement_items = [target_projection]
    for item in aggregate_items:
        if item not in replacement_items:
            replacement_items.append(item)
    replacement = ", ".join(replacement_items)
    suffix = cypher[return_match.end(1) :]
    if suffix and not suffix[0].isspace():
        suffix = " " + suffix
    return cypher[: return_match.start(1)] + replacement + suffix


def _return_clause_suffix(cypher: str, return_match: re.Match[str]) -> str:
    suffix = cypher[return_match.end(1) :]
    if suffix and not suffix[0].isspace():
        suffix = " " + suffix
    return suffix


def _alias_for_label(aliases: dict[str, str], label: str) -> str:
    for alias, alias_label in aliases.items():
        if alias_label.lower() == label.lower():
            return alias
    return ""


def _adapt_return_item_aliases(
    item: str, scaffold_aliases: dict[str, str], generated_aliases: dict[str, str]
) -> str:
    adapted = item
    for scaffold_alias, label in scaffold_aliases.items():
        generated_alias = ""
        if generated_aliases.get(scaffold_alias, "").lower() == label.lower():
            generated_alias = scaffold_alias
        if not generated_alias:
            generated_alias = _alias_for_label(generated_aliases, label)
        if generated_alias and generated_alias != scaffold_alias:
            adapted = re.sub(
                rf"(?<![A-Za-z0-9_]){re.escape(scaffold_alias)}(?=\.|\s*$|\s+AS\b)",
                generated_alias,
                adapted,
            )
    return adapted


def _with_alias_exprs(cypher: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for with_body in re.findall(
        r"(?is)\bWITH\b\s+(.*?)(?=\bMATCH\b|\bRETURN\b|\bWHERE\b|\bORDER\s+BY\b|\bLIMIT\b|$)",
        cypher or "",
    ):
        for item in _split_return_items(with_body):
            match = re.search(r"(?is)(.*?)\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", item.strip())
            if match:
                expr = re.sub(r"\s+", " ", match.group(1).strip()).lower()
                aliases[match.group(2)] = expr
    return aliases


def _adapt_bare_return_variable(item: str, scaffold: str, generated: str) -> str:
    bare = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item.strip())
    if not bare:
        return item
    scaffold_var = bare.group(0)
    if re.search(rf"(?<![A-Za-z0-9_]){re.escape(scaffold_var)}(?![A-Za-z0-9_])", generated):
        return item
    scaffold_aliases = _with_alias_exprs(scaffold)
    generated_aliases = _with_alias_exprs(generated)
    scaffold_expr = scaffold_aliases.get(scaffold_var)
    if not scaffold_expr:
        return item
    for generated_var, generated_expr in generated_aliases.items():
        if generated_expr == scaffold_expr:
            return f"{generated_var} AS {scaffold_var}"
    return item


def _return_shape_from_plan(cypher: str, learned_context: str) -> str:
    plan = _query_plan_from_context(learned_context)
    contract = plan.get("return_contract") if isinstance(plan, dict) else {}
    if not isinstance(contract, dict):
        return cypher
    scaffold = plan.get("scaffold_cypher", "")
    scaffold_items = contract.get("items") or []
    if not scaffold or not scaffold_items:
        return cypher
    return_match = _return_body_span(cypher)
    if not return_match:
        return cypher
    current_body = return_match.group(1).strip()
    current_items = _split_return_items(current_body)
    if not current_items:
        return cypher
    scaffold_aliases = _alias_labels(scaffold)
    generated_aliases = _alias_labels(cypher)
    if not generated_aliases:
        return cypher
    node_return_labels = contract.get("node_return_labels") or []
    if node_return_labels:
        replacement_items = []
        for label in node_return_labels:
            alias = _alias_for_label(generated_aliases, label)
            if alias:
                replacement_items.append(alias)
        if not replacement_items:
            return cypher
        replacement = ", ".join(dict.fromkeys(replacement_items))
    else:
        adapted_items = [
            _adapt_return_item_aliases(str(item), scaffold_aliases, generated_aliases)
            for item in scaffold_items
        ]
        adapted_items = [
            _adapt_bare_return_variable(item, scaffold, cypher) for item in adapted_items
        ]
        # Avoid injecting return expressions that depend on aliases not present in
        # the generated query. This keeps the checker shape-based, not row-based.
        generated_with_aliases = _with_alias_exprs(cypher)
        for item in adapted_items:
            for alias in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\.", item):
                if alias not in generated_aliases:
                    return cypher
            expr = re.split(r"(?i)\s+AS\s+", item, maxsplit=1)[0].strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr):
                if expr not in generated_aliases and expr not in generated_with_aliases:
                    return cypher
        replacement = ", ".join(adapted_items)
    if contract.get("distinct") and not re.search(r"(?i)^\s*DISTINCT\b", replacement):
        replacement = "DISTINCT " + replacement
    if current_body == replacement:
        return cypher
    suffix = _return_clause_suffix(cypher, return_match)
    return cypher[: return_match.start(1)] + replacement + suffix
