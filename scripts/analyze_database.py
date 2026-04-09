"""
Database Analyzer — deep profile of Neo4j demo databases.

Extracts everything the Planner needs to understand the data:
  1. Schema structure (labels, relationships, properties, types)
  2. Data statistics (node counts, rel counts, property completeness)
  3. Sample values for key properties
  4. Relationship patterns with cardinality
  5. Constraints and indexes
  6. Sample triples (real data paths)
  7. Property value distributions (unique counts, common values)

Usage:
  python scripts/analyze_database.py movies
  python scripts/analyze_database.py twitter
  python scripts/analyze_database.py recommendations
  python scripts/analyze_database.py northwind
  python scripts/analyze_database.py all
"""

from __future__ import annotations

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neo4j import GraphDatabase

# Demo database configs
DATABASES = {
    "movies": {"user": "movies", "pass": "movies", "db": "movies"},
    "twitter": {"user": "twitter", "pass": "twitter", "db": "twitter"},
    "recommendations": {"user": "recommendations", "pass": "recommendations", "db": "recommendations"},
    "northwind": {"user": "northwind", "pass": "northwind", "db": "northwind"},
}

URI = "neo4j+s://demo.neo4jlabs.com"


def analyze(db_name: str):
    conf = DATABASES[db_name]
    driver = GraphDatabase.driver(URI, auth=(conf["user"], conf["pass"]))

    print(f"\n{'='*80}")
    print(f"  DATABASE: {db_name}")
    print(f"{'='*80}\n")

    with driver.session(database=conf["db"]) as s:
        # 1. Labels and counts
        print("--- NODE LABELS & COUNTS ---")
        rows = s.run(
            "MATCH (n) WITH labels(n)[0] AS label, count(n) AS cnt "
            "RETURN label, cnt ORDER BY cnt DESC"
        ).data()
        for r in rows:
            print(f"  :{r['label']}  ({r['cnt']} nodes)")
        print()

        # 2. Relationship types and counts
        print("--- RELATIONSHIP TYPES & COUNTS ---")
        rows = s.run(
            "MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS cnt ORDER BY cnt DESC"
        ).data()
        for r in rows:
            print(f"  [:{r['t']}]  ({r['cnt']} rels)")
        print()

        # 3. Relationship patterns (from_label -> rel -> to_label)
        print("--- RELATIONSHIP PATTERNS ---")
        rows = s.run(
            "MATCH (a)-[r]->(b) "
            "WITH labels(a)[0] AS f, type(r) AS t, labels(b)[0] AS to, count(*) AS c "
            "RETURN f, t, to, c ORDER BY c DESC"
        ).data()
        for r in rows:
            print(f"  (:{r['f']})-[:{r['t']}]->(:{r['to']})  x{r['c']}")
        print()

        # 4. Properties per label
        print("--- PROPERTIES PER LABEL ---")
        labels = [r.get('label', r.get('name', '')) for r in s.run("CALL db.labels()").data()]
        for label in sorted(labels):
            try:
                props_rows = s.run(
                    f"MATCH (n:{label}) WITH n LIMIT 1 RETURN keys(n) AS props"
                ).data()
                if props_rows:
                    props = sorted(props_rows[0]['props'])
                    print(f"  :{label} → {props}")
                else:
                    print(f"  :{label} → (no nodes)")
            except Exception as e:
                print(f"  :{label} → ERROR: {e}")
        print()

        # 5. Property completeness per label
        print("--- PROPERTY COMPLETENESS ---")
        for label in sorted(labels):
            try:
                count_row = s.run(f"MATCH (n:{label}) RETURN count(n) AS total").data()
                total = count_row[0]['total'] if count_row else 0
                if total == 0:
                    continue

                props_rows = s.run(
                    f"MATCH (n:{label}) WITH n LIMIT 1 RETURN keys(n) AS props"
                ).data()
                if not props_rows:
                    continue
                props = props_rows[0]['props']

                print(f"  :{label} ({total} nodes)")
                for prop in sorted(props):
                    try:
                        non_null = s.run(
                            f"MATCH (n:{label}) WHERE n.{prop} IS NOT NULL RETURN count(n) AS cnt"
                        ).data()[0]['cnt']
                        pct = (non_null / total * 100) if total > 0 else 0
                        print(f"    .{prop}: {non_null}/{total} ({pct:.0f}%)")
                    except Exception:
                        print(f"    .{prop}: ERROR")
            except Exception as e:
                print(f"  :{label} → ERROR: {e}")
        print()

        # 6. Sample values for key properties
        print("--- SAMPLE VALUES (top 5 per property) ---")
        for label in sorted(labels):
            try:
                props_rows = s.run(
                    f"MATCH (n:{label}) WITH n LIMIT 1 RETURN keys(n) AS props"
                ).data()
                if not props_rows:
                    continue
                props = props_rows[0]['props']

                # Pick key properties (name-like, title-like, id-like)
                key_props = [p for p in props if any(
                    k in p.lower() for k in ['name', 'title', 'screen', 'id', 'category']
                )]
                if not key_props:
                    key_props = props[:3]

                for prop in key_props[:3]:
                    try:
                        vals = s.run(
                            f"MATCH (n:{label}) WHERE n.{prop} IS NOT NULL "
                            f"RETURN DISTINCT n.{prop} AS v ORDER BY v LIMIT 5"
                        ).data()
                        val_strs = [str(v['v'])[:50] for v in vals]
                        print(f"  :{label}.{prop}: {val_strs}")
                    except Exception:
                        pass
            except Exception:
                pass
        print()

        # 7. Relationship properties
        print("--- RELATIONSHIP PROPERTIES ---")
        rel_types = [r.get('relationshipType', r.get('t', '')) for r in s.run("CALL db.relationshipTypes()").data()]
        for rel in sorted(rel_types):
            try:
                props_rows = s.run(
                    f"MATCH ()-[r:{rel}]->() WITH r LIMIT 1 RETURN keys(r) AS props"
                ).data()
                if props_rows and props_rows[0]['props']:
                    props = sorted(props_rows[0]['props'])
                    print(f"  [:{rel}] → {props}")

                    # Sample values for rel properties
                    for prop in props[:3]:
                        try:
                            vals = s.run(
                                f"MATCH ()-[r:{rel}]->() WHERE r.{prop} IS NOT NULL "
                                f"RETURN DISTINCT r.{prop} AS v LIMIT 3"
                            ).data()
                            val_strs = [str(v['v'])[:50] for v in vals]
                            print(f"    .{prop}: {val_strs}")
                        except Exception:
                            pass
                else:
                    print(f"  [:{rel}] → (no properties)")
            except Exception as e:
                print(f"  [:{rel}] → ERROR: {e}")
        print()

        # 8. Constraints
        print("--- CONSTRAINTS ---")
        try:
            rows = s.run("SHOW CONSTRAINTS").data()
            for r in rows:
                row = dict(r)
                print(f"  {row.get('type','?')}: {row.get('labelsOrTypes','?')}.{row.get('properties','?')}")
        except Exception as e:
            print(f"  Not available: {e}")
        print()

        # 9. Indexes
        print("--- INDEXES ---")
        try:
            rows = s.run("SHOW INDEXES").data()
            for r in rows:
                row = dict(r)
                if row.get('labelsOrTypes'):
                    print(f"  {row.get('name','?')}: {row.get('labelsOrTypes','?')}.{row.get('properties','?')} ({row.get('type','?')})")
        except Exception as e:
            print(f"  Not available: {e}")
        print()

        # 10. Sample triples (1 per relationship type)
        print("--- SAMPLE TRIPLES ---")
        patterns = s.run(
            "MATCH (a)-[r]->(b) "
            "WITH labels(a)[0] AS f, type(r) AS t, labels(b)[0] AS to "
            "RETURN DISTINCT f, t, to"
        ).data()
        for pat in patterns:
            try:
                f_label, rel, t_label = pat['f'], pat['t'], pat['to']
                # Get key properties directly
                a_key_prop = _guess_key_prop_for_label(f_label)
                b_key_prop = _guess_key_prop_for_label(t_label)
                sample = s.run(
                    f"MATCH (a:{f_label})-[r:{rel}]->(b:{t_label}) "
                    f"RETURN a.{a_key_prop} AS a_val, type(r) AS rel, "
                    f"properties(r) AS r_props, b.{b_key_prop} AS b_val LIMIT 1"
                ).data()
                if sample:
                    row = sample[0]
                    r_props = row.get('r_props', {})
                    r_str = f" {r_props}" if r_props else ""
                    print(f"  (:{f_label} {{{a_key_prop}: '{row['a_val']}'}}) "
                          f"-[:{rel}{r_str}]-> "
                          f"(:{t_label} {{{b_key_prop}: '{row['b_val']}'}})")
            except Exception as e:
                print(f"  (:{pat['f']})-[:{pat['t']}]->(:{pat['to']}) → ERROR: {e}")
        print()

    driver.close()


