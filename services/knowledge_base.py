"""
Knowledge Base Service — loads extracted graph knowledge and formats it
for injection into LLM prompts.

The knowledge base provides ground truth that eliminates LLM guessing:
  - Property Location Map: "rating is on [:REVIEWED], NOT on :Movie"
  - Match Property Table: "match Movie by .title (UNIQUE)"
  - Ambiguous Entities: "'Neo4j' exists in both :Me and :User"
  - Direction Table: "(:Person)-[:ACTED_IN]->(:Movie), never reverse"
  - Pitfalls: "unitPrice on both :Product and [:ORDERS]"

This is the core research contribution: automated knowledge extraction
from graph databases to ground LLM reasoning.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

_KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge")
_cache: dict[str, dict] = {}


def load_knowledge(database: str) -> dict:
    """Load knowledge base JSON for a database."""
    if database in _cache:
        return _cache[database]

    path = os.path.join(_KNOWLEDGE_DIR, f"{database}.json")
    if not os.path.exists(path):
        logger.warning("[KB] No knowledge file for '%s' at %s", database, path)
        return {}

    with open(path, "r", encoding="utf-8") as f:
        kb = json.load(f)

    _cache[database] = kb
    logger.info("[KB] Loaded knowledge for '%s': %d bytes", database, os.path.getsize(path))
    return kb


def format_for_cypher_prompt(database: str) -> str:
    """
    Format knowledge as a compact text block for the Cypher generation prompt.
    This is injected into the template alongside schema and examples.
    """
    kb = load_knowledge(database)
    if not kb:
        return ""

    sections: list[str] = []

    # 1. Property Location Map — most critical for accuracy
    prop_loc = _format_property_locations(kb)
    if prop_loc:
        sections.append("PROPERTY LOCATIONS (use these, not guesses):\n" + prop_loc)

    # 2. Match Property Table
    match_table = _format_match_table(kb)
    if match_table:
        sections.append("ENTITY MATCHING (use these properties for WHERE/MATCH):\n" + match_table)

    # 3. Ambiguous Entities
    ambiguous = _format_ambiguous(kb)
    if ambiguous:
        sections.append("AMBIGUOUS ENTITIES (choose correct label):\n" + ambiguous)

    # 4. Property Confusion Warnings
    confusion = _format_confusion(kb)
    if confusion:
        sections.append("PROPERTY CONFUSION (same name, different location):\n" + confusion)

    # 5. Direction Table
    directions = _format_directions(kb)
    if directions:
        sections.append("RELATIONSHIP DIRECTIONS (follow exactly):\n" + directions)

    return "\n\n".join(sections)


def format_for_planner_prompt(database: str) -> str:
    """
    Format knowledge for the Planner agent prompt.
    More detailed than Cypher prompt — includes samples and counts.
    """
    kb = load_knowledge(database)
    if not kb:
        return ""

    sections: list[str] = []

    # Everything from Cypher prompt
    cypher_kb = format_for_cypher_prompt(database)
    if cypher_kb:
        sections.append(cypher_kb)

    # Plus: sample values
    samples = _format_samples(kb)
    if samples:
        sections.append("SAMPLE VALUES (real data):\n" + samples)

    # Plus: sample triples
    triples = _format_sample_triples(kb)
    if triples:
        sections.append("SAMPLE DATA PATHS:\n" + triples)

    return "\n\n".join(sections)


def _format_property_locations(kb: dict) -> str:
    """Format property location map."""
    lines = []
    # Node properties
    for label, props in kb.get("node_properties", {}).items():
        for prop, info in props.items():
            lines.append(f"  NODE :{label}.{prop} (type: {info.get('type', '?')})")
    # Relationship properties
    for rel, props in kb.get("relationship_properties", {}).items():
        for prop, info in props.items():
            lines.append(f"  REL  [:{rel}].{prop} (type: {info.get('type', '?')})")
    return "\n".join(lines) if lines else ""


def _format_match_table(kb: dict) -> str:
    """Format unique constraint match table."""
    lines = []
    for c in kb.get("unique_constraints", []):
        lines.append(f"  :{c['label']} → match by .{c['property']} (UNIQUE)")
    return "\n".join(lines) if lines else ""


def _format_ambiguous(kb: dict) -> str:
    """Format ambiguous entities."""
    lines = []
    for a in kb.get("ambiguous_entities", []):
        lines.append(f"  \"{a['value']}\" ({a['property']}) exists in: {a['labels']}")
    return "\n".join(lines) if lines else ""


def _format_confusion(kb: dict) -> str:
    """Format property confusion warnings."""
    lines = []
    for c in kb.get("property_confusion", []):
        lines.append(
            f"  .{c['property']} exists on BOTH {c['on_node']} ({c['node_type']}) "
            f"AND {c['on_rel']} ({c['rel_type']}) — use the correct one!"
        )
    return "\n".join(lines) if lines else ""


def _format_directions(kb: dict) -> str:
    """Format relationship direction table."""
    lines = []
    for p in kb.get("relationship_patterns", []):
        lines.append(f"  (:{p['from']})-[:{p['rel']}]->(:{p['to']})  x{p['count']}")
    return "\n".join(lines) if lines else ""


def _format_samples(kb: dict) -> str:
    """Format sample values."""
    lines = []
    for label, props in kb.get("sample_values", {}).items():
        for prop, vals in props.items():
            vals_str = ", ".join(f'"{v}"' for v in vals[:5])
            lines.append(f"  :{label}.{prop}: {vals_str}")
    return "\n".join(lines) if lines else ""


def _format_sample_triples(kb: dict) -> str:
    """Format sample triples."""
    lines = []
    for t in kb.get("sample_triples", []):
        f = t["from"]
        to = t["to"]
        rp = t.get("rel_properties", {})
        rp_str = f" {rp}" if rp else ""
        lines.append(
            f"  (:{f['label']} {{{f['property']}: '{f['value']}'}}) "
            f"-[:{t['rel']}{rp_str}]-> "
            f"(:{to['label']} {{{to['property']}: '{to['value']}'}})"
        )
    return "\n".join(lines) if lines else ""
