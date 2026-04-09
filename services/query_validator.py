"""
Query Validator + Auto-Corrector — the "smart" part of the agent.

Uses knowledge base (extracted from DB) to:
  1. Validate plan against schema
  2. AUTO-FIX common errors WITHOUT calling LLM again
  3. Detect and resolve ambiguous entities
  4. Fix property location errors (node vs relationship)
  5. Fix relationship direction errors

This is where agent intelligence lives — in deterministic code, not in prompts.
"""

from __future__ import annotations

import logging
from services.query_plan import QueryPlan, PatternSpec, NodeSpec, WhereCondition, ReturnField

logger = logging.getLogger(__name__)


def validate_and_fix(
    plan: QueryPlan,
    knowledge: dict,
) -> tuple[QueryPlan, list[str]]:
    """
    Validate plan against knowledge base and auto-fix what we can.
    Returns (fixed_plan, list_of_fixes_applied).
    
    This is the core agent intelligence — deterministic corrections
    based on ground truth from the database.
    """
    fixes: list[str] = []
    schema_labels = set(knowledge.get("node_properties", {}).keys())
    schema_rels = set(knowledge.get("relationship_properties", {}).keys())

    # Fix 1: Correct wrong labels
    for pat in plan.patterns:
        for node in [pat.from_node, pat.to_node]:
            if node.label and node.label not in schema_labels:
                corrected = _find_closest_label(node.label, schema_labels)
                if corrected:
                    fixes.append(f"Label '{node.label}' → '{corrected}'")
                    node.label = corrected

    # Fix 2: Correct wrong relationships
    for pat in plan.patterns:
        if pat.rel and pat.rel not in schema_rels:
            corrected = _find_closest_rel(pat.rel, schema_rels)
            if corrected:
                fixes.append(f"Relationship '{pat.rel}' → '{corrected}'")
                pat.rel = corrected

    # Fix 3: Correct relationship directions using knowledge
    patterns_kb = knowledge.get("relationship_patterns", [])
    for pat in plan.patterns:
        if not pat.rel or not pat.from_node.label or not pat.to_node.label:
            continue
        fixed = _fix_direction(pat, patterns_kb)
        if fixed:
            fixes.append(fixed)

    # Fix 4: Resolve ambiguous entities
    ambiguous = knowledge.get("ambiguous_entities", [])
    for pat in plan.patterns:
        for node in [pat.from_node, pat.to_node]:
            fix = _resolve_ambiguous(node, pat, ambiguous, patterns_kb)
            if fix:
                fixes.append(fix)

    # Fix 5: Fix property locations (node vs relationship)
    confusion = knowledge.get("property_confusion", [])
    node_props = knowledge.get("node_properties", {})
    rel_props = knowledge.get("relationship_properties", {})
    
    # Fix WHERE conditions pointing to wrong location
    for cond in plan.where:
        fix = _fix_property_location_where(cond, plan, node_props, rel_props, confusion)
        if fix:
            fixes.append(fix)

    # Fix RETURN fields pointing to wrong location
    for ret in plan.returns:
        fix = _fix_property_location_return(ret, plan, node_props, rel_props, confusion)
        if fix:
            fixes.append(fix)

    # Fix 6: Fix match properties using unique constraints AND sample values
    constraints = knowledge.get("unique_constraints", [])
    samples = knowledge.get("sample_values", {})
    for pat in plan.patterns:
        for node in [pat.from_node, pat.to_node]:
            fix = _fix_match_property(node, constraints, node_props, samples)
            if fix:
                fixes.append(fix)

    # Fix 7: Entity resolution — match values against knowledge samples
    node_counts = knowledge.get("node_counts", {})
    for pat in plan.patterns:
        for node in [pat.from_node, pat.to_node]:
            fix = _resolve_entity_from_samples(node, pat, node_props, patterns_kb, node_counts)
            if fix:
                fixes.append(fix)

    # Fix 7: Ensure variable consistency
    var_fixes = _ensure_variable_consistency(plan)
    fixes.extend(var_fixes)

    if fixes:
        logger.info("[Validator] Applied %d auto-fixes: %s", len(fixes), fixes)
    else:
        logger.info("[Validator] Plan is valid, no fixes needed")

    return plan, fixes


def _find_closest_label(wrong: str, labels: set[str]) -> str | None:
    """Find closest matching label (case-insensitive, prefix match)."""
    w = wrong.lower()
    for l in labels:
        if l.lower() == w:
            return l
    for l in labels:
        if l.lower().startswith(w) or w.startswith(l.lower()):
            return l
    return None


