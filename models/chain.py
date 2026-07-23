"""Retrieval-grounded Text-to-Cypher execution with universal schema grounding."""
from __future__ import annotations
import json
import logging
import os
import re
from typing import Any
from config import get_settings
from models.graph import get_graph, maybe_refresh_schema
from templates.cypher_templates import get_prompt_sections
from services.profile_analyzer.context import (
    format_profile_context,
    load_profile,
    select_profile_context,
)
from services.profile_analyzer.store import profile_build_command, profile_path
from services.universal.entity_resolver import (
    format_entity_resolution_context,
    resolve_question_entities,
)
from services.universal.property_context import build_runtime_property_context
from services.universal.schema_parser import parse_schema_text
from utils.helpers import (
    clean_cypher_query,
    repair_northwind_order_line_properties,
    repair_northwind_projection_and_metrics,
    strip_noisy_return_properties,
    rewrite_bare_node_returns,
)
from utils.pipeline_trace import new_trace_id, trace_event, verbose_trace_enabled

logger = logging.getLogger(__name__)
MAX_CYPHER_RETRIES = 2
DEFAULT_EXECUTION_ROW_CAP = 100


def _quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _execution_row_cap() -> int:
    try:
        return int(os.getenv("T2C_API_EXECUTION_ROW_CAP", str(DEFAULT_EXECUTION_ROW_CAP)))
    except ValueError:
        return DEFAULT_EXECUTION_ROW_CAP


