"""
Universal schema parser for ANY Neo4j database.
Parses runtime schema text into structured data for grounding.
No domain-specific hardcode.
"""
from __future__ import annotations
import re
from collections import defaultdict
from dataclasses import dataclass, field

@dataclass
class ParsedNode:
    label: str
    properties: dict[str, str] = field(default_factory=dict)

@dataclass
class ParsedRelationship:
    rel_type: str
    start_labels: list[str] = field(default_factory=list)
    end_labels: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)

@dataclass
class ParsedPath:
    start: str
    rel_type: str
    end: str
    direction: str = "outgoing"

@dataclass
class SchemaGraph:
    nodes: dict[str, ParsedNode] = field(default_factory=dict)
    relationships: dict[str, ParsedRelationship] = field(default_factory=dict)
    paths: list[ParsedPath] = field(default_factory=list)
    raw_schema: str = ""

    def get_all_labels(self) -> list[str]:
        return sorted(self.nodes.keys())

    def get_all_rel_types(self) -> list[str]:
        return sorted(self.relationships.keys())

    def get_node_properties(self, label: str) -> dict[str, str]:
        node = self.nodes.get(label)
        return node.properties if node else {}

    def get_rel_properties(self, rel_type: str) -> dict[str, str]:
        rel = self.relationships.get(rel_type)
        return rel.properties if rel else {}

    def get_paths_for_label(self, label: str) -> list[ParsedPath]:
        return [p for p in self.paths if p.start == label or p.end == label]

    def get_connected_labels(self, label: str, max_hops: int = 2) -> list[list[ParsedPath]]:
        visited_paths: list[list[ParsedPath]] = []
        queue: list[tuple[str, list[ParsedPath]]] = [(label, [])]
        seen = {label}
        while queue:
            current, path_so_far = queue.pop(0)
            if len(path_so_far) >= max_hops:
                continue
            for p in self.get_paths_for_label(current):
                neighbor = p.end if p.start == current else p.start
                new_path = path_so_far + [p]
                visited_paths.append(new_path)
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, new_path))
        return visited_paths

    def find_shortest_path(self, label_a: str, label_b: str, max_hops: int = 3) -> list[ParsedPath] | None:
        if label_a == label_b:
            return []
        queue: list[tuple[str, list[ParsedPath]]] = [(label_a, [])]
        seen = {label_a}
        while queue:
            current, path_so_far = queue.pop(0)
            if len(path_so_far) >= max_hops:
                continue
            for p in self.get_paths_for_label(current):
                neighbor = p.end if p.start == current else p.start
                new_path = path_so_far + [p]
                if neighbor == label_b:
                    return new_path
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, new_path))
        return None


def _extract_labels(s: str) -> list[str]:
    s = re.sub(r"\{[^}]*\}", "", s)
    labels = re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", s)
    if not labels:
        bare = s.strip()
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", bare):
            labels = [bare]
    return labels


def _extract_rel_types(s: str) -> list[str]:
    types = re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", s)
    if not types and s.strip():
        bare = s.strip()
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", bare):
            types = [bare]
    return types


def _parse_props_from_braces(props_str: str) -> dict[str, str]:
    """Parse 'prop1: TYPE1, prop2: TYPE2' by splitting on commas first."""
    result = {}
    if not props_str or not props_str.strip():
        return result
    # Split by comma, then parse each "name: type" pair
    for segment in re.split(r",\s*", props_str.strip()):
        segment = segment.strip()
        if not segment:
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)", segment)
        if m:
            prop_name = m.group(1).strip()
            prop_type = m.group(2).strip()
            result[prop_name] = prop_type
    return result


def parse_schema_text(schema_text: str) -> SchemaGraph:
    """Parse Neo4j schema text into SchemaGraph. Works with ANY database."""
    graph = SchemaGraph(raw_schema=schema_text)
    if not schema_text:
        return graph

    _parse_node_properties(schema_text, graph)
    _parse_relationship_properties(schema_text, graph)
    _parse_patterns(schema_text, graph)

    return graph


def _parse_node_properties(text: str, graph: SchemaGraph) -> None:
    node_section = re.search(
        r"Node properties:\s*\n(.*?)(?:Relationship|The relationships|$)",
        text, re.DOTALL | re.IGNORECASE,
    )
    if node_section:
        for line in node_section.group(1).splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(
                r"([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*)?\{([^}]*)\}",
                line,
            )
            if m:
                label = m.group(1)
                node = graph.nodes.setdefault(label, ParsedNode(label=label))
                node.properties.update(_parse_props_from_braces(m.group(2)))

    # Also parse (:Label {prop: TYPE}) inline patterns
    for m in re.finditer(r"\(:([A-Za-z_][A-Za-z0-9_]*)\s*\{([^}]*)\}", text):
        label = m.group(1)
        node = graph.nodes.setdefault(label, ParsedNode(label=label))
        node.properties.update(_parse_props_from_braces(m.group(2)))


def _parse_relationship_properties(text: str, graph: SchemaGraph) -> None:
    rel_section = re.search(
        r"Relationship properties:\s*\n(.*?)(?:The relationships|$)",
        text, re.DOTALL | re.IGNORECASE,
    )
    if rel_section:
        for line in rel_section.group(1).splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(
                r"([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*)?\{([^}]*)\}",
                line,
            )
            if m:
                rel_type = m.group(1)
                rel = graph.relationships.setdefault(rel_type, ParsedRelationship(rel_type=rel_type))
                rel.properties.update(_parse_props_from_braces(m.group(2)))


def _parse_patterns(text: str, graph: SchemaGraph) -> None:
    # Outgoing: (A)-[:R]->(B)
    for m in re.finditer(r"\(([^)]+)\)-\[([^\]]*)\]->\(([^)]+)\)", text):
        _add_path(m.group(1), m.group(2), m.group(3), graph, "outgoing")
    # Incoming: (A)<-[:R]-(B)
    for m in re.finditer(r"\(([^)]+)\)<-\[([^\]]*)\]-\(([^)]+)\)", text):
        _add_path(m.group(3), m.group(2), m.group(1), graph, "outgoing")


def _add_path(start_s: str, rel_s: str, end_s: str, graph: SchemaGraph, direction: str) -> None:
    start_labels = _extract_labels(start_s)
    end_labels = _extract_labels(end_s)
    rel_types = _extract_rel_types(rel_s)
    for sl in start_labels:
        graph.nodes.setdefault(sl, ParsedNode(label=sl))
        for el in end_labels:
            graph.nodes.setdefault(el, ParsedNode(label=el))
            for rt in rel_types:
                rel = graph.relationships.setdefault(rt, ParsedRelationship(rel_type=rt))
                if sl not in rel.start_labels:
                    rel.start_labels.append(sl)
                if el not in rel.end_labels:
                    rel.end_labels.append(el)
                path = ParsedPath(start=sl, rel_type=rt, end=el, direction=direction)
                if path not in graph.paths:
                    graph.paths.append(path)
