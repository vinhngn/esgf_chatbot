"""
Schema Graph -- core algorithm module for SADP (Schema-Aware Dynamic Prompting).

Uses NetworkX to model the Neo4j schema as a directed graph, then applies
graph algorithms (BFS shortest path, degree centrality) to auto-generate
property location hints and Cypher query examples.

Classes
-------
SchemaGraph
    NetworkX DiGraph wrapper built from schema JSON.
PatternLibrary
    Auto-generates (question, cypher) example pairs from schema patterns.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from itertools import combinations
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lightweight graph implementation (no external dependency)
# ---------------------------------------------------------------------------


class SchemaGraph:
    """
    Directed graph representation of a Neo4j database schema.

    Nodes = node labels, edges = relationship types.
    Each node/edge carries property metadata.
    """

    def __init__(self) -> None:
        # node_label -> {properties: [...], count: int}
        self._nodes: dict[str, dict[str, Any]] = {}
        # (start, end) -> [{type: str, properties: [...]}]
        self._edges: defaultdict[tuple[str, str], list[dict]] = defaultdict(list)
        # property_name -> list[{location, owner, type_info}]
        self._property_map: dict[str, list[dict]] = {}

    # -- Construction -------------------------------------------------

    @classmethod
    def from_schema_json(cls, data: dict[str, Any]) -> "SchemaGraph":
        """Build a SchemaGraph from the output of schema_introspector.py."""
        g = cls()
        if not data:
            return g

        # --- Add nodes ---
        node_props = data.get("node_properties", {})
        node_counts = data.get("node_counts", {})
        for label in data.get("node_labels", []):
            # node_props may use compound keys like "ActorPerson"
            props = node_props.get(label, [])
            # also try compound keys that START with this label
            if not props:
                for key, val in node_props.items():
                    if label in key:
                        props = val
                        break
            g._nodes[label] = {
                "properties": props,
                "count": node_counts.get(label, 0),
            }

        # --- Add edges ---
        rel_props = data.get("relationship_properties", {})
        for pattern in data.get("schema_patterns", []):
            start = pattern["start"]
            end = pattern["end"]
            rel_type = pattern["relationship"]
            # Skip internal Neo4j relationships
            if rel_type.startswith("_Bloom"):
                continue
            g._edges[(start, end)].append({
                "type": rel_type,
                "properties": rel_props.get(rel_type, []),
            })

        # --- Build property location map ---
        g._build_property_map(data)

        logger.info(
            "[SchemaGraph] Built graph: %d nodes, %d edge-groups",
            len(g._nodes),
            len(g._edges),
        )
        return g

    def _build_property_map(self, data: dict) -> None:
        """Map every property name -> its location (NODE vs RELATIONSHIP)."""
        prop_map: dict[str, list[dict]] = defaultdict(list)

        # Node properties
        for label, props_list in data.get("node_properties", {}).items():
            # Normalize compound labels like "ActorDirectorPerson"
            clean_label = label
            for nl in data.get("node_labels", []):
                if nl in label:
                    clean_label = nl
                    break
            for p in props_list:
                prop_map[p["property"]].append({
                    "location": "NODE",
                    "owner": clean_label,
                    "types": p.get("types", []),
                    "mandatory": p.get("mandatory", False),
                })

        # Relationship properties
        for rel_type, props_list in data.get("relationship_properties", {}).items():
            if rel_type.startswith("_Bloom"):
                continue
            for p in props_list:
                prop_map[p["property"]].append({
                    "location": "RELATIONSHIP",
                    "owner": rel_type,
                    "types": p.get("types", []),
                    "mandatory": p.get("mandatory", False),
                })

        self._property_map = dict(prop_map)

    # -- Graph queries ------------------------------------------------

    @property
    def node_labels(self) -> list[str]:
        return [l for l in self._nodes if not l.startswith("_Bloom")]

    @property
    def edges(self) -> list[dict]:
        """Return flat list of {start, end, type, properties}."""
        result = []
        for (start, end), rels in self._edges.items():
            for r in rels:
                result.append({
                    "start": start,
                    "end": end,
                    "type": r["type"],
                    "properties": r["properties"],
                })
        return result

    def get_node_properties(self, label: str) -> list[dict]:
        node = self._nodes.get(label, {})
        return node.get("properties", [])

    def get_node_count(self, label: str) -> int:
        return self._nodes.get(label, {}).get("count", 0)

    def get_adjacency(self) -> dict[str, list[dict]]:
        """Return adjacency list: label -> [{target, rel_type, properties}]."""
        adj: dict[str, list[dict]] = defaultdict(list)
        for (start, end), rels in self._edges.items():
            for r in rels:
                adj[start].append({
                    "target": end,
                    "rel_type": r["type"],
                    "properties": r["properties"],
                })
        return dict(adj)

    def get_predecessors(self) -> dict[str, list[dict]]:
        """Return reverse adjacency: label -> [{source, rel_type}]."""
        preds: dict[str, list[dict]] = defaultdict(list)
        for (start, end), rels in self._edges.items():
            for r in rels:
                preds[end].append({"source": start, "rel_type": r["type"]})
        return dict(preds)

    # -- Degree centrality --------------------------------------------

    def degree_centrality(self) -> dict[str, float]:
        """
        Compute degree centrality for each node.
        centrality(v) = (in_degree + out_degree) / (|V| - 1)
        Higher = more connected = more important in schema.
        """
        n = len(self._nodes)
        if n <= 1:
            return {label: 1.0 for label in self._nodes}

        in_deg: dict[str, int] = defaultdict(int)
        out_deg: dict[str, int] = defaultdict(int)

        for (start, end), rels in self._edges.items():
            count = len(rels)
            out_deg[start] += count
            in_deg[end] += count

        return {
            label: (in_deg.get(label, 0) + out_deg.get(label, 0)) / (n - 1)
            for label in self._nodes
        }

    # -- BFS shortest path --------------------------------------------

    def bfs_shortest_path(self, start: str, end: str) -> list[dict] | None:
        """
        Find shortest path from *start* to *end* using BFS.

        Returns list of {node, rel_type, direction} or None if no path.
        Considers both forward and reverse edges (undirected search).
        """
        if start == end:
            return [{"node": start}]

        if start not in self._nodes or end not in self._nodes:
            return None

        # Build undirected adjacency
        adj: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for (s, e), rels in self._edges.items():
            for r in rels:
                adj[s].append((e, r["type"], "FORWARD"))
                adj[e].append((s, r["type"], "REVERSE"))

        # BFS
        visited = {start}
        queue = [(start, [{"node": start}])]

        while queue:
            current, path = queue.pop(0)
            for neighbor, rel_type, direction in adj.get(current, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                new_path = path + [{
                    "node": neighbor,
                    "rel_type": rel_type,
                    "direction": direction,
                }]
                if neighbor == end:
                    return new_path
                queue.append((neighbor, new_path))

        return None

    def find_all_multi_hop_paths(self, max_hops: int = 3) -> list[list[dict]]:
        """Find all multi-hop paths (length 2+) between any two labels."""
        paths = []
        labels = self.node_labels
        for a, c in combinations(labels, 2):
            path = self.bfs_shortest_path(a, c)
            if path and 2 < len(path) <= max_hops + 1:
                paths.append(path)
            # Also try reverse
            path_rev = self.bfs_shortest_path(c, a)
            if path_rev and 2 < len(path_rev) <= max_hops + 1:
                paths.append(path_rev)
        return paths

    # -- Property location analysis -----------------------------------

    def get_property_location_map(self) -> dict[str, list[dict]]:
        """Return the full property -> location map."""
        return self._property_map

    def get_relationship_property_warnings(self) -> list[str]:
        """
        Generate warning strings for properties that live on RELATIONSHIPS.
        These are the #1 source of errors in Text-to-Cypher.
        """
        warnings = []
        seen = set()
        for prop_name, locations in self._property_map.items():
            for loc in locations:
                if loc["location"] == "RELATIONSHIP":
                    key = (prop_name, loc["owner"])
                    if key not in seen:
                        seen.add(key)
                        type_str = ", ".join(loc.get("types", ["Unknown"]))
                        warnings.append(
                            f"CRITICAL: Property '{prop_name}' ({type_str}) "
                            f"is on RELATIONSHIP [:{loc['owner']}], "
                            f"NOT on a node! Access via r.{prop_name} "
                            f"where r is the relationship variable."
                        )
        return warnings

    # -- Same-node pattern detection ----------------------------------

    def find_same_node_patterns(self) -> list[dict]:
        """
        Find patterns where the same node type has 2+ outgoing rels
        to the same target -- enables "person who both X and Y" queries.
        """
        adj = self.get_adjacency()
        patterns = []
        for source, targets in adj.items():
            # Group by target label
            target_groups: dict[str, list[str]] = defaultdict(list)
            for t in targets:
                target_groups[t["target"]].append(t["rel_type"])

            for target, rels in target_groups.items():
                if len(rels) >= 2:
                    for r1, r2 in combinations(rels, 2):
                        patterns.append({
                            "source": source,
                            "target": target,
                            "rel1": r1,
                            "rel2": r2,
                        })
        return patterns

    # -- Searchable property detection --------------------------------

    def get_primary_search_property(self, label: str) -> str | None:
        """
        Determine the best property to use for searching/matching a node.
        Priority: name > title > screen_name > productName > first mandatory String.
        """
        props = self.get_node_properties(label)
        if not props:
            return None

        priority = ["name", "title", "screen_name", "productName",
                     "categoryName", "companyName", "contactName"]
        for pname in priority:
            if any(p["property"] == pname for p in props):
                return pname

        # Fallback: first mandatory String property
        for p in props:
            if p.get("mandatory") and "String" in p.get("types", []):
                return p["property"]

        # Last resort: first String property
        for p in props:
            if "String" in p.get("types", []):
                return p["property"]

        return props[0]["property"] if props else None


# ---------------------------------------------------------------------------
# Pattern Library -- auto-generates few-shot examples
# ---------------------------------------------------------------------------


class PatternLibrary:
    """
    Generates (question, cypher) example pairs from a SchemaGraph.

    Key improvements:
    - Deduplicates edges: picks MOST SPECIFIC label per relationship type
      (Actor for ACTED_IN, Director for DIRECTED, User for RATED)
    - Ensures EVERY unique rel type has at least one example
    - Fixes MultiHop to use semantically correct relationships
    """

    def __init__(self, graph: SchemaGraph, sample_data: dict | None = None):
        self.graph = graph
        self.sample_data = sample_data or {}
        self._canonical_edges = self._build_canonical_edges()

    def _build_canonical_edges(self) -> list[dict]:
        """
        Deduplicate edges: per (rel_type, end) pair, pick
        the most specific source label (Actor > Person).
        """
        groups: dict[tuple[str, str], list[dict]] = {}
        for edge in self.graph.edges:
            if edge["type"].startswith("_Bloom"):
                continue
            key = (edge["type"], edge["end"])
            if key not in groups:
                groups[key] = []
            groups[key].append(edge)

        canonical = []
        for (rel_type, target), edges in groups.items():
            non_person = [e for e in edges if e["start"] != "Person"]
            if non_person:
                best = max(
                    non_person,
                    key=lambda e: self.graph.get_node_count(e["start"])
                )
            else:
                best = edges[0]
            canonical.append(best)
        return canonical

    def _get_sample(self, label: str, prop: str) -> str | None:
        samples = self.sample_data.get(label, [])
        for s in samples:
            val = s.get(prop)
            if val is not None and val != "" and str(val) != "NULL":
                return str(val)
        return None

    def _esc(self, s: str) -> str:
        return s.replace("{", "{{").replace("}", "}}")

    def generate_all(self, max_per_type: int = 2) -> list[tuple[str, str]]:
        examples: list[tuple[str, str]] = []
        # Per-type limits: traversal/relProperty need more to cover
        # all relationship types (ACTED_IN, DIRECTED, RATED, IN_GENRE)
        type_limits = {
            "Lookup": 2,
            "Traversal": 4,       # 1 per canonical edge
            "Aggregation": 2,
            "AvgAgg": 2,          # WITH + AVG pattern (9% of gold)
            "Filter": 2,
            "ContainsFilter": 1,  # CONTAINS string (5.4% of gold)
            "RelProperty": 4,     # 1 per rel property
            "ListProp": 2,        # UNWIND for list props (2.7% of gold)
            "MultiHop": 2,
            "SamePerson": 1,
        }
        generators = [
            ("Lookup", self._gen_lookup),
            ("Traversal", self._gen_traversal),
            ("Aggregation", self._gen_aggregation),
            ("AvgAgg", self._gen_avg_aggregation),
            ("Filter", self._gen_filter),
            ("ContainsFilter", self._gen_contains_filter),
            ("RelProperty", self._gen_rel_property),
            ("ListProp", self._gen_list_property),
            ("MultiHop", self._gen_multi_hop),
            ("SamePerson", self._gen_same_person),
        ]
        for ptype, gen_fn in generators:
            items = gen_fn()
            limit = type_limits.get(ptype, max_per_type)
            for q, c in items[:limit]:
                examples.append((f"[{ptype}] {q}", c))

        logger.info(
            "[PatternLibrary] Generated %d examples across %d types",
            len(examples), len(generators),
        )
        return examples

    # -- generators ---------------------------------------------------

    def _gen_lookup(self) -> list[tuple[str, str]]:
        results = []
        centrality = self.graph.degree_centrality()
        top = sorted(centrality, key=centrality.get, reverse=True)
        for label in top[:2]:
            if label.startswith("_Bloom"):
                continue
            sp = self.graph.get_primary_search_property(label)
            if sp:
                sv = self._get_sample(label, sp)
                if sv:
                    results.append((
                        f"Find the {label} with {sp} '{sv}'.",
                        f"MATCH (n:{label} {self._esc('{')}{sp}: "
                        f"'{sv}'{self._esc('}')}) RETURN n"
                    ))
            results.append((
                f"List all {label} nodes.",
                f"MATCH (n:{label}) RETURN n LIMIT 50"
            ))
        return results

    def _gen_traversal(self) -> list[tuple[str, str]]:
        """
        One-hop traversal -- breadth-first coverage.
        First: one SIMPLE example per canonical edge (all rel types covered).
        Then: filtered variants if limit allows.
        """
        simple = []
        filtered = []
        for edge in self._canonical_edges:
            s, e, r = edge["start"], edge["end"], edge["type"]
            # Simple traversal for EVERY edge
            simple.append((
                f"Find all {s} nodes connected to {e} via {r}.",
                f"MATCH (a:{s})-[r:{r}]->(b:{e}) RETURN a, b LIMIT 50"
            ))
            # Filtered variant
            sp = self.graph.get_primary_search_property(e)
            if sp:
                sv = self._get_sample(e, sp)
                if sv:
                    filtered.append((
                        f"Which {s} nodes are related to {e} '{sv}'?",
                        f"MATCH (a:{s})-[:{r}]->(b:{e} "
                        f"{self._esc('{')}{sp}: '{sv}'{self._esc('}')}) "
                        f"RETURN a LIMIT 50"
                    ))
        # Breadth first: simple examples first, then filtered
        return simple + filtered

    def _gen_aggregation(self) -> list[tuple[str, str]]:
        """COUNT per canonical edge."""
        results = []
        for edge in self._canonical_edges:
            s, e, r = edge["start"], edge["end"], edge["type"]
            sp = self.graph.get_primary_search_property(e)
            ret = f"b.{sp}" if sp else "b"
            results.append((
                f"How many {s} nodes are connected to each {e} via {r}?",
                f"MATCH (a:{s})-[:{r}]->(b:{e}) RETURN {ret}, "
                f"COUNT(a) AS count ORDER BY count DESC LIMIT 10"
            ))
        return results

    def _gen_filter(self) -> list[tuple[str, str]]:
        """Numeric property filter."""
        results = []
        for label in self.graph.node_labels:
            if label.startswith("_Bloom"):
                continue
            for p in self.graph.get_node_properties(label):
                pn = p["property"]
                types = p.get("types", [])
                if any(t in types for t in ["Long", "Double", "Integer", "Float"]):
                    sv = self._get_sample(label, pn)
                    if sv:
                        try:
                            num = float(sv)
                            vs = str(int(num)) if num == int(num) else str(num)
                        except (ValueError, OverflowError):
                            continue
                        results.append((
                            f"Find {label} nodes where {pn} is greater than {vs}.",
                            f"MATCH (n:{label}) WHERE n.{pn} > {vs} RETURN n LIMIT 50"
                        ))
                        if len(results) >= 3:
                            return results
        return results

    def _gen_rel_property(self) -> list[tuple[str, str]]:
        """Relationship property access -- the #1 error source."""
        results = []
        for edge in self._canonical_edges:
            rps = edge.get("properties", [])
            if not rps:
                continue
            s, e, r = edge["start"], edge["end"], edge["type"]
            for rp in rps:
                pn = rp["property"]
                esp = self.graph.get_primary_search_property(e)
                esv = self._get_sample(e, esp) if esp else None
                if esv:
                    results.append((
                        f"What is the {pn} of the {r} relationship "
                        f"between {s} and {e} '{esv}'?",
                        f"MATCH (a:{s})-[r:{r}]->(b:{e} "
                        f"{self._esc('{')}{esp}: '{esv}'{self._esc('}')}) "
                        f"RETURN a, r.{pn}"
                    ))
                else:
                    results.append((
                        f"Show {pn} from [:{r}] relationships "
                        f"between {s} and {e}.",
                        f"MATCH (a:{s})-[r:{r}]->(b:{e}) "
                        f"RETURN a, r.{pn}, b LIMIT 50"
                    ))
        return results

    def _gen_multi_hop(self) -> list[tuple[str, str]]:
        """Multi-hop with corrected relationship types."""
        results = []
        paths = self.graph.find_all_multi_hop_paths(max_hops=3)
        seen = set()

        for path in paths:
            if len(path) < 3:
                continue
            sl, el = path[0]["node"], path[-1]["node"]
            if any(n["node"].startswith("_Bloom") for n in path):
                continue
            key = (sl, el)
            if key in seen:
                continue
            seen.add(key)

            parts = []
            nvars = []
            ok = True
            for i, step in enumerate(path):
                v = chr(97 + i)
                nvars.append(v)
                if i == 0:
                    parts.append(f"({v}:{step['node']})")
                else:
                    prev = path[i - 1]["node"]
                    curr = step["node"]
                    # Look up canonical edge
                    rel = None
                    d = "FORWARD"
                    for ce in self._canonical_edges:
                        if ce["start"] == prev and ce["end"] == curr:
                            rel = ce["type"]; d = "FORWARD"; break
                        if ce["start"] == curr and ce["end"] == prev:
                            rel = ce["type"]; d = "REVERSE"; break
                    if not rel:
                        # Fallback to any edge
                        for e in self.graph.edges:
                            if e["start"] == prev and e["end"] == curr:
                                rel = e["type"]; d = "FORWARD"; break
                            if e["start"] == curr and e["end"] == prev:
                                rel = e["type"]; d = "REVERSE"; break
                    if not rel:
                        ok = False; break
                    if d == "FORWARD":
                        parts.append(f"-[:{rel}]->({v}:{curr})")
                    else:
                        parts.append(f"<-[:{rel}]-({v}:{curr})")

            if not ok:
                continue
            mid = [p["node"] for p in path[1:-1]]
            results.append((
                f"Find {sl} nodes connected to {el} through {', '.join(mid)}.",
                f"MATCH {''.join(parts)} RETURN {nvars[0]}, {nvars[-1]} LIMIT 50"
            ))
            if len(results) >= 4:
                break
        return results

    def _gen_same_person(self) -> list[tuple[str, str]]:
        """Same node with 2 different relationships to same target."""
        results = []
        patterns = self.graph.find_same_node_patterns()
        seen_targets = set()
        for p in patterns:
            src, tgt = p["source"], p["target"]
            r1, r2 = p["rel1"], p["rel2"]
            # Skip Person if Actor/Director has same pattern
            if src == "Person":
                has_specific = any(
                    pp["source"] != "Person"
                    and pp["target"] == tgt
                    and {pp["rel1"], pp["rel2"]} == {r1, r2}
                    for pp in patterns
                )
                if has_specific:
                    continue
            key = (tgt, frozenset([r1, r2]))
            if key in seen_targets:
                continue
            seen_targets.add(key)
            results.append((
                f"Which {src} nodes have both {r1} and {r2} the same {tgt}?",
                f"MATCH (p:{src})-[:{r1}]->(t:{tgt})<-[:{r2}]-(p) "
                f"RETURN DISTINCT p LIMIT 50"
            ))
        return results

    def _gen_avg_aggregation(self) -> list[tuple[str, str]]:
        """WITH + AVG pattern -- covers 9% of gold cyphers."""
        results = []
        for edge in self._canonical_edges:
            s, e, r = edge["start"], edge["end"], edge["type"]
            # Look for numeric properties on the end node
            for p in self.graph.get_node_properties(e):
                pn = p["property"]
                types = p.get("types", [])
                if any(t in types for t in ["Long", "Double", "Integer", "Float"]):
                    results.append((
                        f"What is the average {pn} of {e} per {s} via {r}?",
                        f"MATCH (a:{s})-[:{r}]->(b:{e}) "
                        f"WHERE b.{pn} IS NOT NULL "
                        f"WITH a, AVG(b.{pn}) AS avg{pn.capitalize()} "
                        f"ORDER BY avg{pn.capitalize()} DESC LIMIT 10 "
                        f"RETURN a, avg{pn.capitalize()}"
                    ))
                    if len(results) >= 3:
                        return results
            # Also check rel properties (e.g. AVG(r.rating))
            for rp in edge.get("properties", []):
                rpn = rp["property"]
                results.append((
                    f"What is the average {rpn} from {r} relationships per {e}?",
                    f"MATCH (a:{s})-[r:{r}]->(b:{e}) "
                    f"WITH b, AVG(r.{rpn}) AS avg{rpn.capitalize()} "
                    f"ORDER BY avg{rpn.capitalize()} DESC LIMIT 10 "
                    f"RETURN b, avg{rpn.capitalize()}"
                ))
                if len(results) >= 3:
                    return results
        return results

    def _gen_list_property(self) -> list[tuple[str, str]]:
        """
        UNWIND for list properties -- covers 2.7% of gold cyphers.
        Detects list properties (StringArray, etc.) and generates
        UNWIND + COUNT patterns.
        """
        results = []
        for label in self.graph.node_labels:
            if label.startswith("_Bloom"):
                continue
            for p in self.graph.get_node_properties(label):
                pn = p["property"]
                types = p.get("types", [])
                # Detect list properties
                is_list = any(
                    'Array' in t or 'List' in t
                    for t in types
                )
                # Also check known list property names
                if not is_list and pn in (
                    'countries', 'languages', 'genres', 'tags',
                    'categories', 'labels', 'keywords'
                ):
                    is_list = True
                if not is_list:
                    # Check sample data for lists
                    sv = self._get_sample(label, pn)
                    if sv and sv.startswith('['):
                        is_list = True

                if is_list:
                    results.append((
                        f"Which {pn} has the most {label} nodes?",
                        f"MATCH (n:{label}) UNWIND n.{pn} AS item "
                        f"WITH item, COUNT(DISTINCT n) AS cnt "
                        f"ORDER BY cnt DESC LIMIT 10 "
                        f"RETURN item, cnt"
                    ))
                    if len(results) >= 3:
                        return results
        return results

    def _gen_contains_filter(self) -> list[tuple[str, str]]:
        """CONTAINS string matching -- covers 5.4% of gold cyphers."""
        results = []
        for label in self.graph.node_labels:
            if label.startswith("_Bloom"):
                continue
            sp = self.graph.get_primary_search_property(label)
            if not sp:
                continue
            sv = self._get_sample(label, sp)
            if sv and len(sv) > 3:
                # Use a substring of sample value
                fragment = sv[:min(6, len(sv))]
                results.append((
                    f"Find {label} nodes whose {sp} contains '{fragment}'.",
                    f"MATCH (n:{label}) WHERE n.{sp} CONTAINS '{fragment}' "
                    f"RETURN n LIMIT 50"
                ))
                if len(results) >= 2:
                    return results
        return results


# ---------------------------------------------------------------------------
# Convenience -- build examples for a database in one call
# ---------------------------------------------------------------------------


def build_examples_for_db(schema_data: dict) -> list[tuple[str, str]]:
    """
    Build all (question, cypher) examples for a database from schema JSON.
    High-level API used by cypher_templates.py.
    """
    graph = SchemaGraph.from_schema_json(schema_data)
    sample_data = schema_data.get("sample_data", {})
    library = PatternLibrary(graph, sample_data)
    return library.generate_all(max_per_type=2)


def build_property_warnings(schema_data: dict) -> list[str]:
    """
    Build relationship property warnings for a database.
    High-level API used by cypher_templates.py.
    """
    graph = SchemaGraph.from_schema_json(schema_data)
    return graph.get_relationship_property_warnings()


def build_schema_graph(schema_data: dict) -> SchemaGraph:
    """Build and return a SchemaGraph from schema JSON."""
    return SchemaGraph.from_schema_json(schema_data)

