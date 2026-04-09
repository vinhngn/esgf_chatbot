"""
Automated Graph Knowledge Extraction for Text-to-Cypher.

Extracts structured knowledge from any Neo4j database into a JSON file.
This knowledge base eliminates LLM guessing by providing ground truth about:
  - Where each property lives (node vs relationship)
  - Which property to use for entity matching (unique constraints)
  - Ambiguous entities (same value in multiple labels)
  - Exact relationship directions
  - Property type information
  - Common pitfalls specific to this database

Usage:
  python scripts/extract_knowledge.py movies
  python scripts/extract_knowledge.py twitter
  python scripts/extract_knowledge.py all

Output: knowledge/<database>.json
"""

from __future__ import annotations

import json
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neo4j import GraphDatabase

logging.basicConfig(level=logging.WARNING)

URI = "neo4j+s://demo.neo4jlabs.com"
DATABASES = {
    "movies": {"user": "movies", "pass": "movies", "db": "movies"},
    "twitter": {"user": "twitter", "pass": "twitter", "db": "twitter"},
    "recommendations": {"user": "recommendations", "pass": "recommendations", "db": "recommendations"},
    "northwind": {"user": "northwind", "pass": "northwind", "db": "northwind"},
}

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge")


def extract(db_name: str) -> dict:
    """Extract complete knowledge base from a Neo4j database."""
    conf = DATABASES[db_name]
    driver = GraphDatabase.driver(URI, auth=(conf["user"], conf["pass"]))
    kb = {"database": db_name}

    with driver.session(database=conf["db"]) as s:

        # 1. Node property map
        kb["node_properties"] = _extract_node_properties(s)

        # 2. Relationship property map
        kb["relationship_properties"] = _extract_rel_properties(s)

        # 3. Relationship direction truth table
        kb["relationship_patterns"] = _extract_patterns(s)

        # 4. Unique constraints (match properties)
        kb["unique_constraints"] = _extract_constraints(s)

        # 5. Ambiguous entities
        kb["ambiguous_entities"] = _extract_ambiguous(s, kb["node_properties"])

        # 6. Property confusion pitfalls
        kb["property_confusion"] = _extract_confusion(kb["node_properties"], kb["relationship_properties"])

        # 7. Node counts
        kb["node_counts"] = _extract_node_counts(s)

        # 8. Sample values for key properties
        kb["sample_values"] = _extract_samples(s, kb["node_properties"], kb["unique_constraints"])

        # 9. Sample triples
        kb["sample_triples"] = _extract_sample_triples(s, kb["relationship_patterns"])

    driver.close()
    return kb


def _extract_node_properties(s) -> dict:
    """Extract all node properties with types and samples."""
    result = {}
    labels = [r.get("label", r.get("name", "")) for r in s.run("CALL db.labels()").data()]
    for label in sorted(labels):
        if label.startswith("_") or not label:
            continue
        try:
            props_rows = s.run(f"MATCH (n:{label}) WITH n LIMIT 1 RETURN keys(n) AS k").data()
            if not props_rows or not props_rows[0]["k"]:
                continue
            props = {}
            for p in sorted(props_rows[0]["k"]):
                try:
                    sample = s.run(
                        f"MATCH (n:{label}) WHERE n.{p} IS NOT NULL RETURN n.{p} AS v LIMIT 1"
                    ).data()
                    val = sample[0]["v"] if sample else None
                    props[p] = {
                        "type": type(val).__name__ if val is not None else "unknown",
                        "sample": str(val)[:80] if val is not None else None,
                    }
                except Exception:
                    props[p] = {"type": "unknown", "sample": None}
            result[label] = props
        except Exception:
            pass
    return result


def _extract_rel_properties(s) -> dict:
    """Extract all relationship properties with types and samples."""
    result = {}
    rel_types = [r.get("relationshipType", r.get("name", "")) for r in s.run("CALL db.relationshipTypes()").data()]
    for rel in sorted(rel_types):
        try:
            props_rows = s.run(f"MATCH ()-[r:{rel}]->() WITH r LIMIT 1 RETURN keys(r) AS k").data()
            if not props_rows or not props_rows[0]["k"]:
                result[rel] = {}
                continue
            props = {}
            for p in sorted(props_rows[0]["k"]):
                try:
                    sample = s.run(
                        f"MATCH ()-[r:{rel}]->() WHERE r.{p} IS NOT NULL RETURN r.{p} AS v LIMIT 1"
                    ).data()
                    val = sample[0]["v"] if sample else None
                    props[p] = {
                        "type": type(val).__name__ if val is not None else "unknown",
                        "sample": str(val)[:80] if val is not None else None,
                    }
                except Exception:
                    props[p] = {"type": "unknown", "sample": None}
            result[rel] = props
        except Exception:
            result[rel] = {}
    return result


def _extract_patterns(s) -> list:
    """Extract relationship direction truth table."""
    rows = s.run(
        "MATCH (a)-[r]->(b) "
        "WITH labels(a)[0] AS f, type(r) AS t, labels(b)[0] AS to, count(*) AS c "
        "RETURN f, t, to, c ORDER BY c DESC"
    ).data()
    return [{"from": r["f"], "rel": r["t"], "to": r["to"], "count": r["c"]} for r in rows]


