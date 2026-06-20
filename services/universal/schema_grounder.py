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
from utils.pipeline_trace import trace_event

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
    trace_id: str = "unknown",
) -> dict:
    """Main entry: LLM schema linking + algorithmic validation.
    
    Returns dict with:
        schema_text: compact grounded schema for Cypher prompt
        debug: raw LLM output, validated selection, dropped items
    """
    from services.universal.schema_parser import parse_schema_text
    
    if schema_graph is None:
        schema_graph = parse_schema_text(runtime_schema)

    trace_event(
        logger,
        trace_id,
        "GROUND-01",
        "Runtime schema parsed into a graph",
        {
            "labels": schema_graph.get_all_labels(),
            "relationships": schema_graph.get_all_rel_types(),
            "traversal_paths": [
                {
                    "from": path.start,
                    "relationship": path.rel_type,
                    "to": path.end,
                    "direction": path.direction,
                }
                for path in schema_graph.paths
            ],
        },
    )
    
    # Step 1: Ask LLM to link schema
    raw_llm = _call_grounder_llm(
        question,
        runtime_schema,
        db_name,
        llm,
        trace_id=trace_id,
    )
    
    # Step 2: Validate against runtime schema
    validated, dropped = _validate_selection(raw_llm, schema_graph)
    trace_event(
        logger,
        trace_id,
        "GROUND-04",
        "Grounding JSON validated against the runtime schema",
        {
            "validated_labels": sorted(validated.labels),
            "validated_relationships": sorted(validated.relationships),
            "validated_paths": validated.paths,
            "entity_bindings": validated.entity_bindings,
            "return_contract": validated.return_contract,
            "metric_contract": validated.metric_contract,
            "dropped_hallucinated_or_invalid_items": dropped,
        },
    )
    
    # Step 3: Expand paths if needed
    paths_before_expansion = list(validated.paths)
    validated = _expand_paths(validated, schema_graph)
    trace_event(
        logger,
        trace_id,
        "GROUND-05",
        "Traversal paths after deterministic shortest-path expansion",
        {
            "llm_selected_paths": paths_before_expansion,
            "final_paths": validated.paths,
            "algorithm_added_paths": [
                path for path in validated.paths if path not in paths_before_expansion
            ],
        },
    )
    
    # Step 4: Format compact schema
    schema_text = _format_grounded_schema(validated, question, db_name)
    trace_event(
        logger,
        trace_id,
        "GROUND-06",
        "Schema grounder return value: text inserted into the final Cypher prompt",
        schema_text,
    )
    
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


def _call_grounder_llm(
    question: str,
    runtime_schema: str,
    db_name: str,
    llm,
    *,
    trace_id: str,
) -> dict:
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
- Return a JSON object, not a paragraph and not Cypher. The paths array may contain multiple traversal steps.
- Preserve relationship direction from schema.
- For entity values in the question (e.g. names, titles), map them to the most likely label.property.
- Include ALL labels needed to complete the query path, not just start/end.
- Include relationship properties when the question asks about relationship data.
- If the requested metric/time property is absent from the schema, leave metric_contract empty.
- Never assume an implicit timestamp, identifier, property, or relationship.

Database: {db_name}
Schema:
{schema_truncated}