def _find_closest_rel(wrong: str, rels: set[str]) -> str | None:
    """Find closest matching relationship."""
    w = wrong.lower().replace(" ", "_")
    for r in rels:
        if r.lower() == w:
            return r
    for r in rels:
        if w in r.lower() or r.lower() in w:
            return r
    return None


def _fix_direction(pat: PatternSpec, patterns_kb: list) -> str | None:
    """
    Fix relationship direction based on knowledge base truth table.
    
    The KB stores the CANONICAL direction: (A)-[:REL]->(B).
    The plan stores from_node, to_node, and direction ("->" or "<-").
    
    Effective direction:
      direction="->" means from_node -[:REL]-> to_node
      direction="<-" means from_node <-[:REL]- to_node (= to_node -[:REL]-> from_node)
    
    We need to check if the EFFECTIVE direction matches KB.
    """
    f_label = pat.from_node.label
    t_label = pat.to_node.label
    rel = pat.rel
    direction = pat.direction.strip().lower()

    # Compute effective source and target
    if direction in ("<-", "in", "incoming"):
        eff_source = t_label  # to_node is actually the source
        eff_target = f_label
    else:
        eff_source = f_label
        eff_target = t_label

    # Check if effective direction matches KB
    for p in patterns_kb:
        if p["rel"] == rel and p["from"] == eff_source and p["to"] == eff_target:
            return None  # Effective direction is correct

    # Check if REVERSE effective direction matches KB
    for p in patterns_kb:
        if p["rel"] == rel and p["from"] == eff_target and p["to"] == eff_source:
            # Need to flip: swap from/to nodes AND flip direction
            pat.from_node, pat.to_node = pat.to_node, pat.from_node
            # Keep direction the same — swapping nodes achieves the flip
            return f"Fixed direction: (:{eff_target})-[:{rel}]->(:{eff_source}) was wrong, swapped to (:{eff_source})-[:{rel}]->(:{eff_target})"

    return None


def _resolve_ambiguous(
    node: NodeSpec,
    pat: PatternSpec,
    ambiguous: list,
    patterns_kb: list,
) -> str | None:
    """Resolve ambiguous entity to correct label using relationship context."""
    if not node.match:
        return None

    for match_prop, match_val in node.match.items():
        val_lower = str(match_val).lower()
        for amb in ambiguous:
            if amb["property"] == match_prop and amb["value"].lower() == val_lower:
                # Entity is ambiguous — use relationship context to pick correct label
                possible_labels = amb["labels"]
                rel = pat.rel
                if not rel:
                    continue

                # Check which label works with this relationship
                for p in patterns_kb:
                    if p["rel"] == rel:
                        if node is pat.from_node and p["from"] in possible_labels:
                            if node.label != p["from"]:
                                old = node.label
                                node.label = p["from"]
                                return f"Resolved ambiguous '{match_val}': :{old} → :{p['from']} (based on [:{rel}])"
                        if node is pat.to_node and p["to"] in possible_labels:
                            if node.label != p["to"]:
                                old = node.label
                                node.label = p["to"]
                                return f"Resolved ambiguous '{match_val}': :{old} → :{p['to']} (based on [:{rel}])"
    return None


def _fix_property_location_where(
    cond: WhereCondition,
    plan: QueryPlan,
    node_props: dict,
    rel_props: dict,
    confusion: list,
) -> str | None:
    """Fix WHERE condition pointing to wrong property location."""
    prop = cond.prop
    var = cond.var

    # Find what label/rel this variable refers to
    var_label = _get_var_label(var, plan)
    var_rel = _get_var_rel(var, plan)

    if var_label:
        # Variable is a node — check if property actually exists on this node
        label_props = node_props.get(var_label, {})
        if prop not in label_props:
            # Property not on this node — check if it's on a relationship
            for rel_name, rprops in rel_props.items():
                if prop in rprops:
                    # Need to change var to point to relationship variable
                    rel_var = _find_rel_var_for_type(rel_name, plan)
                    if rel_var:
                        old_ref = f"{var}.{prop}"
                        cond.var = rel_var
                        return f"Property '{prop}' is on [:{rel_name}], not :{var_label}. Changed {old_ref} → {rel_var}.{prop}"
    return None


def _fix_property_location_return(
    ret: ReturnField,
    plan: QueryPlan,
    node_props: dict,
    rel_props: dict,
    confusion: list,
) -> str | None:
    """Fix RETURN field pointing to wrong property location."""
    if not ret.var or not ret.prop:
        return None

    var_label = _get_var_label(ret.var, plan)
    if var_label:
        label_props = node_props.get(var_label, {})
        if ret.prop not in label_props:
            for rel_name, rprops in rel_props.items():
                if ret.prop in rprops:
                    rel_var = _find_rel_var_for_type(rel_name, plan)
                    if rel_var:
                        old_ref = f"{ret.var}.{ret.prop}"
                        ret.var = rel_var
                        return f"Property '{ret.prop}' is on [:{rel_name}], not :{var_label}. Changed {old_ref} → {rel_var}.{ret.prop}"
    return None


