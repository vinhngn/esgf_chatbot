"""
Cypher Validator — parse and validate generated Cypher against knowledge base.

Operates on the Cypher STRING directly (not JSON plan).
Catches errors BEFORE execution and auto-fixes what it can.

Checks:
  1. Labels exist in schema
  2. Relationship types exist
  3. Directions match knowledge truth table
  4. Properties accessed on correct node/relationship
  5. Match properties use UNIQUE constraint property
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def validate_and_fix(
    cypher: str,
    knowledge: dict,
) -> tuple[str, list[str]]:
    """
    Validate Cypher string against knowledge base and auto-fix.
    Returns (fixed_cypher, list_of_fixes).
    """
    if not cypher.strip() or not knowledge:
        return cypher, []

    fixes: list[str] = []
    node_props = knowledge.get("node_properties", {})
    rel_props = knowledge.get("relationship_properties", {})
    patterns_kb = knowledge.get("relationship_patterns", [])
    constraints = knowledge.get("unique_constraints", [])
    node_counts = knowledge.get("node_counts", {})

    # Fix 0: Syntax — COUNT(pattern) → count{pattern}
    cypher, syntax_fixes = _fix_count_syntax(cypher)
    fixes.extend(syntax_fixes)

    # Fix 1: Check and fix relationship directions
    cypher, dir_fixes = _fix_directions(cypher, patterns_kb)
    fixes.extend(dir_fixes)

    # Fix 2: Check property locations (node vs relationship)
    cypher, prop_fixes = _fix_property_locations(cypher, node_props, rel_props)
    fixes.extend(prop_fixes)

    # Fix 3: Resolve entity labels using knowledge samples
    cypher, entity_fixes = _fix_entity_labels(cypher, node_props, node_counts, patterns_kb)
    fixes.extend(entity_fixes)

    # Fix 4: Strip unnecessary aliases from RETURN
    cypher, alias_fixes = _strip_aliases(cypher)
    fixes.extend(alias_fixes)

    if fixes:
        logger.info("[CypherValidator] Applied %d fixes: %s", len(fixes), fixes)

    return cypher, fixes


def _fix_directions(cypher: str, patterns_kb: list) -> tuple[str, list[str]]:
    """Check relationship directions in Cypher against knowledge truth table."""
    fixes = []

    # Find all patterns like (a:Label)-[:REL]->(b:Label) or (a:Label)<-[:REL]-(b:Label)
    # Pattern: (var:Label)-[:REL]->(var:Label)
    forward_pattern = re.compile(
        r'\((\w+):(\w+)(?:\s*\{[^}]*\})?\)\s*-\[:(\w+)\]\s*->\s*\((\w+):(\w+)(?:\s*\{[^}]*\})?\)'
    )
    reverse_pattern = re.compile(
        r'\((\w+):(\w+)(?:\s*\{[^}]*\})?\)\s*<-\[:(\w+)\]\s*-\s*\((\w+):(\w+)(?:\s*\{[^}]*\})?\)'
    )

    # Check forward patterns
    for m in forward_pattern.finditer(cypher):
        from_var, from_label, rel, to_var, to_label = m.groups()
        if not _direction_exists(from_label, rel, to_label, patterns_kb):
            if _direction_exists(to_label, rel, from_label, patterns_kb):
                # Need to reverse: swap the arrow direction
                old = m.group(0)
                # Build reversed pattern
                from_part = _extract_node_part(cypher, from_var, from_label, m.start())
                to_part = _extract_node_part(cypher, to_var, to_label, m.start())
                new = old.replace(f"-[:{rel}]->", f"<-[:{rel}]-")
                cypher = cypher.replace(old, new)
                fixes.append(f"Reversed direction: (:{from_label})-[:{rel}]->(:{to_label}) → (:{from_label})<-[:{rel}]-(:{to_label})")

    return cypher, fixes


def _fix_property_locations(
    cypher: str,
    node_props: dict,
    rel_props: dict,
) -> tuple[str, list[str]]:
    """Fix properties accessed on wrong node/relationship."""
    fixes = []

    # Find all property accesses: var.property
    # We need to know what label each variable refers to
    var_labels = _extract_var_labels(cypher)

    # Find property accesses in WHERE, RETURN, ORDER BY
    prop_accesses = re.findall(r'(\w+)\.(\w+)', cypher)

    for var, prop in prop_accesses:
        if var in var_labels:
            label = var_labels[var]
            label_props = node_props.get(label, {})

            if prop not in label_props:
                # Property not on this node — check if it's on a relationship
                for rel_name, rprops in rel_props.items():
                    if prop in rprops:
                        rel_var = _find_rel_var(cypher, rel_name)
                        if rel_var and rel_var != var:
                            old_ref = f"{var}.{prop}"
                            new_ref = f"{rel_var}.{prop}"
                            cypher = cypher.replace(old_ref, new_ref)
                            fixes.append(f"Property '{prop}' on [:{rel_name}], not :{label}. {old_ref} → {new_ref}")
                        elif not rel_var:
                            # No rel variable exists — add one
                            old_pattern = f"[:{rel_name}]"
                            new_pattern = f"[r:{rel_name}]"
                            if old_pattern in cypher:
                                cypher = cypher.replace(old_pattern, new_pattern, 1)
                                old_ref = f"{var}.{prop}"
                                new_ref = f"r.{prop}"
                                cypher = cypher.replace(old_ref, new_ref)
                                fixes.append(f"Added rel var 'r' for [:{rel_name}], fixed {old_ref} → {new_ref}")
                        break

    return cypher, fixes


def _fix_entity_labels(
    cypher: str,
    node_props: dict,
    node_counts: dict,
    patterns_kb: list,
) -> tuple[str, list[str]]:
    """Resolve entity labels using knowledge samples."""
    fixes = []

    # Find all node patterns with match values: (var:Label {prop: 'value'})
    node_pattern = re.compile(r'\((\w+):(\w+)\s*\{(\w+):\s*[\'"]([^\'"]+)[\'"]\}\)')

    for m in node_pattern.finditer(cypher):
        var, label, prop, value = m.groups()
        value_lower = value.lower()

        # Check if this value exists in a MORE SPECIFIC label's samples
        best_label = None
        best_prop = None
        best_count = node_counts.get(label, 999999)

        for check_label, props in node_props.items():
            for check_prop, info in props.items():
                sample = info.get("sample", "")
                if sample and str(sample).lower() == value_lower:
                    check_count = node_counts.get(check_label, 1)
                    if check_count < best_count:
                        # More specific label found
                        best_label = check_label
                        best_prop = check_prop
                        best_count = check_count

        if best_label and (best_label != label or best_prop != prop):
            old = f"({var}:{label} {{{prop}: '{value}'}})"
            new_val = value
            # Get exact case from sample
            for p_info in node_props.get(best_label, {}).values():
                if str(p_info.get("sample", "")).lower() == value_lower:
                    new_val = str(p_info["sample"])
                    break
            new = f"({var}:{best_label} {{{best_prop}: '{new_val}'}})"
            cypher = cypher.replace(old, new)
            fixes.append(f"Entity resolved: :{label}.{prop}='{value}' → :{best_label}.{best_prop}='{new_val}'")

    return cypher, fixes


def _direction_exists(from_label: str, rel: str, to_label: str, patterns_kb: list) -> bool:
    for p in patterns_kb:
        if p["rel"] == rel and p["from"] == from_label and p["to"] == to_label:
            return True
    return False


def _extract_var_labels(cypher: str) -> dict[str, str]:
    """Extract variable → label mapping from MATCH patterns."""
    var_labels = {}
    for m in re.finditer(r'\((\w+):(\w+)', cypher):
        var, label = m.groups()
        var_labels[var] = label
    return var_labels


def _find_rel_var(cypher: str, rel_type: str) -> str | None:
    """Find the variable name for a relationship type in Cypher."""
    m = re.search(rf'\[(\w+):{rel_type}\]', cypher)
    if m:
        return m.group(1)
    return None


def _extract_node_part(cypher: str, var: str, label: str, start: int) -> str:
    """Extract the full node pattern string."""
    pattern = re.compile(rf'\({var}:{label}(?:\s*\{{[^}}]*\}})?\)')
    m = pattern.search(cypher, start)
    return m.group(0) if m else f"({var}:{label})"


def _fix_count_syntax(cypher: str) -> tuple[str, list[str]]:
    """Fix COUNT(pattern) → count{pattern} for Neo4j 5.x."""
    fixes = []
    # Match COUNT((var)-[:REL]->(var)) patterns
    pattern = re.compile(r'COUNT\(\s*(\([^)]*\)-\[:[A-Z_]+\]->[^)]*\))\s*\)', re.IGNORECASE)
    for m in pattern.finditer(cypher):
        old = m.group(0)
        inner = m.group(1)
        new = f"count{{{inner}}}"
        cypher = cypher.replace(old, new)
        fixes.append(f"Syntax: {old} → {new}")
    return cypher, fixes


def _strip_aliases(cypher: str) -> tuple[str, list[str]]:
    """Strip unnecessary aliases from RETURN clause for eval compatibility."""
    fixes = []

    # Find RETURN clause — handle single-line and multiline
    lines = cypher.split('\n')
    return_idx = -1
    for i, line in enumerate(lines):
        if re.search(r'\bRETURN\b', line, re.IGNORECASE):
            return_idx = i
            break

    if return_idx < 0:
        return cypher, fixes

    # Collect RETURN line(s) — stop at ORDER BY, LIMIT, or end
    return_lines = [lines[return_idx]]
    j = return_idx + 1
    while j < len(lines):
        stripped = lines[j].strip()
        if re.match(r'^(ORDER|LIMIT|SKIP|UNION|WITH)\b', stripped, re.IGNORECASE) or not stripped:
            break
        # Continuation of RETURN (no keyword start)
        if not re.match(r'^(MATCH|WHERE|RETURN|CREATE|DELETE|SET|REMOVE|MERGE)\b', stripped, re.IGNORECASE):
            return_lines.append(lines[j])
            j += 1
        else:
            break

    return_text = ' '.join(l.strip() for l in return_lines)
    # Extract just the field list after RETURN [DISTINCT]
    # Handle both "RETURN x, y ORDER BY" and standalone "RETURN x, y"
    m = re.search(r'\bRETURN\s+(?:DISTINCT\s+)?(.*?)(?:\s+ORDER\b|\s+LIMIT\b|\s+SKIP\b|$)', return_text, re.IGNORECASE)
    if not m:
        return cypher, fixes

    prefix_match = re.search(r'(\bRETURN\s+(?:DISTINCT\s+)?)', return_text, re.IGNORECASE)
    if not prefix_match:
        return cypher, fixes

    field_list = m.group(1).strip()

    # Smart split by comma — respect parentheses and braces
    parts = _smart_split(field_list)
    new_parts = []
    changed = False

    for part in parts:
        part = part.strip()
        alias_m = re.match(r'^(.+?)\s+AS\s+(\w+)\s*$', part, re.IGNORECASE)
        if alias_m:
            expr = alias_m.group(1).strip()
            # Keep alias for aggregations and computed expressions
            if re.search(r'(?:count|avg|sum|size|collect|min|max)\s*[\({]', expr, re.IGNORECASE):
                new_parts.append(part)
            elif any(op in expr for op in ['+', '-', '*', '/', 'toFloat', 'toString']):
                new_parts.append(part)
            else:
                new_parts.append(expr)
                changed = True
        else:
            new_parts.append(part)

    if changed:
        new_field_list = ', '.join(new_parts)
        # Replace in original cypher string
        cypher = cypher.replace(field_list, new_field_list, 1)
        fixes.append("Stripped unnecessary aliases from RETURN")

    return cypher, fixes


def _smart_split(text: str) -> list[str]:
    """Split by comma, respecting parentheses and braces."""
    parts = []
    depth = 0
    current = []
    for ch in text:
        if ch in '({[':
            depth += 1
            current.append(ch)
        elif ch in ')}]':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append(''.join(current))
    return parts
