"""
GraphCypherQAChain setup.
Uses langchain_neo4j.GraphCypherQAChain (compatible with GraphStore/Neo4jGraph)
instead of langchain_community version which caused validation errors.
Thread-safe singleton with double-checked locking.
"""

from __future__ import annotations

import ast
import logging
import re
import threading
import urllib.parse

from langchain_core.prompts import PromptTemplate
from langchain_neo4j import GraphCypherQAChain
from templates.cypher_templates import get_cypher_template
from utils.helpers import clean_cypher_query

from models.graph import (
    get_graph,
    get_schema_context,
    get_schema_node_properties,
    get_schema_relationship_properties,
    maybe_refresh_schema,
)
from models.llm import get_cypher_llm, get_qa_llm

logger = logging.getLogger(__name__)

_chain: GraphCypherQAChain | None = None
_chain_lock = threading.Lock()


def _build_chain() -> GraphCypherQAChain:
    """Build the GraphCypherQAChain with current config."""
    logger.info("[Chain] Building GraphCypherQAChain...")
    template = get_cypher_template()
    prompt = PromptTemplate(
        input_variables=["schema", "question"],
        template=template,
    )
    chain = GraphCypherQAChain.from_llm(
        cypher_llm=get_cypher_llm(),
        qa_llm=get_qa_llm(),
        graph=get_graph(),
        cypher_prompt=prompt,
        validate_cypher=True,
        return_direct=True,
        verbose=True,
        allow_dangerous_requests=True,
        return_intermediate_steps=True,
        top_k=100,
    )
    logger.info("[Chain] GraphCypherQAChain ready.")
    return chain


def get_chain() -> GraphCypherQAChain:
    """Get or create the chain (thread-safe singleton)."""
    global _chain
    if _chain is None:
        with _chain_lock:
            if _chain is None:  # double-checked locking
                _chain = _build_chain()
    return _chain


def reset_chain() -> None:
    """Force rebuild the chain on next call (e.g. after config change)."""
    global _chain
    with _chain_lock:
        _chain = None
    logger.info("[Chain] Chain reset — will rebuild on next invoke.")


def invoke_chain(question: str) -> dict | str:
    """
    Invoke the chain with a question.
    Returns chain result dict or an error string.
    """
    maybe_refresh_schema()
    chain = get_chain()

    try:
        result = chain.invoke({"query": question}, return_only_outputs=True)
    except Exception as e:
        logger.warning("[Chain] GraphCypher chain error: %s", e)
        fallback_result = _run_fallback_query(question, str(e))
        if fallback_result is not None:
            return fallback_result
        return "Sorry, I couldn't find an answer to your question."

    if result is None:
        fallback_result = _run_fallback_query(question, "No answer was generated.")
        if fallback_result is not None:
            return fallback_result
        return "No answer was generated."

    # Clean and URL-encode the generated Cypher query for Neo4j Browser links
    try:
        steps = result.get("intermediate_steps", [{}])
        if steps and isinstance(steps[-1], dict):
            query_raw = steps[-1].get("query", "")
            if not query_raw:
                fallback_result = _run_fallback_query(
                    question,
                    "The chain did not produce any Cypher query.",
                )
                if fallback_result is not None:
                    return fallback_result
            else:
                cleaned = clean_cypher_query(query_raw)
                if not cleaned:
                    fallback_result = _run_fallback_query(
                        question,
                        "The generated Cypher query was empty after cleaning.",
                    )
                    if fallback_result is not None:
                        return fallback_result
                else:
                    invalid_refs = _find_invalid_property_references(cleaned)
                    contract_errors = _validate_return_contract(question, cleaned)
                    constraint_errors = _validate_query_constraints(question, cleaned)
                    path_errors = _validate_path_hints(question, cleaned)
                    if invalid_refs:
                        logger.warning(
                            "[Chain] Invalid property references detected: %s",
                            ", ".join(invalid_refs),
                        )
                        fallback_result = _run_fallback_query(
                            question,
                            "Invalid property references detected: " + ", ".join(invalid_refs),
                        )
                        if fallback_result is not None:
                            return fallback_result
                    if contract_errors:
                        logger.warning(
                            "[Chain] Return contract mismatch detected: %s",
                            "; ".join(contract_errors),
                        )
                        fallback_result = _run_fallback_query(
                            question,
                            "Return contract mismatch: " + "; ".join(contract_errors),
                        )
                        if fallback_result is not None:
                            return fallback_result
                    if constraint_errors:
                        logger.warning(
                            "[Chain] Query constraint mismatch detected: %s",
                            "; ".join(constraint_errors),
                        )
                        fallback_result = _run_fallback_query(
                            question,
                            "Query constraint mismatch: " + "; ".join(constraint_errors),
                        )
                        if fallback_result is not None:
                            return fallback_result
                    if path_errors:
                        logger.warning(
                            "[Chain] Path hint mismatch detected: %s",
                            "; ".join(path_errors),
                        )
                        fallback_result = _run_fallback_query(
                            question,
                            "Path hint mismatch: " + "; ".join(path_errors),
                        )
                        if fallback_result is not None:
                            return fallback_result
                    encoded = urllib.parse.quote(cleaned)
                    result["intermediate_steps"][-1]["query"] = encoded
        else:
            fallback_result = _run_fallback_query(
                question,
                "The chain returned no intermediate query steps.",
            )
            if fallback_result is not None:
                return fallback_result
    except Exception as e:
        logger.warning("[Chain] Failed to extract/clean Cypher query: %s", e)

    return result