def _pick_key(props: dict) -> str:
    """Pick the best display property from a node's properties."""
    for key in ['name', 'title', 'screen_name', 'companyName', 'productName',
                'categoryName', 'text', 'orderID', 'customerID']:
        if key in props:
            val = str(props[key])[:40]
            return f"{key}: '{val}'"
    for k, v in props.items():
        if isinstance(v, str) and len(v) > 2:
            return f"{k}: '{str(v)[:40]}'"
    return ""


def _guess_key_prop_for_label(label: str) -> str:
    """Guess key property for a label."""
    l = label.lower()
    if "movie" in l or "film" in l:
        return "title"
    if "tweet" in l:
        return "text"
    if "category" in l:
        return "categoryName"
    if "product" in l:
        return "productName"
    if "order" in l:
        return "orderID"
    if "supplier" in l or "customer" in l:
        return "companyName"
    if "hashtag" in l or "link" in l or "source" in l or "genre" in l:
        return "name"
    return "name"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/analyze_database.py <database|all>")
        print("Databases: movies, twitter, recommendations, northwind")
        sys.exit(1)

    target = sys.argv[1].lower()

    if target == "all":
        for db in DATABASES:
            try:
                analyze(db)
            except Exception as e:
                print(f"\nERROR analyzing {db}: {e}\n")
    elif target in DATABASES:
        analyze(target)
    else:
        print(f"Unknown database: {target}")
        print(f"Available: {list(DATABASES.keys())}")
        sys.exit(1)