def _extract_constraints(s) -> list:
    """Extract unique constraints."""
    try:
        rows = s.run("SHOW CONSTRAINTS").data()
        constraints = []
        for r in rows:
            row = dict(r)
            if row.get("type") == "UNIQUENESS":
                label = row.get("labelsOrTypes", ["?"])[0]
                prop = row.get("properties", ["?"])[0]
                constraints.append({"label": label, "property": prop})
        return constraints
    except Exception:
        return []


def _extract_ambiguous(s, node_properties: dict) -> list:
    """Find entities that exist in multiple labels."""
    name_map: dict[tuple, list] = {}
    for label in node_properties:
        for prop in ["name", "title", "screen_name", "companyName", "productName"]:
            if prop not in node_properties[label]:
                continue
            try:
                vals = s.run(
                    f"MATCH (n:{label}) WHERE n.{prop} IS NOT NULL "
                    f"RETURN DISTINCT n.{prop} AS v LIMIT 200"
                ).data()
                for v in vals:
                    key = (prop, str(v["v"])[:60])
                    if key not in name_map:
                        name_map[key] = []
                    if label not in name_map[key]:
                        name_map[key].append(label)
            except Exception:
                pass

    return [
        {"property": k[0], "value": k[1], "labels": v}
        for k, v in name_map.items()
        if len(v) > 1
    ]


def _extract_confusion(node_props: dict, rel_props: dict) -> list:
    """Find properties that exist on both nodes and relationships."""
    confusion = []
    for rel, rprops in rel_props.items():
        if not rprops:
            continue
        for label, nprops in node_props.items():
            overlap = set(rprops.keys()) & set(nprops.keys())
            if overlap:
                for p in overlap:
                    confusion.append({
                        "property": p,
                        "on_node": f":{label}",
                        "node_type": nprops[p]["type"],
                        "on_rel": f"[:{rel}]",
                        "rel_type": rprops[p]["type"],
                    })
    return confusion


def _extract_node_counts(s) -> dict:
    rows = s.run(
        "MATCH (n) WITH labels(n)[0] AS label, count(n) AS cnt "
        "RETURN label, cnt ORDER BY cnt DESC"
    ).data()
    return {r["label"]: r["cnt"] for r in rows if not r["label"].startswith("_")}


def _extract_samples(s, node_props: dict, constraints: list) -> dict:
    """Sample values for key properties."""
    # Prioritize unique constraint properties
    key_props: dict[str, list[str]] = {}
    for c in constraints:
        key_props.setdefault(c["label"], []).append(c["property"])
    # Fallback: common name properties
    for label in node_props:
        if label not in key_props:
            candidates = [p for p in ["name", "title", "screen_name", "companyName",
                                       "productName", "categoryName", "userId", "orderID"]
                          if p in node_props[label]]
            if candidates:
                key_props[label] = candidates[:2]

    result = {}
    for label, props in key_props.items():
        result[label] = {}
        for prop in props:
            try:
                vals = s.run(
                    f"MATCH (n:{label}) WHERE n.{prop} IS NOT NULL "
                    f"RETURN DISTINCT n.{prop} AS v ORDER BY v LIMIT 10"
                ).data()
                result[label][prop] = [str(v["v"])[:60] for v in vals]
            except Exception:
                pass
    return result


def _extract_sample_triples(s, patterns: list) -> list:
    """One real data sample per relationship type."""
    seen = set()
    triples = []
    for pat in patterns:
        rel = pat["rel"]
        if rel in seen:
            continue
        seen.add(rel)
        f_label, t_label = pat["from"], pat["to"]
        f_prop = _guess_key(f_label)
        t_prop = _guess_key(t_label)
        try:
            rows = s.run(
                f"MATCH (a:{f_label})-[r:{rel}]->(b:{t_label}) "
                f"WHERE a.{f_prop} IS NOT NULL AND b.{t_prop} IS NOT NULL "
                f"RETURN a.{f_prop} AS a_val, properties(r) AS r_props, b.{t_prop} AS b_val LIMIT 1"
            ).data()
            if rows:
                r = rows[0]
                rp = {k: str(v)[:40] for k, v in (r["r_props"] or {}).items()}
                triples.append({
                    "from": {"label": f_label, "property": f_prop, "value": str(r["a_val"])[:40]},
                    "rel": rel,
                    "rel_properties": rp,
                    "to": {"label": t_label, "property": t_prop, "value": str(r["b_val"])[:40]},
                })
        except Exception:
            pass
    return triples


def _guess_key(label: str) -> str:
    l = label.lower()
    if "movie" in l: return "title"
    if "tweet" in l: return "text"
    if "category" in l: return "categoryName"
    if "product" in l: return "productName"
    if "order" in l: return "orderID"
    if "supplier" in l or "customer" in l: return "companyName"
    return "name"


def save_knowledge(kb: dict, db_name: str):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"{db_name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(kb, f, indent=2, ensure_ascii=False, default=str)
    print(f"Saved: {path} ({os.path.getsize(path)} bytes)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/extract_knowledge.py <database|all>")
        sys.exit(1)

    target = sys.argv[1].lower()
    if target == "all":
        for db in DATABASES:
            print(f"\nExtracting: {db}...")
            try:
                kb = extract(db)
                save_knowledge(kb, db)
            except Exception as e:
                print(f"  ERROR: {e}")
    elif target in DATABASES:
        print(f"Extracting: {target}...")
        kb = extract(target)
        save_knowledge(kb, target)
    else:
        print(f"Unknown: {target}. Available: {list(DATABASES.keys())}")