def _run_fallback_query(question: str, error_message: str) -> dict | None:
    """
    Generate and execute a direct Cypher fallback when GraphCypherQAChain fails.
    This keeps the current architecture but adds one low-frequency repair path.
    """
    try:
        graph = get_graph()
        schema_text = graph.get_schema
        schema_context = get_schema_context()
        template = get_cypher_template()
        prompt = (
            template.replace("{schema}", schema_text).replace(
                "{question}",
                (
                    "The previous generated query failed.\n"
                    f"Failure: {error_message}\n\n"
                    "Use the schema and the grounded question below to repair the query.\n"
                    "If the question contains intent hints or schema context, use them.\n\n"
                    f"{question}\n\nCypher Query:"
                ),
            )
            + f"\n\nAdditional schema grounding:\n{schema_context}"
        )
        query_raw = get_cypher_llm().invoke(prompt).content.strip()
        cleaned = clean_cypher_query(query_raw)
        if not cleaned:
            return None
        contract_errors = _validate_return_contract(question, cleaned)
        if contract_errors:
            logger.warning(
                "[Chain] Fallback query still violates return contract: %s",
                "; ".join(contract_errors),
            )
            return None
        constraint_errors = _validate_query_constraints(question, cleaned)
        if constraint_errors:
            logger.warning(
                "[Chain] Fallback query still violates query constraints: %s",
                "; ".join(constraint_errors),
            )
            return None
        path_errors = _validate_path_hints(question, cleaned)
        if path_errors:
            logger.warning(
                "[Chain] Fallback query still violates path hints: %s",
                "; ".join(path_errors),
            )
            return None

        result = graph.query(cleaned)
        encoded = urllib.parse.quote(cleaned)
        logger.info("[Chain] Fallback direct query succeeded.")
        return {
            "result": result,
            "intermediate_steps": [{"query": encoded}],
        }
    except Exception as fallback_error:
        logger.warning("[Chain] Fallback query failed: %s", fallback_error)
        return None


