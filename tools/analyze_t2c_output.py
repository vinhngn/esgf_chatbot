from __future__ import annotations

import argparse
import ast
import csv
import re
from collections import Counter
from pathlib import Path


def _return_body(cypher: str) -> str:
    match = re.search(r"(?is)\bRETURN\b\s+(.*?)(?:\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)", cypher or "")
    return match.group(1).strip() if match else ""


def _has_aggregate(cypher: str) -> bool:
    return bool(re.search(r"(?i)\b(count|avg|sum|min|max|collect)\s*\(", cypher or ""))


def _has_order(cypher: str) -> bool:
    return bool(re.search(r"(?i)\bORDER\s+BY\b", cypher or ""))


def _parse_rows(value: str) -> list:
    try:
        parsed = ast.literal_eval(value or "[]")
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _columns(rows: list) -> set[str]:
    cols: set[str] = set()
    for row in rows[:5]:
        if isinstance(row, dict):
            cols.update(str(key) for key in row)
    return cols


def _classify(row: dict) -> str:
    if str(row.get("exact_match", "")).strip() == "1":
        return "correct"
    gold = row.get("original_cypher", "")
    gen = row.get("generated_cypher", "")
    if not gen or gen.startswith("CYPHER_ERROR"):
        return "generation_or_execution_error"

    gold_rows = _parse_rows(row.get("original_results", ""))
    gen_rows = _parse_rows(row.get("generated_results", ""))
    if not gen_rows and gold_rows:
        return "empty_generated_result"
    if gen_rows and not gold_rows:
        return "extra_generated_result"

    gold_cols = _columns(gold_rows)
    gen_cols = _columns(gen_rows)
    if gold_cols and gen_cols and gold_cols != gen_cols:
        return "return_projection_mismatch"

    if _has_aggregate(gold) != _has_aggregate(gen):
        return "aggregate_shape_mismatch"
    if _has_order(gold) != _has_order(gen):
        return "ordering_shape_mismatch"

    gold_return = _return_body(gold)
    gen_return = _return_body(gen)
    if gold_return and gen_return and gold_return != gen_return:
        return "return_expression_mismatch"
    return "value_or_row_mismatch"


def main() -> None:
    parser = argparse.ArgumentParser(description="Group T2C failures into general error buckets.")
    parser.add_argument("csv_path")
    parser.add_argument("--examples", type=int, default=3)
    args = parser.parse_args()

    path = Path(args.csv_path)
    buckets: Counter[str] = Counter()
    examples: dict[str, list[dict]] = {}
    total = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader, start=1):
            total += 1
            bucket = _classify(row)
            buckets[bucket] += 1
            examples.setdefault(bucket, [])
            if len(examples[bucket]) < args.examples and bucket != "correct":
                examples[bucket].append({
                    "row": index,
                    "question": row.get("nl_question", ""),
                    "gold": row.get("original_cypher", ""),
                    "generated": row.get("generated_cypher", ""),
                })

    correct = buckets.get("correct", 0)
    print(f"file={path}")
    print(f"total={total} exact={correct}/{total} ({(correct / total * 100) if total else 0:.2f}%)")
    print("buckets:")
    for bucket, count in buckets.most_common():
        print(f"- {bucket}: {count}")
    print("examples:")
    for bucket, rows in examples.items():
        if bucket == "correct" or not rows:
            continue
        print(f"\n[{bucket}]")
        for item in rows:
            print(f"row {item['row']}: {item['question']}")
            print(f"  gold: {item['gold']}")
            print(f"  gen : {item['generated']}")


if __name__ == "__main__":
    main()
