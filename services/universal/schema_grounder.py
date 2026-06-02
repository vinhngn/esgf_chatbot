"""
Universal LLM-based schema grouder.
Works with ANY Neo4j database - no domain hardcode.

Flow:
  1. Build candidate list from parsed schema
  2. Ask LLM to select relevant schema components
  3. Validate selections against runtime schema
  4. Expand missing connecting paths
  5. Format compact grounded schema
"""
from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass, field
from services.universal.schema_parser import SchemaGraph, ParsedPath

logger = logging.getLogger(__name__)

_MAX_SCHEMA_CHARS = 12000

@dataclass
class GroundingSelection:
    labels: set[str] = field(default_factory=set)
    relationships: set[str] = field(default_factory=set)
    node_properties: dict[str, set[str]] = field(default_factory=dict)
    rel_properties: dict[str, set[str]] = field(default_factory=dict)
    paths: list[dict] = field(default_factory=list)
    entity_bindings: list[dict] = field(default_factory=list)
    return_contract: list[str] = field(default_factory=list)
    metric_contract: str = ""
    notes: list[str] = field(default_factory=list)


def build_grounded_schema(
    *,
    question: str,
    runtime_schema: str,
    db_name: str,
    llm,
    schema_graph: SchemaGraph | None = None,
) -> dict:
    """Main entry: LLM schema linking + algorithmic validation.
    
    Returns dict with:
        schema_text: compact grounded schema for Cypher prompt
        debug: raw LLM output, validated selection, dropped items
    """
    from services.universal.schema_parser import parse_schema_text
    
    if schema_graph is None:
        schema_graph = parse_schema_text(runtime_schema)
    
    # Step 1: Ask LLM to link schema
    raw_llm = _call_grounder_llm(question, runtime_schema, db_name, llm)
    
    # Step 2: Validate against runtime schema
    validated, dropped = _validate_selection(raw_llm, schema_graph)
    
    # Step 3: Expand paths if needed
    validated = _expand_paths(validated, schema_graph)
    
    # Step 4: Format compact schema
    schema_text = _format_grounded_schema(validated, question, db_name)
    
    return {
        "schema_text": schema_text,
        "debug": {
            "raw_llm": raw_llm,
            "validated": {
                "labels": sorted(validated.labels),
                "relationships": sorted(validated.relationships),
                "node_properties": {k: sorted(v) for k, v in validated.node_properties.items()},
                "rel_properties": {k: sorted(v) for k, v in validated.rel_properties.items()},
                "paths": validated.paths,
                "entity_bindings": validated.entity_bindings,
                "return_contract": validated.return_contract,
                "metric_contract": validated.metric_contract,
                "notes": validated.notes,
            },
            "dropped": dropped,
        },
    }


def _call_grounder_llm(question: str, runtime_schema: str, db_name: str, llm) -> dict:
    schema_truncated = runtime_schema[:_MAX_SCHEMA_CHARS]
    prompt = f"""You are a schema linker for Neo4j Text-to-Cypher. Given a user question and database schema, identify which schema components are needed to answer the question.

Return ONLY valid JSON. Do NOT generate Cypher.

Required JSON shape:
{{
  "intent": "short description of what the question asks",
  "labels": ["NodeLabel1", "NodeLabel2"],
  "relationships": ["REL_TYPE1"],
  "node_properties": {{"NodeLabel": ["prop1", "prop2"]}},
  "rel_properties": {{"REL_TYPE": ["prop1"]}},
  "paths": [
    {{"from": "NodeLabel1", "relationship": "REL_TYPE", "to": "NodeLabel2"}}
  ],
  "entity_bindings": [
    {{"text": "literal from question", "candidate_label": "NodeLabel", "candidate_property": "property"}}
  ],
  "return_contract": ["NodeLabel.property or aggregate"],
  "metric_contract": "describe ranking/aggregation intent if any",
  "notes": ["important observation about schema usage"]
}}

Rules:
- Use ONLY labels, relationships, and properties present in the schema below.
- Do NOT invent new schema elements.
- Preserve relationship direction from schema.
- For entity values in the question (e.g. names, titles), map them to the most likely label.property.
- Include ALL labels needed to complete the query path, not just start/end.
- Include relationship properties when the question asks about relationship data.

Database: {db_name}
Schema:
{schema_truncated}

Question: {question}
"""
    try:
        response = llm.invoke(prompt)
        content = response.content.strip()
        return _extract_json(content)
    except Exception as exc:
        logger.warning("[SchemaGrounder] LLM call failed: %s", exc)
        return {}


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", text or "", flags=re.IGNORECASE)
    cleaned = cleaned.replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        return json.loads(cleaned[start:end + 1])
    except Exception:
        return {}