Question: {question}
"""
    trace_event(
        logger,
        trace_id,
        "GROUND-02",
        "Full prompt sent to the schema-grounding LLM",
        prompt,
        verbose_only=True,
    )
    try:
        response = llm.invoke(prompt)
        content = response.content.strip()
        parsed = _extract_json(content)
        trace_event(
            logger,
            trace_id,
            "GROUND-03",
            "Grounding LLM return contract: raw text parsed into a JSON object",
            {
                "raw_llm_text": content,
                "parsed_json": parsed,
                "return_type": type(parsed).__name__,
                "path_count": len(parsed.get("paths", [])) if isinstance(parsed, dict) else 0,
            },
            verbose_only=True,
        )
        if not parsed:
            trace_event(
                logger,
                trace_id,
                "GROUND-03",
                "Grounding LLM returned no usable JSON; fallback may use full schema",
            )
        return parsed
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
    dropped = {
        "labels": [],
        "relationships": [],
        "node_properties": {},
        "rel_properties": {},
        "paths": [],
        "entity_bindings": [],
        "return_contract": [],
        "metric_contract": [],
        "notes": [],
    }
    if not isinstance(raw, dict):
        return sel, dropped

    def list_field(name: str) -> list:
        value = raw.get(name)
        return value if isinstance(value, list) else []

    def dict_field(name: str) -> dict:
        value = raw.get(name)
        return value if isinstance(value, dict) else {}
    
    all_labels = set(schema.get_all_labels())
    all_rels = set(schema.get_all_rel_types())
    
    # Validate labels
    for label in list_field("labels"):
        if isinstance(label, str) and label in all_labels:
            sel.labels.add(label)
        else:
            dropped["labels"].append(label)
    
    # Validate relationships
    for rel in list_field("relationships"):
        if isinstance(rel, str) and rel in all_rels:
            sel.relationships.add(rel)
        else:
            dropped["relationships"].append(rel)
    
    # Validate node properties
    for label, props in dict_field("node_properties").items():
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
    for rel_type, props in dict_field("rel_properties").items():
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
    for path in list_field("paths"):
        if not isinstance(path, dict):
            dropped["paths"].append(path)
            continue
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
    
    # Validate entity bindings instead of copying LLM-proposed schema refs.
    for binding in list_field("entity_bindings"):
        if not isinstance(binding, dict):
            dropped["entity_bindings"].append(binding)
            continue
        text = binding.get("text", "")
        label = binding.get("candidate_label", "")
        prop = binding.get("candidate_property", "")
        valid = bool(
            isinstance(text, str)
            and text.strip()
            and isinstance(label, str)
            and label in all_labels
            and (
                not prop
                or (
                    isinstance(prop, str)
                    and prop in schema.get_node_properties(label)
                )
            )
        )
        if not valid:
            dropped["entity_bindings"].append(binding)
            continue
        clean_binding = {
            "text": text,
            "candidate_label": label,
            "candidate_property": prop,
        }
        sel.entity_bindings.append(clean_binding)
        sel.labels.add(label)
        if prop:
            sel.node_properties.setdefault(label, set()).add(prop)

    # Return fields are executable prompt constraints, so validate every
    # qualified schema reference. Bare count(*) is schema-independent and safe.
    for item in list_field("return_contract"):
        if not isinstance(item, str) or not item.strip():
            dropped["return_contract"].append(item)
            continue
        refs = re.findall(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b",
            item,
        )
        valid_refs = True
        for owner, prop in refs:
            if owner in all_labels and prop in schema.get_node_properties(owner):
                sel.labels.add(owner)
                sel.node_properties.setdefault(owner, set()).add(prop)
            elif owner in all_rels and prop in schema.get_rel_properties(owner):
                sel.relationships.add(owner)
                sel.rel_properties.setdefault(owner, set()).add(prop)
            else:
                valid_refs = False
                break
        safe_unqualified = bool(
            re.fullmatch(r"(?i)\s*count\s*\(\s*\*\s*\)\s*", item)
            or item.strip() in all_labels
        )
        if (refs and valid_refs) or (not refs and safe_unqualified):
            sel.return_contract.append(item)
        else:
            dropped["return_contract"].append(item)

    metric_contract = raw.get("metric_contract", "")
    if isinstance(metric_contract, str) and metric_contract.strip():
        metric_lower = metric_contract.lower()
        schema_properties = {
            prop.lower()
            for label in all_labels
            for prop in schema.get_node_properties(label)
        } | {
            prop.lower()
            for rel_type in all_rels
            for prop in schema.get_rel_properties(rel_type)
        }
        references_schema_property = any(
            re.search(rf"\b{re.escape(prop)}\b", metric_lower)
            for prop in schema_properties
        )
        uses_schema_independent_aggregate = bool(
            re.search(r"\b(count|sum|average|avg|min|max)\b", metric_lower)
        )
        if references_schema_property or uses_schema_independent_aggregate:
            sel.metric_contract = metric_contract.strip()
        else:
            dropped["metric_contract"].append(metric_contract)

    # Free-form notes are useful for diagnostics but unsafe as executable
    # prompt context because they can contain unsupported assumptions.
    dropped["notes"].extend(
        note for note in list_field("notes") if isinstance(note, str)
    )
    sel.notes = []
    
    return sel, dropped


def _expand_paths(sel: GroundingSelection, schema: SchemaGraph) -> GroundingSelection:
    """If labels are selected but no paths connect them, find shortest paths."""
    # A relationship type with one runtime shape is unambiguous even when the
    # LLM omitted its endpoint labels/path. Never guess when a type is polymorphic.
    selected_rel_types = set(sel.relationships)
    existing_rel_types = {
        path.get("relationship", "") for path in sel.paths
    }
    for rel_type in sorted(selected_rel_types - existing_rel_types):
        candidates = [path for path in schema.paths if path.rel_type == rel_type]
        if len(candidates) != 1:
            continue
        step = candidates[0]
        sel.paths.append(
            {
                "from": step.start,
                "relationship": step.rel_type,
                "to": step.end,
            }
        )
        sel.labels.add(step.start)
        sel.labels.add(step.end)

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