def _find_invalid_property_references(query: str) -> list[str]:
    """
    Lightweight schema guard:
    detect obvious alias.property references that are incompatible with the
    label/relationship properties seen in the current Neo4j schema.
    """
    node_props = get_schema_node_properties()
    rel_props = get_schema_relationship_properties()

    node_aliases: dict[str, set[str]] = {}
    rel_aliases: dict[str, set[str]] = {}

    for alias, label in re.findall(r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", query):
        node_aliases.setdefault(alias, set()).add(label)
    for alias, rel in re.findall(r"\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", query):
        rel_aliases.setdefault(alias, set()).add(rel)

    invalid: list[str] = []
    for alias, prop in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b", query):
        if alias in node_aliases:
            allowed = set()
            for label in node_aliases[alias]:
                allowed.update(node_props.get(label, []))
            if allowed and prop not in allowed:
                invalid.append(f"{alias}.{prop} not in node properties {sorted(allowed)}")
        elif alias in rel_aliases:
            allowed = set()
            for rel in rel_aliases[alias]:
                allowed.update(rel_props.get(rel, []))
            if allowed and prop not in allowed:
                invalid.append(f"{alias}.{prop} not in relationship properties {sorted(allowed)}")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in invalid:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _extract_return_contract(question: str) -> dict[str, object]:
    """Parse the lightweight return contract embedded in the enhanced question."""
    match = re.search(
        r"Return Contract:\s*(.*?)(?:\n[A-Z][A-Za-z ]+:\n|\nVerified Triples:|\nInstance Triples:|\Z)",
        question,
        flags=re.DOTALL,
    )
    if not match:
        return {}

    contract: dict[str, object] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower().replace(" ", "_")
        value = value.strip()
        if key in {"expected_items", "notes"}:
            contract[key] = [item.strip() for item in value.split("|") if item.strip() and item.strip() != "NONE"]
        elif key == "strict":
            contract[key] = value.lower() == "true"
        else:
            contract[key] = value
    return contract


def _extract_query_constraints(question: str) -> dict[str, object]:
    """Parse the lightweight query constraints embedded in the enhanced question."""
    match = re.search(
        r"Query Constraints:\s*(.*?)(?:\n[A-Z][A-Za-z ]+:\n|\nVerified Triples:|\nInstance Triples:|\Z)",
        question,
        flags=re.DOTALL,
    )
    if not match:
        return {}

    constraints: dict[str, object] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower().replace(" ", "_")
        value = value.strip()
        if key == "allow_aggregation":
            constraints[key] = value.lower() == "true"
        elif key == "anchor_lock":
            if value == "NONE":
                constraints[key] = {}
            else:
                try:
                    parsed = ast.literal_eval(value)
                    constraints[key] = parsed if isinstance(parsed, dict) else {}
                except Exception:
                    constraints[key] = {}
        elif key == "notes":
            constraints[key] = [item.strip() for item in value.split("|") if item.strip() and item.strip() != "NONE"]
        else:
            constraints[key] = value
    return constraints


def _extract_path_hints(question: str) -> dict[str, object]:
    """Parse the lightweight path hints embedded in the enhanced question."""
    match = re.search(
        r"Path Hints:\s*(.*?)(?:\n[A-Z][A-Za-z ]+:\n|\nVerified Triples:|\nInstance Triples:|\Z)",
        question,
        flags=re.DOTALL,
    )
    if not match:
        return {}

    hints: dict[str, object] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower().replace(" ", "_")
        value = value.strip()
        if key in {"path_patterns", "notes"}:
            hints[key] = [item.strip() for item in value.split("|") if item.strip() and item.strip() != "NONE"]
        elif key == "needs_distinct":
            hints[key] = value.lower() == "true"
        else:
            hints[key] = value
    return hints


def _split_top_level_commas(text: str) -> list[str]:
    items: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quote = False
    quote_char = ""

    for ch in text:
        if ch in ("'", '"'):
            if not in_quote:
                in_quote = True
                quote_char = ch
            elif quote_char == ch:
                in_quote = False
                quote_char = ""

        if not in_quote:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                item = "".join(buf).strip()
                if item:
                    items.append(item)
                buf = []
                continue

        buf.append(ch)

    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return items


def _extract_clause_items(query: str, clause_name: str) -> list[str]:
    match = re.search(rf"(?is)\b{clause_name}\b(.*)", query)
    if not match:
        return []
    clause_text = match.group(1)
    clause_text = re.split(
        r"\bORDER BY\b|\bLIMIT\b|\bSKIP\b|\bWHERE\b|\bUNION\b",
        clause_text,
        flags=re.IGNORECASE,
    )[0]
    return _split_top_level_commas(clause_text)


def _parse_projection_item(item: str) -> tuple[str, str]:
    cleaned = re.sub(r"(?i)\bDISTINCT\b", "", (item or "")).strip()
    if not cleaned:
        return "", ""
    if " AS " in cleaned.upper():
        left, right = re.split(r"\s+AS\s+", cleaned, flags=re.IGNORECASE, maxsplit=1)
        return left.strip(), right.strip()
    return cleaned, cleaned


def _extract_return_aliases(query: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for item in _extract_clause_items(query, "RETURN"):
        base, alias = _parse_projection_item(item)
        if base:
            aliases[base] = alias
    return aliases


def _extract_with_aliases(query: str) -> dict[str, str]:
    if "WITH" not in query.upper() or "RETURN" not in query.upper():
        return {}
    match = re.search(r"(?is)\bWITH\b(.*)\bRETURN\b", query)
    if not match:
        return {}
    with_text = re.split(
        r"\bORDER BY\b|\bLIMIT\b|\bSKIP\b|\bWHERE\b",
        match.group(1),
        flags=re.IGNORECASE,
    )[0]
    lineage: dict[str, str] = {}
    for item in _split_top_level_commas(with_text):
        base, alias = _parse_projection_item(item)
        if base and alias and base != alias:
            lineage[alias] = base
    return lineage


def _canonical_expr(expr: str) -> str:
    if not expr:
        return ""
    normalized = expr.strip().replace("`", "")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"(?i)\bDISTINCT\b", "", normalized).strip()
    normalized = re.sub(r"\b[a-zA-Z_]\w*\.", "", normalized)
    normalized = re.sub(r"\s*([()/+*\-,:=])\s*", r"\1", normalized)
    normalized = re.sub(
        r"(?i)\bcount\(\s*(?!distinct\b)([a-zA-Z_]\w*)\s*\)",
        "count(*)",
        normalized,
    )
    return normalized.lower()


def _projection_schema(query: str) -> list[dict[str, str]]:
    return_aliases = _extract_return_aliases(query)
    with_aliases = _extract_with_aliases(query)
    schema: list[dict[str, str]] = []
    for base, alias in return_aliases.items():
        lineage_base = with_aliases.get(base, base) if re.fullmatch(r"[A-Za-z_]\w*", base) else base
        schema.append(
            {
                "base": base,
                "alias": alias,
                "canonical_base": _canonical_expr(lineage_base),
                "canonical_alias": alias.lower(),
            }
        )
    return schema


def _contains_aggregation(query: str) -> bool:
    return bool(re.search(r"(?i)\b(count|sum|avg|min|max|collect)\s*(\(|\{)", query))


def _extract_relationship_types(query: str) -> set[str]:
    return {
        rel.upper()
        for rel in re.findall(r"\[\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", query)
    } | {
        rel.upper()
        for rel in re.findall(r"\[\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", query)
    }


def _extract_node_labels(query: str) -> set[str]:
    return {
        label
        for label in re.findall(r"\(\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", query)
    } | {
        label
        for label in re.findall(r"\(\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", query)
    }


def _extract_anchor_nodes(query: str) -> list[dict[str, str]]:
    anchors: list[dict[str, object]] = []
    pattern = re.compile(
        r"\(\s*(?P<alias>[A-Za-z_][A-Za-z0-9_]*)?\s*:\s*(?P<label>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\{\s*(?P<body>[^}]*)\s*\})?\s*\)"
    )
    for match in pattern.finditer(query):
        body = match.group("body") or ""
        props: dict[str, str] = {}
        for part in body.split(","):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            props[key.strip().strip("`")] = value.strip().strip("'\"")
        anchors.append(
            {
                "alias": (match.group("alias") or "").strip(),
                "label": match.group("label").strip(),
                "props": props,
            }
        )

    if anchors:
        by_alias = {
            str(node.get("alias", "")): node
            for node in anchors
            if str(node.get("alias", ""))
        }
        for alias, prop, value in re.findall(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*['\"]([^'\"]+)['\"]",
            query,
        ):
            node = by_alias.get(alias)
            if node is None:
                continue
            props = node.setdefault("props", {})
            if isinstance(props, dict):
                props[prop] = value
    return anchors


def _validate_path_hints(question: str, query: str) -> list[str]:
    """Ensure generated Cypher preserves the hinted relationship family."""
    hints = _extract_path_hints(question)
    if not hints:
        return []

    path_patterns = [item for item in (hints.get("path_patterns", []) or []) if isinstance(item, str)]
    if not path_patterns:
        return []

    rels_in_query = _extract_relationship_types(query)
    labels_in_query = _extract_node_labels(query)
    errors: list[str] = []

    for pattern in path_patterns:
        required_rels = {
            rel.upper()
            for rel in re.findall(r"\[:([A-Za-z_][A-Za-z0-9_]*)\]", pattern)
        }
        if required_rels and not required_rels.issubset(rels_in_query):
            errors.append(
                "missing required relationship(s): " + ", ".join(sorted(required_rels - rels_in_query))
            )
        required_labels = {
            label
            for label in re.findall(r":([A-Za-z_][A-Za-z0-9_]*)\)", pattern)
            if "|" not in label
        }
        if required_labels and not required_labels.issubset(labels_in_query):
            errors.append(
                "missing required label(s): " + ", ".join(sorted(required_labels - labels_in_query))
            )

    if any("MENTIONS" in pattern for pattern in path_patterns):
        if "contains" in query.lower() and "mentions" not in query.lower():
            errors.append("mention questions must use the MENTIONS relationship, not text contains matching")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in errors:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _validate_query_constraints(question: str, query: str) -> list[str]:
    """Validate projection/aggregation/anchor locks embedded in the enhanced question."""
    constraints = _extract_query_constraints(question)
    if not constraints:
        return []

    errors: list[str] = []
    projection_lock = str(constraints.get("projection_lock", "") or "").lower()
    allow_aggregation = bool(constraints.get("allow_aggregation", False))
    aggregation_style = str(constraints.get("aggregation_style", "") or "").lower()
    query_mode = str(constraints.get("query_mode", "") or "").lower()
    anchor_lock = constraints.get("anchor_lock", {})
    actual_items = _projection_schema(query)

    if projection_lock == "full_node":
        for item in actual_items:
            base = item.get("base", "")
            if "." in base or "(" in base or "{" in base:
                errors.append("projection lock requires returning full nodes, not projected properties or aggregates")
                break

    if projection_lock == "properties":
        for item in actual_items:
            base = item.get("base", "")
            alias = item.get("alias", "")
            if re.fullmatch(r"[A-Za-z_]\w*", base) and alias == base:
                errors.append("projection lock requires projected properties/metrics, not full nodes")
                break

    if not allow_aggregation and _contains_aggregation(query):
        errors.append("aggregation is not allowed for this question")

    if query_mode == "rank_graph_count":
        if "count{" not in query.lower() and not re.search(r"(?i)\bcount\s*\(\s*\*\s*\)", query):
            errors.append("graph-count mode requires an explicit count projection")
        if re.search(r"(?i)\b(return|with)\b[^\n]*\b[a-zA-Z_]\w*\.(followers|following|statuses)\b", query):
            errors.append("graph-count mode should not fallback to nullable count-like properties")

    if aggregation_style == "path_count" and "count{" not in query.lower() and not re.search(r"(?i)\bcount\s*\(\s*\*\s*\)", query):
        errors.append("path_count aggregation should use count{} or count(*)")

    if isinstance(anchor_lock, dict) and anchor_lock:
        expected_label = str(anchor_lock.get("label", "") or "")
        expected_property = str(anchor_lock.get("property", "") or "")
        expected_value = str(anchor_lock.get("value", "") or "")
        anchors = _extract_anchor_nodes(query)

        matched_label = [node for node in anchors if node.get("label") == expected_label]
        if not matched_label:
            errors.append(f"anchor lock requires label '{expected_label}'")
        elif expected_property:
            matched_prop = [
                node for node in matched_label
                if expected_property in (node.get("props", {}) or {})
            ]
            if not matched_prop:
                errors.append(
                    f"anchor lock requires property '{expected_property}' on label '{expected_label}'"
                )
            elif expected_value:
                matched_value = [
                    node for node in matched_prop
                    if str((node.get("props", {}) or {}).get(expected_property, "")) == expected_value
                ]
                if not matched_value:
                    errors.append(
                        f"anchor lock requires {expected_label}.{expected_property} = '{expected_value}'"
                    )

    deduped: list[str] = []
    seen: set[str] = set()
    for item in errors:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _validate_return_contract(question: str, query: str) -> list[str]:
    """Check RETURN contract using the same projection logic as t2c comparator."""
    contract = _extract_return_contract(question)
    if not contract or not contract.get("strict"):
        return []

    expected_items_raw = [item for item in (contract.get("expected_items", []) or []) if isinstance(item, str)]
    if not expected_items_raw:
        return []

    expected_query = "RETURN " + ", ".join(expected_items_raw)
    expected_items = _projection_schema(expected_query)
    actual_items = _projection_schema(query)
    errors: list[str] = []

    if len(actual_items) != len(expected_items):
        errors.append(
            f"expected {len(expected_items)} return item(s) but got {len(actual_items)}"
        )

    compare_len = min(len(actual_items), len(expected_items))
    for idx in range(compare_len):
        expected = expected_items[idx]
        actual = actual_items[idx]
        expected_alias = expected["canonical_alias"]
        actual_alias = actual["canonical_alias"]
        expected_base = expected["canonical_base"]
        actual_base = actual["canonical_base"]

        if expected_alias and expected_alias != expected_base:
            if actual_alias != expected_alias:
                errors.append(
                    f"return item {idx + 1} should expose alias '{expected_alias}' but got '{actual_alias or actual_base}'"
                )
                continue

        if expected_base and actual_base and expected_base != actual_base:
            errors.append(
                f"return item {idx + 1} should have canonical base '{expected_base}' but got '{actual_base}'"
            )

    return errors
