from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from services.profile_analyzer.cypher_shape import parse_cypher_shape, summarize_shapes
from services.profile_analyzer.question_profile import profile_question, summarize_questions


def _row_database(row: dict, fallback: str) -> str:
    value = (row.get("database_name") or row.get("database") or fallback or "").strip()
    return value.lower() or "unknown"


def _read_rows(csv_path: Path) -> list[dict]:
    csv.field_size_limit(sys.maxsize)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _example_record(row: dict, index: int) -> dict:
    question = (row.get("question") or "").strip()
    cypher = (row.get("cypher") or "").strip()
    q_profile = profile_question(question)
    c_shape = parse_cypher_shape(cypher)
    return {
        "row": index,
        "question": question,
        "question_profile": q_profile.to_dict(),
        "cypher": cypher,
        "cypher_shape": c_shape.to_dict(),
    }


def build_profile_from_csv(
    csv_path: str | Path,
    *,
    output_path: str | Path | None = None,
    database: str = "",
) -> dict:
    """Build a reusable data/query profile from a T2C-style CSV file."""
    path = Path(csv_path)
    rows = _read_rows(path)
    examples = [_example_record(row, idx) for idx, row in enumerate(rows, start=1)]
    db_name = database or _row_database(rows[0], path.stem) if rows else path.stem
    questions = [example["question"] for example in examples]
    shapes = [parse_cypher_shape(example["cypher"]) for example in examples]

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

    profile = {
        "source": str(path),
        "database": db_name,
        "row_count": len(examples),
        "question_summary": summarize_questions(questions),
        "cypher_summary": summarize_shapes(shapes),
        "intent_to_shape_signatures": {
            intent: counter.most_common(10) for intent, counter in sorted(intent_to_shapes.items())
        },
        "motif_to_return_contracts": {
            motif: counter.most_common(10) for motif, counter in sorted(motif_to_returns.items())
        },
        "token_to_path_motifs": {
            token: counter.most_common(8)
            for token, counter in sorted(token_to_motifs.items())
            if sum(counter.values()) >= 2
        },
        "examples": examples,
    }
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def build_profiles(csv_paths: Iterable[str | Path], output_dir: str | Path) -> list[dict]:
    out_dir = Path(output_dir)
    profiles: list[dict] = []
    for csv_path in csv_paths:
        path = Path(csv_path)
        out = out_dir / f"{path.stem}_profile.json"
        profiles.append(build_profile_from_csv(path, output_path=out, database=path.stem))
    return profiles