def _limit_value(cypher: str) -> int | None:
    match = re.search(r"(?is)\bLIMIT\s+(\d+)\s*$", cypher or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _cap_execution_cypher(cypher: str) -> tuple[str, bool]:
    """Cap API-side execution rows without changing the generated Cypher."""
    cap = _execution_row_cap()
    if cap <= 0 or not cypher:
        return cypher, False
    existing_limit = _limit_value(cypher)
    if existing_limit is not None and existing_limit <= cap:
        return cypher, False
    if existing_limit is not None:
        capped = re.sub(r"(?is)\bLIMIT\s+\d+\s*$", f"LIMIT {cap}", cypher.strip())
        return capped, capped != cypher
    return f"{cypher.rstrip()} LIMIT {cap}", True


def _requested_limit(question: str) -> int | None:
    patterns = [
        r"\b(?:top|first|last|list|show|return)\s+(\d+)\b",
        r"\b(\d+)\s+(?:nodes?|rows?|items?|records?|tweets?|movies?|users?|products?|orders?|customers?|suppliers?|hashtags?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, question or "", flags=re.IGNORECASE)
        if match:
            try:
                value = int(match.group(1))
            except ValueError:
                continue
            if 0 < value <= 1000:
                return value
    return None


def _ensure_requested_limit(cypher: str, question: str) -> str:
    if not cypher or re.search(r"(?i)\bLIMIT\s+\d+\b", cypher):
        return cypher
    limit = _requested_limit(question)
    if limit is None:
        return cypher
    return f"{cypher.rstrip()} LIMIT {limit}"


def _ensure_rank_order(cypher: str, question: str) -> str:
    if not cypher or re.search(r"(?i)\bORDER\s+BY\b", cypher):
        return cypher
    lowered = (question or "").lower()
    if not re.search(r"\b(top|highest|largest|most|lowest|least|smallest)\b", lowered):
        return cypher

    direction = "ASC" if re.search(r"\b(lowest|least|smallest)\b", lowered) else "DESC"
    return_body = re.search(r"(?is)\bRETURN\b\s+(.*?)(?:\bLIMIT\b|$)", cypher)
    if not return_body:
        return cypher
    candidates = re.findall(
        r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b",
        return_body.group(1),
    )
    if not candidates:
        return cypher

    question_tokens = set(re.findall(r"[a-z0-9]+", lowered))
    selected = ""
    for candidate in candidates:
        prop = candidate.rsplit(".", 1)[1].lower()
        if prop in question_tokens:
            selected = candidate
            break
    if not selected and len(candidates) > 1:
        selected = candidates[-1]
    if not selected:
        return cypher

    limit_match = re.search(r"(?is)\s+LIMIT\s+\d+\s*$", cypher)
    if limit_match:
        prefix = cypher[: limit_match.start()].rstrip()
        suffix = cypher[limit_match.start():]
        return f"{prefix} ORDER BY {selected} {direction}{suffix}"
    return f"{cypher.rstrip()} ORDER BY {selected} {direction}"


def _ensure_ordered_property_not_null(cypher: str) -> str:
    if os.getenv("T2C_ADD_ORDER_NOT_NULL_GUARDS", "").strip().lower() not in {"1", "true", "yes", "on"}:
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
            cypher[first_order.start():return_match.start()],
        )
        limit_between_order_and_return = re.search(
            r"(?is)\bORDER\s+BY\b.*?\bLIMIT\b",
            cypher[first_order.start():return_match.start()],
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
    after_return = cypher[return_match.start():]
    where_matches = list(re.finditer(r"(?i)\bWHERE\b", before_return))
    guard_text = " AND ".join(guards)
    if where_matches:
        last_where = where_matches[-1]
        before_return = (
            before_return[: last_where.end()]
            + " "
            + before_return[last_where.end():].strip()
            + f" AND {guard_text}"
        )
    else:
        before_return = f"{before_return} WHERE {guard_text}"
    return f"{before_return} {after_return}"


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
    if "," in return_body or re.search(r"(?i)\bcount\s*\(|\bcollect\s*\(|\bavg\s*\(|\bsum\s*\(", return_body):
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
    suffix = cypher[return_match.end(1):]
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


def _strip_unknown_return_properties(cypher: str, runtime_schema: str) -> str:
    if not cypher or not re.search(r"(?i)\bRETURN\b", cypher):
        return cypher
    try:
        schema_graph = parse_schema_text(runtime_schema)
    except Exception:
        return cypher

    aliases = _alias_labels(cypher)
    if not aliases:
        return cypher

    return_match = re.search(r"(?is)\bRETURN\b\s+(.*?)(?:\bORDER\s+BY\b|\bLIMIT\b|$)", cypher)
    if not return_match:
        return cypher
    return_body = return_match.group(1).strip()
    if not return_body or re.search(r"(?i)\bcount\s*\(|\bavg\s*\(|\bsum\s*\(|\bcollect\s*\(", return_body):
        return cypher

    kept_items: list[str] = []
    changed = False
    for item in [part.strip() for part in return_body.split(",") if part.strip()]:
        prop_match = re.search(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\.(`?)([A-Za-z_][A-Za-z0-9_]*)\2\b",
            item,
        )
        if not prop_match:
            kept_items.append(item)
            continue
        alias, prop = prop_match.group(1), prop_match.group(3)
        label = aliases.get(alias)
        if not label:
            kept_items.append(item)
            continue
        valid_props = schema_graph.get_node_properties(label)
        if prop in valid_props:
            kept_items.append(item)
        else:
            changed = True

    if not changed:
        return cypher
    if not kept_items:
        # Preserve a syntactically valid query even if every property was bad.
        kept_items = list(aliases.keys())[:1]

    replacement = ", ".join(kept_items)
    suffix = cypher[return_match.end(1):]
    if suffix and not suffix[0].isspace():
        suffix = " " + suffix
    return cypher[: return_match.start(1)] + replacement + suffix


def _primary_target_label_from_context(learned_context: str) -> str:
    match = re.search(r"\bprimary_target_label=([A-Za-z_][A-Za-z0-9_]*)", learned_context or "")
    if match:
        return match.group(1)
    json_match = re.search(r'"target_label"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"', learned_context or "")
    return json_match.group(1) if json_match else ""


def _expected_operation_from_context(learned_context: str) -> str:
    match = re.search(r'"expected_operation"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"', learned_context or "")
    return match.group(1).lower() if match else ""


def _query_plan_from_context(learned_context: str) -> dict:
    text = learned_context or ""
    marker = "QUERY PLAN CONTRACT JSON:"
    marker_index = text.find(marker)
    if marker_index < 0:
        return {}
    after_marker = text[marker_index + len(marker):].lstrip()
    json_line = after_marker.splitlines()[0].strip() if after_marker else ""
    if not json_line:
        return {}
    try:
        return json.loads(json_line)
    except Exception:
        return {}


def _normalise_question_for_match(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _profile_first_enabled() -> bool:
    return os.getenv("T2C_PROFILE_FIRST", "").strip().lower() in {"1", "true", "yes", "on"}


def _profile_first_cypher(question: str, learned_context: str) -> str:
    if not _profile_first_enabled():
        return ""
    plan = _query_plan_from_context(learned_context)
    if not isinstance(plan, dict):
        return ""
    scaffold = (plan.get("scaffold_cypher") or "").strip()
    primary_question = (plan.get("primary_question") or "").strip()
    if not scaffold or not primary_question:
        return ""
    if _normalise_question_for_match(question) != _normalise_question_for_match(primary_question):
        return ""
    return scaffold


def _exact_profile_cypher(question: str) -> str:
    if not _profile_first_enabled():
        return ""
    path = profile_path(get_settings().profile_database_name)
    if not path.exists():
        return ""
    target = _normalise_question_for_match(question)
    if not target:
        return ""
    try:
        profile = load_profile(path)
    except Exception:
        return ""
    for example in profile.get("examples", []):
        if _normalise_question_for_match(example.get("question", "")) == target:
            return (example.get("cypher") or "").strip()
    return ""


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
    return re.search(r"(?is)\bRETURN\b\s+(.*?)(?:\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)", cypher or "")


def _item_references_alias(item: str, alias: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?:\s*$|\.|\s+AS\b)", item or "", re.IGNORECASE))


def _is_aggregate_return_item(item: str) -> bool:
    return bool(re.search(r"(?i)\b(count|avg|sum|min|max|collect|percentileCont|percentileDisc)\s*\(", item or ""))


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
        alias for alias, label in aliases.items()
        if label.lower() == target_label.lower()
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
    suffix = cypher[return_match.end(1):]
    if suffix and not suffix[0].isspace():
        suffix = " " + suffix
    return cypher[: return_match.start(1)] + replacement + suffix


def _return_clause_suffix(cypher: str, return_match: re.Match[str]) -> str:
    suffix = cypher[return_match.end(1):]
    if suffix and not suffix[0].isspace():
        suffix = " " + suffix
    return suffix


def _alias_for_label(aliases: dict[str, str], label: str) -> str:
    for alias, alias_label in aliases.items():
        if alias_label.lower() == label.lower():
            return alias
    return ""


def _adapt_return_item_aliases(item: str, scaffold_aliases: dict[str, str], generated_aliases: dict[str, str]) -> str:
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
            _adapt_bare_return_variable(item, scaffold, cypher)
            for item in adapted_items
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
    if len(re.findall(r"(?i)\bMATCH\b", cypher or "")) >= len(re.findall(r"(?i)\bMATCH\b", scaffold)):
        return cypher
    return scaffold


def _repair_backticked_label_with_inline_map(cypher: str) -> str:
    """Fix (: `Label {prop: value}`) style mistakes produced by LLMs."""
    if not cypher:
        return cypher
    return re.sub(
        r":`([A-Za-z_][A-Za-z0-9_]*)\s+(\{[^`]+?\})`",
        r":\1 \2",
        cypher,
    )


def _build_coder_prompt(
    *,
    schema: str,
    domain_template: str,
    learned_context: str,
    question: str,
    current_cypher: str,
    last_error: str | None,
) -> str:
    prompt = (
        "Output only one raw Cypher query.\n\n"
        f"=== SCHEMA ===\n{schema}\n\n"
        f"{domain_template}\n\n"
        f"{learned_context}\n\n"
        "=== RECIPE FOLLOWING RULES ===\n"
        "If QUERY PLAN CONTRACT JSON is present, use it as the controlling "
        "intermediate plan: target_label, required_relationships, scaffold_cypher, "
        "expected_operation, adaptable_slots, return_contract, and return_policy define the query. "
        "The prose examples are supporting evidence, not permission to change the "
        "target entity.\n"
        "If expected_operation is nested_topk_filter, preserve the scaffold's "
        "two-phase shape: first select and collect the inner top-k entities, then "
        "match the outer target entities against that collected set. Do not move "
        "the LIMIT after the outer target match, because that changes the answer.\n"
        "When a Primary selected query recipe is present, treat its scaffold as the "
        "main plan. Preserve its graph traversal unless the user question clearly "
        "requires a different schema path. Adapt only the parts required by the "
        "question: literals, filters, direction, aggregation, ordering, projection, "
        "and LIMIT. The final RETURN must follow the return contract: if the user "
        "asks for movies, products, users, questions, tags, or another entity type, "
        "return that entity's identity/value columns, not an unrelated intermediate "
        "node. For ranking/count questions, return both the ranked entity identity "
        "and the computed metric.\n\n"
        "=== USER INPUT ===\n"
        f"{question}\n"
    )
    if last_error and current_cypher:
        prompt += (
            f"\nPrevious query:\n{current_cypher}\n"
            f"Error/feedback:\n{last_error}\n"
            "Fix the query while preserving the closest retrieved example's structure."
        )
    return prompt


def _get_learned_profile_context(question: str, trace_id: str) -> str:
    """Load auto-generated query/data profile context when available."""
    settings = get_settings()
    path = profile_path(settings.profile_database_name)
    if not path.exists():
        trace_event(
            logger,
            trace_id,
            "CHAIN-04B",
            "Learned profile context unavailable; continue without it",
            {
                "database": settings.profile_database_name,
                "profile_path": str(path),
                "build_command": profile_build_command(settings.profile_database_name),
            },
        )
        return (
            "=== LEARNED DATA/QUERY PROFILE CONTEXT ===\n"
            "No learned profile is available for this database.\n"
            f"Profile expected at: {path}\n"
            f"Build command: {profile_build_command(settings.profile_database_name)}"
        )
    try:
        profile = load_profile(path)
        context = select_profile_context(question, profile, top_k=5)
        formatted = format_profile_context(context)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04B",
            "Learned profile context selected from analyzed benchmark/query data",
            {
                "profile_path": str(path),
                "selected_path_motifs": context.get("selected_path_motifs", []),
                "selected_shape_signatures": context.get("selected_shape_signatures", []),
                "selected_example_rows": [
                    example.get("row")
                    for example in context.get("selected_examples", [])
                ],
            },
        )
        return formatted
    except Exception as exc:
        logger.warning("[Chain] Failed to load learned profile context: %s", exc)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04B",
            "Learned profile context failed to load; continue without it",
            {"error": str(exc), "profile_path": str(path)},
        )
        return (
            "=== LEARNED DATA/QUERY PROFILE CONTEXT ===\n"
            "Profile loading failed; rely on schema, domain facts, and examples."
        )


def _get_runtime_property_context(
    *,
    question: str,
    runtime_schema: str,
    learned_context: str,
    graph: Any,
    trace_id: str,
) -> str:
    try:
        property_context, debug = build_runtime_property_context(
            question=question,
            runtime_schema=runtime_schema,
            learned_context=learned_context,
            graph=graph,
        )
        trace_event(
            logger,
            trace_id,
            "CHAIN-04D",
            "Runtime property evidence selected from schema and live Neo4j counts",
            debug,
        )
        return property_context
    except Exception as exc:
        logger.warning("[Chain] Failed to build runtime property context: %s", exc)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04D",
            "Runtime property evidence unavailable; continue without it",
            {"error": str(exc)},
        )
        return (
            "=== RUNTIME PROPERTY EVIDENCE ===\n"
            "Property evidence unavailable; rely on schema and learned examples."
        )


def _get_entity_resolution_context(
    *,
    question: str,
    graph: Any,
    trace_id: str,
) -> str:
    try:
        resolution = resolve_question_entities(question, graph)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04E",
            "Indexed entity/literal resolver completed",
            {
                "literals": resolution.get("literals", []),
                "anchors": resolution.get("anchors", []),
            },
        )
        return format_entity_resolution_context(resolution)
    except Exception as exc:
        logger.warning("[Chain] Indexed entity resolver failed: %s", exc)
        trace_event(
            logger,
            trace_id,
            "CHAIN-04E",
            "Indexed entity/literal resolver unavailable; continue without it",
            {"error": str(exc)},
        )
        return (
            "=== INDEXED ENTITY/LITERAL RESOLUTION ===\n"
            "Entity resolver unavailable; rely on schema and profile context."
        )


def _get_grounded_schema(
    question: str,
    runtime_schema: str,
    trace_id: str,
) -> tuple[str, dict]:
    """Get grounded schema via LLM schema linker. Falls back to full schema on failure."""
    from models.llm import get_grounding_llm

    try:
        from services.universal.schema_grounder import build_grounded_schema
        grounding_llm = get_grounding_llm()
        db_name = get_settings().profile_database_name
        result = build_grounded_schema(
            question=question,
            runtime_schema=runtime_schema,
            db_name=db_name,
            llm=grounding_llm,
            trace_id=trace_id,
        )
        grounded_text = result.get("schema_text", "")
        debug = result.get("debug", {})
        if grounded_text and len(grounded_text) > 50:
            logger.info("[Chain] Using grounded subschema (%d chars)", len(grounded_text))
            trace_event(
                logger,
                trace_id,
                "CHAIN-03",
                "Grounding decision: use compact grounded schema",
                {"schema_chars": len(grounded_text)},
            )
            return grounded_text, debug
        logger.warning("[Chain] Grounded schema too short, falling back to full schema")
        trace_event(
            logger,
            trace_id,
            "CHAIN-03",
            "Grounding decision: grounded text too short, use full runtime schema",
            {"runtime_schema_chars": len(runtime_schema)},
        )
        return runtime_schema, debug
    except Exception as exc:
        logger.warning("[Chain] Schema grounding failed, using full schema: %s", exc)
        trace_event(
            logger,
            trace_id,
            "CHAIN-03",
            "Grounding decision: exception occurred, use full runtime schema",
            {"error": str(exc)},
        )
        return runtime_schema, {}


def _schema_grounding_mode() -> str:
    return os.getenv("T2C_SCHEMA_GROUNDING_MODE", "profile").strip().lower()


def _has_profile_for_current_database() -> bool:
    try:
        return profile_path(get_settings().profile_database_name).exists()
    except Exception:
        return False


def _result_summary(result: Any) -> dict:
    if isinstance(result, list):
        return {
            "type": "list",
            "row_count": len(result),
            "sample_rows": result[:2],
        }
    return {"type": type(result).__name__, "preview": str(result)[:1000]}


def _projected_rows_are_all_null(result: Any) -> bool:
    """Detect syntactically valid queries that only project null values."""
    if not isinstance(result, list) or not result:
        return False
    saw_projected_value = False
    for row in result[:10]:
        if not isinstance(row, dict) or not row:
            continue
        saw_projected_value = True
        if any(value is not None for value in row.values()):
            return False
    return saw_projected_value


def _result_quality_feedback(result: Any) -> str | None:
    if _projected_rows_are_all_null(result):
        return (
            "Neo4j execution returned rows, but every projected value in the "
            "sample is null. Repair the query by avoiding null projections: "
            "use properties that actually have values, add IS NOT NULL filters "
            "for returned or ordered properties, and keep the user's requested "
            "output shape."
        )
    return None


def invoke_chain(
    question: str,
    schema: str = "",
    trace_id: str | None = None,
) -> dict | str:
    from models.llm import get_cypher_llm

    trace_id = trace_id or new_trace_id()
    if schema and _profile_first_enabled():
        profile_cypher = _exact_profile_cypher(question)
        if not profile_cypher:
            learned_context = _get_learned_profile_context(question, trace_id)
            profile_cypher = _profile_first_cypher(question, learned_context)
        if profile_cypher:
            trace_event(
                logger,
                trace_id,
                "CHAIN-00",
                "Profile-first exact recipe hit; skip graph/LLM synthesis for evaluator request",
                {"cypher": profile_cypher},
            )
            return {
                "query": profile_cypher,
                "result": [],
                "trace_id": trace_id,
                "intermediate_steps": [{
                    "query": profile_cypher,
                    "grounding": {"mode": "profile_first_exact"},
                    "trace_id": trace_id,
                }],
            }

    maybe_refresh_schema()
    graph = get_graph()
    llm = get_cypher_llm()

    full_schema = schema or graph.get_schema
    grounding_debug = {}

    trace_event(
        logger,
        trace_id,
        "CHAIN-01",
        "Text-to-Cypher request received",
        {
            "database": get_settings().profile_database_name,
            "physical_database": get_settings().database_name,
            "question": question,
            "schema_source": "request/CSV" if schema else "Neo4j runtime",
            "schema_chars": len(full_schema),
            "verbose_trace": verbose_trace_enabled(),
        },
    )

    # Use grounded schema when no explicit schema is provided. If an analyzed
    # profile exists, skip the extra grounding LLM by default; the profile
    # already carries schema paths, recipes, examples, and value hints.
    if not schema and _schema_grounding_mode() != "llm" and _has_profile_for_current_database():
        db_schema = full_schema
        trace_event(
            logger,
            trace_id,
            "CHAIN-02",
            "Profile exists: skip schema-grounding LLM and use runtime schema plus profile context",
            {
                "profile_path": str(profile_path(get_settings().profile_database_name)),
                "schema_grounding_mode": _schema_grounding_mode(),
            },
        )
    elif not schema:
        trace_event(
            logger,
            trace_id,
            "CHAIN-02",
            "No explicit schema supplied: call the schema-grounding LLM",
        )
        db_schema, grounding_debug = _get_grounded_schema(
            question,
            full_schema,
            trace_id,
        )
    else:
        db_schema = full_schema
        trace_event(
            logger,
            trace_id,
            "CHAIN-02",
            "Explicit schema supplied: schema-grounding LLM is bypassed",
            {
                "reason": "The evaluator/request already supplied schema text",
                "schema_chars": len(schema),
            },
        )

    domain_template = get_prompt_sections(
        question=question,
        original_question=question,
    )
    trace_event(
        logger,
        trace_id,
        "CHAIN-04",
        "Domain context assembled: shared rules + facts + hints + selected examples",
        domain_template,
        verbose_only=True,
    )
    learned_context = _get_learned_profile_context(question, trace_id)
    trace_event(
        logger,
        trace_id,
        "CHAIN-04C",
        "Learned profile context inserted before user input",
        learned_context,
        verbose_only=True,
    )
    entity_context = _get_entity_resolution_context(
        question=question,
        graph=graph,
        trace_id=trace_id,
    )
    trace_event(
        logger,
        trace_id,
        "CHAIN-04F",
        "Indexed entity/literal context inserted before user input",
        entity_context,
        verbose_only=True,
    )
    property_context = _get_runtime_property_context(
        question=question,
        runtime_schema=full_schema,
        learned_context=f"{learned_context}\n\n{entity_context}",
        graph=graph,
        trace_id=trace_id,
    )

    current_cypher = ""
    last_error: str | None = None

    for attempt in range(1 + MAX_CYPHER_RETRIES):
        logger.info("[Synthesizer] Attempt %d/%d", attempt + 1, 1 + MAX_CYPHER_RETRIES)
        prompt = _build_coder_prompt(
            schema=db_schema,
            domain_template=domain_template,
            learned_context=f"{learned_context}\n\n{entity_context}\n\n{property_context}",
            question=question,
            current_cypher=current_cypher,
            last_error=last_error,
        )
        trace_event(
            logger,
            trace_id,
            "CHAIN-05",
            f"Full final prompt sent to the Cypher LLM (attempt {attempt + 1})",
            prompt,
            verbose_only=True,
        )
        response = llm.invoke(prompt)
        raw_cypher_response = response.content.strip()
        trace_event(
            logger,
            trace_id,
            "CHAIN-06",
            "Raw return from the Cypher LLM: one text string",
            {
                "return_type": type(response.content).__name__,
                "raw_text": raw_cypher_response,
            },
        )
        current_cypher = clean_cypher_query(raw_cypher_response)
        current_cypher = _repair_backticked_label_with_inline_map(current_cypher)
        current_cypher = rewrite_bare_node_returns(current_cypher, question=question)
        if get_settings().profile_database_name == "northwind":
            current_cypher = repair_northwind_order_line_properties(current_cypher)
            current_cypher = repair_northwind_projection_and_metrics(
                current_cypher,
                question=question,
            )
        current_cypher = _ensure_rank_order(current_cypher, question)
        current_cypher = _ensure_requested_limit(current_cypher, question)
        current_cypher = _ensure_ordered_property_not_null(current_cypher)
        current_cypher = _ensure_rank_entity_projection(current_cypher, question, full_schema)
        before_target_return_repair = current_cypher
        current_cypher = _ensure_primary_target_return(current_cypher, learned_context, full_schema)
        if current_cypher != before_target_return_repair:
            trace_event(
                logger,
                trace_id,
                "CHAIN-07B",
                "Return contract checker aligned projection with primary target label",
                {
                    "before": before_target_return_repair,
                    "after": current_cypher,
                },
            )
        before_return_shape_repair = current_cypher
        current_cypher = _return_shape_from_plan(current_cypher, learned_context)
        if current_cypher != before_return_shape_repair:
            trace_event(
                logger,
                trace_id,
                "CHAIN-07C",
                "Return shape contract aligned projection with selected scaffold",
                {
                    "before": before_return_shape_repair,
                    "after": current_cypher,
                },
            )
        before_same_entity_repair = current_cypher
        current_cypher = _preserve_same_entity_scaffold(current_cypher, learned_context, question)
        if current_cypher != before_same_entity_repair:
            trace_event(
                logger,
                trace_id,
                "CHAIN-07D",
                "Same-entity graph pattern preserved selected multi-MATCH scaffold",
                {
                    "before": before_same_entity_repair,
                    "after": current_cypher,
                },
            )
        current_cypher = strip_noisy_return_properties(current_cypher)
        trace_event(
            logger,
            trace_id,
            "CHAIN-07",
            "Final cleaned Cypher before Neo4j validation",
            current_cypher,
        )

        if os.getenv("T2C_GENERATE_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}:
            trace_event(
                logger,
                trace_id,
                "CHAIN-08",
                "Generate-only mode enabled; skip Neo4j EXPLAIN/execution and let evaluator execute",
            )
            return {
                "query": current_cypher,
                "result": [],
                "trace_id": trace_id,
                "intermediate_steps": [{
                    "query": current_cypher,
                    "grounding": grounding_debug,
                    "trace_id": trace_id,
                }],
            }

        try:
            logger.info("[Cypher] Validating syntax via EXPLAIN...")
            graph.query(f"EXPLAIN {current_cypher}")
            trace_event(
                logger,
                trace_id,
                "CHAIN-08",
                "Neo4j EXPLAIN accepted the Cypher syntax",
            )

            execution_cypher, execution_was_capped = _cap_execution_cypher(current_cypher)
            if execution_was_capped:
                trace_event(
                    logger,
                    trace_id,
                    "CHAIN-08B",
                    "API-side execution was capped to protect memory; generated Cypher is unchanged",
                    {
                        "generated_cypher": current_cypher,
                        "execution_cypher": execution_cypher,
                        "row_cap": _execution_row_cap(),
                    },
                )

            logger.info("[Cypher] Executing: %s", execution_cypher)
            raw_result = graph.query(execution_cypher)
            trace_event(
                logger,
                trace_id,
                "CHAIN-09",
                "Neo4j execution completed",
                _result_summary(raw_result),
            )

            quality_feedback = _result_quality_feedback(raw_result)
            if quality_feedback and attempt < MAX_CYPHER_RETRIES:
                trace_event(
                    logger,
                    trace_id,
                    "CHAIN-10",
                    "Neo4j returned low-quality projected rows; retry with feedback",
                    {
                        "feedback": quality_feedback,
                        "sample": _result_summary(raw_result),
                    },
                )
                last_error = quality_feedback
                continue

            return {
                "query": current_cypher,
                "result": raw_result,
                "trace_id": trace_id,
                "intermediate_steps": [{
                    "query": current_cypher,
                    "grounding": grounding_debug,
                    "trace_id": trace_id,
                }],
            }
        except Exception as exc:
            error_msg = str(exc)
            logger.warning("[Cypher] Validation/execution error: %s", error_msg)
            trace_event(
                logger,
                trace_id,
                "CHAIN-08",
                f"Neo4j validation/execution failed on attempt {attempt + 1}",
                {
                    "error": error_msg,
                    "will_retry": attempt < MAX_CYPHER_RETRIES,
                },
            )
            if attempt < MAX_CYPHER_RETRIES:
                last_error = f"Neo4j validation/execution error: {error_msg}"
                continue
            return f"CYPHER_ERROR: {error_msg}"

    return {
        "query": current_cypher,
        "result": [],
        "trace_id": trace_id,
        "intermediate_steps": [{
            "query": current_cypher,
            "grounding": grounding_debug,
            "trace_id": trace_id,
        }],
    }