def _fix_match_property(
    node: NodeSpec,
    constraints: list,
    node_props: dict,
    samples: dict,
) -> str | None:
    """
    Fix match property to use the correct UNIQUE property.
    
    Logic:
    1. If node has match values, check if the property is the UNIQUE one
    2. If not, AND the UNIQUE property exists, switch to it
    3. Use sample values to verify the value actually belongs to this label
    """
    if not node.match or not node.label:
        return None

    # Find unique constraint for this label
    unique_prop = None
    for c in constraints:
        if c["label"] == node.label:
            unique_prop = c["property"]
            break

    if not unique_prop:
        return None

    for match_prop, match_val in list(node.match.items()):
        if match_prop == unique_prop:
            continue  # Already using unique property

        # Check if value exists in sample_values for the UNIQUE property
        label_samples = samples.get(node.label, {})
        unique_samples = label_samples.get(unique_prop, [])
        
        val_str = str(match_val).lower()
        
        # If value matches a sample of the unique property → switch
        for sv in unique_samples:
            if sv.lower() == val_str:
                node.match = {unique_prop: sv}  # Use exact case from sample
                return f":{node.label} match '{match_prop}={match_val}' → '{unique_prop}={sv}' (UNIQUE + sample match)"

        # If current property exists but unique is different → switch anyway
        # (the value might just not be in our limited samples)
        label_props = node_props.get(node.label, {})
        if unique_prop in label_props:
            node.match = {unique_prop: match_val}
            return f":{node.label} match property '{match_prop}' → '{unique_prop}' (UNIQUE constraint)"

    return None


def _get_var_label(var: str, plan: QueryPlan) -> str | None:
    """Get the label for a variable from the plan."""
    for pat in plan.patterns:
        if pat.from_node.var == var:
            return pat.from_node.label
        if pat.to_node.var == var:
            return pat.to_node.label
    return None


def _get_var_rel(var: str, plan: QueryPlan) -> str | None:
    """Get the relationship type for a variable."""
    for pat in plan.patterns:
        if pat.rel_var == var:
            return pat.rel
    return None


def _find_rel_var_for_type(rel_type: str, plan: QueryPlan) -> str | None:
    """Find the relationship variable for a given type, or create one."""
    for pat in plan.patterns:
        if pat.rel == rel_type:
            if not pat.rel_var:
                pat.rel_var = "r"
            return pat.rel_var
    return None


def _resolve_entity_from_samples(
    node: NodeSpec,
    pat: PatternSpec,
    node_props: dict,
    patterns_kb: list,
    node_counts: dict = None,
) -> str | None:
    """
    Resolve entity to correct label by checking sample values in node_properties.
    
    This is the REAL intelligence: when question says 'neo4j', check ALL labels'
    property samples to find where this value actually lives.
    
    Example:
      node = User {name: 'neo4j'}
      node_props has Me.screen_name.sample = 'neo4j'
      → Fix: change label to Me, property to screen_name
    """
    if not node.match:
        return None

    for match_prop, match_val in list(node.match.items()):
        val_lower = str(match_val).lower()

        # Search ALL labels for this value in their property samples
        # Prefer labels with fewer nodes (more specific, e.g., Me over User)
        candidates: list[tuple[str, str, str, int]] = []
        nc = node_counts or {}

        for label, props in node_props.items():
            for prop_name, prop_info in props.items():
                sample = prop_info.get("sample", "")
                if sample and str(sample).lower() == val_lower:
                    if _label_fits_pattern(label, node, pat, patterns_kb):
                        # Labels not in node_counts are likely rare/specific (e.g., Me)
                        # Give them low count so they're preferred over generic labels
                        count = nc.get(label, 1)
                        candidates.append((label, prop_name, str(sample), count))

        # Sort by node count (prefer specific labels like Me over generic User)
        if candidates:
            candidates.sort(key=lambda x: x[3])
            best_match = (candidates[0][0], candidates[0][1], candidates[0][2])

        if best_match:
            new_label, new_prop, exact_val = best_match
            # Only change if the new label is actually different AND better
            # Don't "fix" if current label already matches a sample
            current_matches = False
            current_props = node_props.get(node.label, {})
            for cp_name, cp_info in current_props.items():
                if cp_info.get("sample", "").lower() == val_lower:
                    current_matches = True
                    break

            if current_matches:
                # Current label already has this value — don't change label
                # But maybe fix the property
                if new_label == node.label and new_prop != match_prop:
                    old = f"{match_prop}='{match_val}'"
                    node.match = {new_prop: exact_val}
                    return f":{node.label} match property {old} → {new_prop}='{exact_val}' (sample match)"
                return None

            if new_label != node.label or new_prop != match_prop:
                old = f":{node.label} {{{match_prop}: '{match_val}'}}"
                node.label = new_label
                node.match = {new_prop: exact_val}
                new = f":{new_label} {{{new_prop}: '{exact_val}'}}"
                return f"Entity resolved: {old} → {new} (sample match)"

    return None


