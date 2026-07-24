from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

from services.profile_analyzer.cypher_shape import parse_cypher_shape, summarize_shapes
from services.profile_analyzer.question_profile import profile_question, summarize_questions

METRIC_HINTS = {
    "age",
    "answers",
    "comments",
    "count",
    "createdat",
    "favorites",
    "followers",
    "following",
    "reputation",
    "score",
    "statuses",
    "upvotes",
    "views",
}
IDENTITY_HINTS = {"id", "accountid", "userid", "name", "screenname", "title"}
TEXT_HINTS = {"body", "description", "location", "name", "plot", "tagline", "text", "title"}
DATE_HINTS = {"createdat", "date", "released", "updatedat", "year"}


def _quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _is_internal_schema_name(name: str) -> bool:
    return name.startswith("_")


def _first_value(rows: list[dict], key: str, default: Any = None) -> Any:
    if not rows:
        return default
    return rows[0].get(key, default)


def _prop_key(prop: str) -> str:
    return "".join(ch for ch in prop.lower() if ch.isalnum())


def _property_role(prop: str) -> str:
    key = _prop_key(prop)
    if key in METRIC_HINTS or key.endswith(("count", "score", "rating", "votes")):
        return "metric"
    if key in DATE_HINTS or key.endswith(("date", "at")):
        return "temporal"
    if key in IDENTITY_HINTS or key.endswith("id"):
        return "identity"
    if key in TEXT_HINTS or "name" in key or "title" in key:
        return "text"
    return "attribute"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def _query(session: Any, cypher: str, **params: Any) -> list[dict]:
    return [record.data() for record in session.run(cypher, **params)]


def _schema_visualization(session: Any) -> tuple[dict[str, list[str]], list[dict]]:
    try:
        row = session.run("CALL db.schema.visualization()").single()
    except Exception:
        return {}, []
    if not row:
        return {}, []

    label_props: dict[str, set[str]] = defaultdict(set)
    for node in row.get("nodes") or []:
        label = str(node.get("name") or "")
        if not label or _is_internal_schema_name(label):
            continue
        for prop in node.get("indexes") or []:
            label_props[label].add(str(prop))

    patterns: list[dict] = []
    for rel in row.get("relationships") or []:
        nodes = list(getattr(rel, "nodes", []) or [])
        if len(nodes) != 2:
            continue
        from_label = str(nodes[0].get("name") or "")
        to_label = str(nodes[1].get("name") or "")
        rel_type = str(getattr(rel, "type", "") or rel.get("name") or "")
        if (
            from_label
            and to_label
            and rel_type
            and not _is_internal_schema_name(from_label)
            and not _is_internal_schema_name(to_label)
            and not _is_internal_schema_name(rel_type)
        ):
            patterns.append(
                {
                    "type": rel_type,
                    "from": from_label,
                    "to": to_label,
                    "count": None,
                }
            )
    return {label: sorted(props) for label, props in label_props.items()}, patterns


def _sample_node_properties(session: Any, label: str, sample_limit: int) -> list[str]:
    limit_clause = "LIMIT $sample_limit" if sample_limit > 0 else ""
    params = {"sample_limit": sample_limit} if sample_limit > 0 else {}
    rows = _query(
        session,
        f"""
        MATCH (n:{_quote_ident(label)})
        WITH n
        {limit_clause}
        UNWIND keys(n) AS property
        RETURN property, count(*) AS seen
        ORDER BY seen DESC, property
        LIMIT 30
        """,
        **params,
    )
    return [str(row["property"]) for row in rows if row.get("property")]