def _validate_selection(raw: dict, schema: SchemaGraph) -> tuple[GroundingSelection, dict]:
    """Validate LLM output against runtime schema. Returns (valid, dropped)."""
    sel = GroundingSelection()
    dropped = {"labels": [], "relationships": [], "node_properties": {}, "rel_properties": {}, "paths": []}
    
    all_labels = set(schema.get_all_labels())
    all_rels = set(schema.get_all_rel_types())
    
    # Validate labels
    for label in raw.get("labels", []):
        if isinstance(label, str) and label in all_labels:
            sel.labels.add(label)
        else:
            dropped["labels"].append(label)
    
    # Validate relationships
    for rel in raw.get("relationships", []):
        if isinstance(rel, str) and rel in all_rels:
            sel.relationships.add(rel)
        else:
            dropped["relationships"].append(rel)
    
    # Validate node properties
    for label, props in (raw.get("node_properties") or {}).items():
        if label not in all_labels:
            dropped["node_properties"][label] = props
            continue
        schema_props = schema.get_node_properties(label)
        valid_props = set()
        for p in (props if isinstance(props, list) else []):
            if p in schema_props:
                valid_props.add(p)
            else:
                dropped["node_properties"].setdefault(label, []).append(p)
        if valid_props:
            sel.node_properties[label] = valid_props
            sel.labels.add(label)  # ensure label included
    
    # Validate rel properties
    for rel_type, props in (raw.get("rel_properties") or {}).items():
        if rel_type not in all_rels:
            dropped["rel_properties"][rel_type] = props
            continue
        schema_props = schema.get_rel_properties(rel_type)
        valid_props = set()
        for p in (props if isinstance(props, list) else []):
            if p in schema_props:
                valid_props.add(p)
            else:
                dropped["rel_properties"].setdefault(rel_type, []).append(p)
        if valid_props:
            sel.rel_properties[rel_type] = valid_props
            sel.relationships.add(rel_type)
    
    # Validate paths
    for path in (raw.get("paths") or []):
        start = path.get("from", "")
        rel = path.get("relationship", "")
        end = path.get("to", "")
        # Check path exists in schema
        exists = any(
            p.start == start and p.rel_type == rel and p.end == end
            for p in schema.paths
        )
        if exists:
            sel.paths.append(path)
            sel.labels.add(start)
            sel.labels.add(end)
            sel.relationships.add(rel)
        else:
            dropped["paths"].append(path)
    
    # Copy through entity_bindings, return_contract, metric_contract, notes
    sel.entity_bindings = raw.get("entity_bindings", [])
    sel.return_contract = raw.get("return_contract", [])
    sel.metric_contract = raw.get("metric_contract", "")
    sel.notes = raw.get("notes", [])
    
    return sel, dropped


def _expand_paths(sel: GroundingSelection, schema: SchemaGraph) -> GroundingSelection:
    """If labels are selected but no paths connect them, find shortest paths."""
    if len(sel.labels) < 2:
        return sel
    
    # Check which label pairs are already connected by selected paths
    connected_pairs = set()
    for p in sel.paths:
        connected_pairs.add((p.get("from", ""), p.get("to", "")))
    
    labels = sorted(sel.labels)
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = labels[i], labels[j]
            if (a, b) in connected_pairs or (b, a) in connected_pairs:
                continue
            # Find shortest path
            shortest = schema.find_shortest_path(a, b, max_hops=3)
            if shortest:
                for step in shortest:
                    path_dict = {
                        "from": step.start,
                        "relationship": step.rel_type,
                        "to": step.end,
                    }
                    sel.paths.append(path_dict)
                    sel.labels.add(step.start)
                    sel.labels.add(step.end)
                    sel.relationships.add(step.rel_type)
                    connected_pairs.add((step.start, step.end))
    
    return sel


def _format_grounded_schema(sel: GroundingSelection, question: str, db_name: str) -> str:
    """Format compact grounded schema text for Cypher prompt."""
    parts = [
        f"=== GROUNDED SUBSCHEMA ({db_name}) ===",
        "",
    ]
    
    # Nodes with properties
    if sel.labels:
        parts.append("Nodes:")
        for label in sorted(sel.labels):
            props = sel.node_properties.get(label, set())
            if props:
                parts.append(f"  - {label} {{{', '.join(sorted(props))}}}")
            else:
                parts.append(f"  - {label}")
        parts.append("")
    
    # Relationships with properties
    if sel.paths:
        parts.append("Relationships:")
        seen_patterns = set()
        for p in sel.paths:
            pattern = f"(:{p['from']})-[:{p['relationship']}]->(:{p['to']})"
            if pattern not in seen_patterns:
                seen_patterns.add(pattern)
                rel_props = sel.rel_properties.get(p["relationship"], set())
                if rel_props:
                    pattern = f"(:{p['from']})-[:{p['relationship']} {{{', '.join(sorted(rel_props))}}}]->(:{p['to']})"
                parts.append(f"  - {pattern}")
        parts.append("")
    
    # Entity bindings
    if sel.entity_bindings:
        parts.append("Entity bindings:")
        for eb in sel.entity_bindings:
            text = eb.get("text", "")
            label = eb.get("candidate_label", "")
            prop = eb.get("candidate_property", "")
            if text and label:
                binding = f'"{text}" -> {label}'
                if prop:
                    binding += f".{prop}"
                parts.append(f"  - {binding}")
        parts.append("")
    
    # Return contract
    if sel.return_contract:
        parts.append("Return contract:")
        for rc in sel.return_contract:
            parts.append(f"  - {rc}")
        parts.append("")
    
    # Metric contract
    if sel.metric_contract:
        parts.append(f"Metric contract: {sel.metric_contract}")
        parts.append("")
    
    # Notes
    if sel.notes:
        parts.append("Notes:")
        for note in sel.notes:
            parts.append(f"  - {note}")
    
    return "\n".join(parts)
