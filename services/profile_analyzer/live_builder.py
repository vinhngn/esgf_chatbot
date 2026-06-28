from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

from services.profile_analyzer.cypher_shape import parse_cypher_shape, summarize_shapes
from services.profile_analyzer.question_profile import profile_question, summarize_questions


def _quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _is_internal_schema_name(name: str) -> bool:
    return name.startswith("_")


def _first_value(rows: list[dict], key: str, default: Any = None) -> Any:
    if not rows:
        return default
    return rows[0].get(key, default)


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
    rows = _query(
        session,
        f"""
        MATCH (n:{_quote_ident(label)})
        WITH n
        LIMIT $sample_limit
        UNWIND keys(n) AS property
        RETURN property, count(*) AS seen
        ORDER BY seen DESC, property
        LIMIT 30
        """,
        sample_limit=sample_limit,
    )
    return [str(row["property"]) for row in rows if row.get("property")]


def _node_property_index(session: Any, labels: list[str], sample_limit: int) -> dict[str, list[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for label in labels:
        try:
            props = _sample_node_properties(session, label, sample_limit)
        except Exception:
            continue
        index[label].update(props)
    return {label: sorted(props) for label, props in index.items()}


def _sample_relationship_properties(session: Any, rel_type: str, sample_limit: int) -> list[str]:
    rows = _query(
        session,
        f"""
        MATCH ()-[r:{_quote_ident(rel_type)}]->()
        WITH r
        LIMIT $sample_limit
        UNWIND keys(r) AS property
        RETURN property, count(*) AS seen
        ORDER BY seen DESC, property
        LIMIT 20
        """,
        sample_limit=sample_limit,
    )
    return [str(row["property"]) for row in rows if row.get("property")]


def _collect_labels(session: Any, sample_limit: int, visual_label_props: dict[str, list[str]]) -> list[dict]:
    rows = _query(session, "CALL db.labels() YIELD label RETURN label ORDER BY label")
    label_names = [
        str(row["label"])
        for row in rows
        if not _is_internal_schema_name(str(row["label"]))
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
                    {"name": prop, "non_null": None}
                    for prop in properties
                ],
            }
        )
    return labels


def _collect_relationships(session: Any, sample_limit: int, visual_patterns: list[dict]) -> list[dict]:
    rel_types = _query(session, "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType ORDER BY relationshipType")
    relationships: list[dict] = []
    for row in rel_types:
        rel_type = row["relationshipType"]
        if _is_internal_schema_name(str(rel_type)):
            continue
        patterns = [
            pattern
            for pattern in visual_patterns
            if pattern["type"] == rel_type
        ][:20]
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
                    {"name": prop, "non_null": None}
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
                f"List sample {pattern['from']} connected to {pattern['to']} "
                f"through {rel_type}."
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


def _example_record(index: int, question: str, cypher: str) -> dict:
    return {
        "row": index,
        "question": question,
        "question_profile": profile_question(question).to_dict(),
        "cypher": cypher,
        "cypher_shape": parse_cypher_shape(cypher).to_dict(),
    }


def _summarize_live_profile(labels: list[dict], relationships: list[dict]) -> dict:
    return {
        "label_count": len(labels),
        "relationship_type_count": len(relationships),
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
    output_path: str | Path | None = None,
    sample_limit: int = 1000,
) -> dict:
    """Build a reusable profile from a live Neo4j database without benchmark rows."""
    driver = GraphDatabase.driver(uri, auth=(username, password), connection_timeout=20)
    try:
        with driver.session(database=database) as session:
            visual_label_props, visual_patterns = _schema_visualization(session)
            labels = _collect_labels(session, sample_limit, visual_label_props)
            relationships = _collect_relationships(session, sample_limit, visual_patterns)
    finally:
        driver.close()

    examples = _label_examples(labels)
    examples.extend(_relationship_examples(relationships, len(examples) + 1))
    questions = [example["question"] for example in examples]
    shapes = [parse_cypher_shape(example["cypher"]) for example in examples]
    intent_to_shapes, motif_to_returns, token_to_motifs = _build_indexes(examples)

    profile = {
        "source": uri,
        "source_type": "neo4j_live",
        "database": database.lower(),
        "row_count": len(examples),
        "schema_profile": {
            "labels": labels,
            "relationships": relationships,
            "summary": _summarize_live_profile(labels, relationships),
        },
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