def _label_fits_pattern(
    label: str,
    node: NodeSpec,
    pat: PatternSpec,
    patterns_kb: list,
) -> bool:
    """Check if a label works in the current pattern's relationship context."""
    if not pat.rel:
        return True  # No relationship constraint

    for p in patterns_kb:
        if p["rel"] == pat.rel:
            if node is pat.from_node and p["from"] == label:
                return True
            if node is pat.to_node and p["to"] == label:
                return True
            # Also check reverse (validator will fix direction later)
            if node is pat.from_node and p["to"] == label:
                return True
            if node is pat.to_node and p["from"] == label:
                return True

    return False


def _ensure_variable_consistency(plan: QueryPlan) -> list[str]:
    """
    Ensure variable consistency across patterns.
    
    Rules:
    1. If same label appears with different vars AND the question implies
       they should be the same entity → merge to one var
    2. If a node has no var → assign one
    
    Detection for "same entity": if two patterns share a common node
    (same label + same match properties), they refer to the same entity.
    """
    fixes = []
    
    # Build map: label → list of (var, match_dict, pattern_index, is_from)
    label_vars: dict[str, list[tuple[str, dict, int, bool]]] = {}
    for i, pat in enumerate(plan.patterns):
        for node, is_from in [(pat.from_node, True), (pat.to_node, False)]:
            if node.label:
                label_vars.setdefault(node.label, []).append(
                    (node.var, dict(node.match), i, is_from)
                )

    # For each label with multiple vars: check if they should be merged
    for label, entries in label_vars.items():
        if len(entries) <= 1:
            continue

        vars_used = set(e[0] for e in entries if e[0])
        if len(vars_used) <= 1:
            continue

        # Check if any entries share match properties (same entity)
        # OR if they have no match (generic — should be same var)
        groups: list[list[int]] = []  # groups of entry indices that should share var
        
        # Simple heuristic: if entries have same match dict OR both empty → same entity
        for i, (var_i, match_i, pat_i, from_i) in enumerate(entries):
            placed = False
            for group in groups:
                j = group[0]
                var_j, match_j, pat_j, from_j = entries[j]
                if match_i == match_j:  # same match = same entity
                    group.append(i)
                    placed = True
                    break
            if not placed:
                groups.append([i])

        # Merge vars within each group
        for group in groups:
            if len(group) <= 1:
                continue
            # Pick the first var as canonical
            canonical_var = None
            for idx in group:
                v = entries[idx][0]
                if v:
                    canonical_var = v
                    break
            if not canonical_var:
                canonical_var = label[0].lower()

            for idx in group:
                var, match, pat_idx, is_from = entries[idx]
                if var != canonical_var:
                    pat = plan.patterns[pat_idx]
                    node = pat.from_node if is_from else pat.to_node
                    old_var = node.var
                    node.var = canonical_var
                    if old_var:
                        fixes.append(f"Merged var '{old_var}' → '{canonical_var}' for :{label}")
                        # Also update any WHERE/RETURN referencing old var
                        for cond in plan.where:
                            if cond.var == old_var:
                                cond.var = canonical_var
                        for ret in plan.returns:
                            if ret.var == old_var:
                                ret.var = canonical_var

    # Assign vars to nodes without one
    var_counter = 0
    used_vars = set()
    for pat in plan.patterns:
        for node in [pat.from_node, pat.to_node]:
            if node.var:
                used_vars.add(node.var)

    for pat in plan.patterns:
        for node in [pat.from_node, pat.to_node]:
            if not node.var and node.label:
                # Find if this label already has a var
                for pat2 in plan.patterns:
                    for n2 in [pat2.from_node, pat2.to_node]:
                        if n2.label == node.label and n2.var:
                            node.var = n2.var
                            break
                    if node.var:
                        break
                if not node.var:
                    v = node.label[0].lower()
                    while v in used_vars:
                        var_counter += 1
                        v = chr(ord('a') + var_counter)
                    node.var = v
                    used_vars.add(v)

    return fixes