def _node_property_index(
    session: Any, labels: list[str], sample_limit: int
) -> dict[str, list[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for label in labels:
        try:
            props = _sample_node_properties(session, label, sample_limit)
        except Exception:
            continue
        index[label].update(props)
    return {label: sorted(props) for label, props in index.items()}


def _sample_relationship_properties(session: Any, rel_type: str, sample_limit: int) -> list[str]:
    limit_clause = "LIMIT $sample_limit" if sample_limit > 0 else ""
    params = {"sample_limit": sample_limit} if sample_limit > 0 else {}
    rows = _query(
        session,
        f"""
        MATCH ()-[r:{_quote_ident(rel_type)}]->()
        WITH r
        {limit_clause}
        UNWIND keys(r) AS property
        RETURN property, count(*) AS seen
        ORDER BY seen DESC, property
        LIMIT 20
        """,
        **params,
    )
    return [str(row["property"]) for row in rows if row.get("property")]


def _collect_labels(
    session: Any, sample_limit: int, visual_label_props: dict[str, list[str]]
) -> list[dict]:
    rows = _query(session, "CALL db.labels() YIELD label RETURN label ORDER BY label")
    label_names = [
        str(row["label"]) for row in rows if not _is_internal_schema_name(str(row["label"]))
    ]
    property_index = visual_label_props or _node_property_index(session, label_names, sample_limit)
    labels: list[dict] = []
    for label in label_names:
        count = _first_value(
            _query(session, f"MATCH (n:{_quote_ident(label)}) RETURN count(n) AS count"),
            "count",
            0,
        )
        properties = property_index.get(label, [])[:30]
        labels.append(
            {
                "label": label,
                "count": int(count or 0),
                "properties": [
                    {"name": prop, "role": _property_role(prop), "non_null": None}
                    for prop in properties
                ],
            }
        )
    return labels


def _collect_relationships(
    session: Any, sample_limit: int, visual_patterns: list[dict]
) -> list[dict]:
    rel_types = _query(
        session,
        "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType ORDER BY relationshipType",
    )
    relationships: list[dict] = []
    for row in rel_types:
        rel_type = row["relationshipType"]
        if _is_internal_schema_name(str(rel_type)):
            continue
        patterns = [pattern for pattern in visual_patterns if pattern["type"] == rel_type][:20]
        properties: list[str] = []
        relationships.append(
            {
                "type": rel_type,
                "patterns": [
                    {
                        "from": pattern["from"],
                        "to": pattern["to"],
                        "count": pattern.get("count"),
                    }
                    for pattern in patterns
                ],
                "properties": [
                    {"name": prop, "role": _property_role(prop), "non_null": None}
                    for prop in properties
                ],
            }
        )
    return relationships


def _label_examples(labels: list[dict]) -> list[dict]:
    examples: list[dict] = []
    for index, label_info in enumerate(labels, start=1):
        label = label_info["label"]
        props = [prop["name"] for prop in label_info.get("properties", [])[:3]]
        if props:
            projection = ", ".join(f"n.{_quote_ident(prop)} AS {prop}" for prop in props)
            question = f"List sample {label} records."
        else:
            projection = "n"
            question = f"List sample {label} nodes."
        cypher = f"MATCH (n:{_quote_ident(label)}) RETURN {projection} LIMIT 5"
        examples.append(_example_record(index, question, cypher))
    return examples


def _relationship_examples(relationships: list[dict], start_index: int) -> list[dict]:
    examples: list[dict] = []
    row = start_index
    for rel_info in relationships:
        rel_type = rel_info["type"]
        for pattern in rel_info.get("patterns", [])[:2]:
            question = (
                f"List sample {pattern['from']} connected to {pattern['to']} through {rel_type}."
            )
            cypher = (
                f"MATCH (a:{_quote_ident(pattern['from'])})"
                f"-[r:{_quote_ident(rel_type)}]->"
                f"(b:{_quote_ident(pattern['to'])}) "
                "RETURN a, r, b LIMIT 5"
            )
            examples.append(_example_record(row, question, cypher))
            row += 1
    return examples


def _preferred_props(label_info: dict, roles: set[str], limit: int = 3) -> list[str]:
    props = [prop["name"] for prop in label_info.get("properties", []) if prop.get("role") in roles]
    return props[:limit]


def _projection(alias: str, props: list[str]) -> str:
    if not props:
        return alias
    return ", ".join(f"{alias}.{_quote_ident(prop)} AS {alias}_{prop}" for prop in props)


def _label_recipe_examples(labels: list[dict], start_index: int) -> list[dict]:
    examples: list[dict] = []
    row = start_index
    for label_info in labels:
        label = label_info["label"]
        identity_props = _preferred_props(label_info, {"identity", "text"}, limit=3)
        metric_props = _preferred_props(label_info, {"metric", "temporal"}, limit=8)

        recipes = [
            (
                f"How many {label} nodes are in the graph?",
                f"MATCH (n:{_quote_ident(label)}) RETURN count(n) AS {label.lower()}Count",
            ),
        ]
        for prop in metric_props:
            recipes.append(
                (
                    f"List top {label} records by {prop}.",
                    f"MATCH (n:{_quote_ident(label)}) "
                    f"WHERE n.{_quote_ident(prop)} IS NOT NULL "
                    f"RETURN {_projection('n', identity_props + [prop])} "
                    f"ORDER BY n.{_quote_ident(prop)} DESC LIMIT 10",
                )
            )
        for prop in identity_props[:2]:
            recipes.append(
                (
                    f"Find {label} records by {prop}.",
                    f"MATCH (n:{_quote_ident(label)}) "
                    f"WHERE n.{_quote_ident(prop)} IS NOT NULL "
                    f"RETURN {_projection('n', identity_props)} LIMIT 10",
                )
            )

        for question, cypher in recipes:
            examples.append(_example_record(row, question, cypher))
            row += 1
    return examples


def _relationship_recipe_examples(
    labels: list[dict],
    relationships: list[dict],
    start_index: int,
) -> list[dict]:
    label_index = {label["label"]: label for label in labels}
    examples: list[dict] = []
    row = start_index
    for rel_info in relationships:
        rel_type = rel_info["type"]
        for pattern in rel_info.get("patterns", []):
            from_label = pattern["from"]
            to_label = pattern["to"]
            from_props = _preferred_props(
                label_index.get(from_label, {}), {"identity", "text"}, limit=2
            )
            to_props = _preferred_props(
                label_index.get(to_label, {}), {"identity", "text"}, limit=2
            )
            recipes = [
                (
                    f"Count {to_label} records connected from each {from_label} through {rel_type}.",
                    f"MATCH (a:{_quote_ident(from_label)})-[:{_quote_ident(rel_type)}]->"
                    f"(b:{_quote_ident(to_label)}) "
                    f"RETURN {_projection('a', from_props)}, count(b) AS connectedCount "
                    "ORDER BY connectedCount DESC LIMIT 10",
                ),
                (
                    f"Count {from_label} records connected to each {to_label} through {rel_type}.",
                    f"MATCH (a:{_quote_ident(from_label)})-[:{_quote_ident(rel_type)}]->"
                    f"(b:{_quote_ident(to_label)}) "
                    f"RETURN {_projection('b', to_props)}, count(a) AS connectedCount "
                    "ORDER BY connectedCount DESC LIMIT 10",
                ),
                (
                    f"List {from_label} to {to_label} connections through {rel_type}.",
                    f"MATCH (a:{_quote_ident(from_label)})-[:{_quote_ident(rel_type)}]->"
                    f"(b:{_quote_ident(to_label)}) "
                    f"RETURN {_projection('a', from_props)}, {_projection('b', to_props)} LIMIT 10",
                ),
            ]
            for question, cypher in recipes:
                examples.append(_example_record(row, question, cypher))
                row += 1
    return examples


def _schema_edges(relationships: list[dict]) -> list[dict]:
    edges: list[dict] = []
    for rel_info in relationships:
        for pattern in rel_info.get("patterns", []):
            edges.append(
                {
                    "from": pattern["from"],
                    "type": rel_info["type"],
                    "to": pattern["to"],
                }
            )
    return edges


def _path_signature(path: list[dict]) -> str:
    if not path:
        return ""
    bits = [f"({path[0]['from']})"]
    for edge in path:
        bits.append(f"-[:{edge['type']}]->({edge['to']})")
    return "".join(bits)


def _enumerate_schema_paths(relationships: list[dict], max_hops: int) -> list[dict]:
    edges = _schema_edges(relationships)
    by_from: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        by_from[edge["from"]].append(edge)

    paths: list[list[dict]] = []

    def walk(current_label: str, path: list[dict]) -> None:
        if len(path) >= max_hops:
            return
        for edge in by_from.get(current_label, []):
            next_path = path + [edge]
            paths.append(next_path)
            if len(next_path) < max_hops:
                walk(edge["to"], next_path)

    for start in sorted(by_from):
        walk(start, [])

    seen: set[str] = set()
    result: list[dict] = []
    for path in paths:
        signature = _path_signature(path)
        if signature in seen:
            continue
        seen.add(signature)
        result.append(
            {
                "hops": len(path),
                "signature": signature,
                "edges": path,
            }
        )
    return result


def _path_recipe_examples(paths: list[dict], start_index: int) -> list[dict]:
    examples: list[dict] = []
    row = start_index
    for path in paths:
        if path["hops"] < 2:
            continue
        edges = path["edges"]
        node_labels = [edges[0]["from"]] + [edge["to"] for edge in edges]
        match = f"(n0:{_quote_ident(node_labels[0])})"
        for idx, edge in enumerate(edges):
            match += f"-[:{_quote_ident(edge['type'])}]->(n{idx + 1}:{_quote_ident(edge['to'])})"
        question = (
            f"Find {node_labels[0]} records connected to {node_labels[-1]} "
            f"through the path {path['signature']}."
        )
        returns = ", ".join(f"n{idx}" for idx in range(len(node_labels)))
        cypher = f"MATCH {match} RETURN {returns} LIMIT 10"
        examples.append(_example_record(row, question, cypher))
        row += 1
    return examples


def _build_query_recipes(labels: list[dict], relationships: list[dict], paths: list[dict]) -> dict:
    return {
        "label_recipe_count": sum(
            1
            + len(_preferred_props(label, {"metric", "temporal"}, limit=8))
            + min(2, len(_preferred_props(label, {"identity", "text"}, limit=3)))
            for label in labels
        ),
        "relationship_recipe_count": sum(len(rel.get("patterns", [])) * 3 for rel in relationships),
        "path_recipe_count": sum(1 for path in paths if path["hops"] >= 2),
        "recipe_policy": [
            "Prefer relationship/path recipes when the question mentions two or more entity types.",
            "Prefer metric recipes for top/highest/most/ranking questions.",
            "Prefer identity/text properties for lookup and projection.",
            "Use LIMIT only when the question asks for top/first/sample or the recipe is exploratory.",
        ],
    }


def _value_limit_clause(value_limit: int) -> tuple[str, dict]:
    if value_limit > 0:
        return "LIMIT $value_limit", {"value_limit": value_limit}
    return "", {}


def _collect_value_profile(session: Any, labels: list[dict], value_limit: int) -> dict:
    profile: dict[str, dict] = {}
    limit_clause, params = _value_limit_clause(value_limit)
    for label_info in labels:
        label = label_info["label"]
        label_values: dict[str, list] = {}
        candidate_props = [
            prop["name"]
            for prop in label_info.get("properties", [])
            if prop.get("role") in {"identity", "text", "metric", "temporal"}
        ]
        for prop in candidate_props:
            try:
                rows = _query(
                    session,
                    f"""
                    MATCH (n:{_quote_ident(label)})
                    WHERE n.{_quote_ident(prop)} IS NOT NULL
                    RETURN n.{_quote_ident(prop)} AS value, count(*) AS frequency
                    ORDER BY frequency DESC, value
                    {limit_clause}
                    """,
                    **params,
                )
            except Exception as exc:
                label_values[prop] = [{"error": str(exc)[:300]}]
                continue
            label_values[prop] = [
                {"value": _jsonable(row.get("value")), "frequency": int(row.get("frequency") or 0)}
                for row in rows
            ]
        profile[label] = label_values
    return profile


def _example_record(index: int, question: str, cypher: str) -> dict:
    return {
        "row": index,
        "question": question,
        "question_profile": profile_question(question).to_dict(),
        "cypher": cypher,
        "cypher_shape": parse_cypher_shape(cypher).to_dict(),
    }


def _summarize_live_profile(labels: list[dict], relationships: list[dict]) -> dict:
    paths = _enumerate_schema_paths(relationships, max_hops=3)
    return {
        "label_count": len(labels),
        "relationship_type_count": len(relationships),
        "schema_path_count": len(paths),
        "top_labels": sorted(
            [{"label": item["label"], "count": item["count"]} for item in labels],
            key=lambda item: (-item["count"], item["label"]),
        )[:20],
        "top_relationship_patterns": [
            {
                "type": rel["type"],
                "from": pattern["from"],
                "to": pattern["to"],
                "count": pattern["count"],
            }
            for rel in relationships
            for pattern in rel.get("patterns", [])[:3]
        ][:40],
    }


def _build_indexes(examples: list[dict]) -> tuple[dict, dict, dict]:
    intent_to_shapes: dict[str, Counter[str]] = defaultdict(Counter)
    motif_to_returns: dict[str, Counter[str]] = defaultdict(Counter)
    token_to_motifs: dict[str, Counter[str]] = defaultdict(Counter)
    for example in examples:
        q_profile = example["question_profile"]
        c_shape = example["cypher_shape"]
        signature = c_shape["signature"]
        return_kinds = ",".join(item["kind"] for item in c_shape["return_items"])
        for intent in q_profile["intents"]:
            intent_to_shapes[intent][signature] += 1
        for motif in c_shape["path_motifs"]:
            motif_to_returns[motif][return_kinds] += 1
            for token in q_profile["tokens"]:
                token_to_motifs[token][motif] += 1
    return (
        {intent: counter.most_common(10) for intent, counter in sorted(intent_to_shapes.items())},
        {motif: counter.most_common(10) for motif, counter in sorted(motif_to_returns.items())},
        {token: counter.most_common(8) for token, counter in sorted(token_to_motifs.items())},
    )


def build_profile_from_neo4j(
    *,
    uri: str,
    username: str,
    password: str,
    database: str,
    profile_name: str | None = None,
    output_path: str | Path | None = None,
    sample_limit: int = 0,
    max_hops: int = 3,
    value_limit: int = 0,
    include_value_profile: bool = True,
) -> dict:
    """Build a reusable profile from a live Neo4j database without benchmark rows.

    sample_limit=0 means unbounded sampling for fallback property discovery.
    """
    driver = GraphDatabase.driver(uri, auth=(username, password), connection_timeout=20)
    try:
        with driver.session(database=database) as session:
            visual_label_props, visual_patterns = _schema_visualization(session)
            labels = _collect_labels(session, sample_limit, visual_label_props)
            relationships = _collect_relationships(session, sample_limit, visual_patterns)
            value_profile = (
                _collect_value_profile(session, labels, value_limit)
                if include_value_profile
                else {}
            )
    finally:
        driver.close()

    paths = _enumerate_schema_paths(relationships, max_hops=max_hops)
    examples = _label_examples(labels)
    examples.extend(_relationship_examples(relationships, len(examples) + 1))
    examples.extend(_label_recipe_examples(labels, len(examples) + 1))
    examples.extend(_relationship_recipe_examples(labels, relationships, len(examples) + 1))
    examples.extend(_path_recipe_examples(paths, len(examples) + 1))
    questions = [example["question"] for example in examples]
    shapes = [parse_cypher_shape(example["cypher"]) for example in examples]
    intent_to_shapes, motif_to_returns, token_to_motifs = _build_indexes(examples)

    profile = {
        "source": uri,
        "source_type": "neo4j_live",
        "database": (profile_name or database).lower(),
        "row_count": len(examples),
        "schema_profile": {
            "labels": labels,
            "relationships": relationships,
            "paths": paths,
            "summary": _summarize_live_profile(labels, relationships),
        },
        "value_profile": value_profile,
        "query_recipe_profile": _build_query_recipes(labels, relationships, paths),
        "question_summary": summarize_questions(questions),
        "cypher_summary": summarize_shapes(shapes),
        "intent_to_shape_signatures": intent_to_shapes,
        "motif_to_return_contracts": motif_to_returns,
        "token_to_path_motifs": token_to_motifs,
        "examples": examples,
    }
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile
