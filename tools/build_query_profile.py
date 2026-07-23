from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config import get_settings  # noqa: E402
from services.profile_analyzer import build_profile_from_csv, build_profile_from_neo4j  # noqa: E402
from services.profile_analyzer.store import profile_path  # noqa: E402


def _print_preview(profile: dict, output_path: Path, preview: int) -> None:
    schema_summary = profile.get("schema_profile", {}).get("summary", {})
    preview_payload = {
        "database": profile["database"],
        "source_type": profile.get("source_type", "csv"),
        "row_count": profile["row_count"],
        "schema_summary": schema_summary,
        "query_recipe_profile": profile.get("query_recipe_profile", {}),
        "value_profile_labels": list((profile.get("value_profile") or {}).keys())[:preview],
        "top_intents": profile["question_summary"]["intent_counts"][:preview],
        "top_path_motifs": profile["cypher_summary"]["path_motifs"][:preview],
        "top_shape_signatures": profile["cypher_summary"]["shape_signatures"][:preview],
        "return_kinds": profile["cypher_summary"]["return_kinds"],
        "output": str(output_path),
    }
    print(json.dumps(preview_payload, ensure_ascii=False, indent=2))


def _build_from_csv(args: argparse.Namespace) -> None:
    csv_path = Path(args.csv_path)
    database = (args.database or csv_path.stem).lower()
    out = Path(args.out) if args.out else profile_path(database)
    profile = build_profile_from_csv(csv_path, output_path=out, database=database)
    _print_preview(profile, out, args.preview)


def _build_from_live(args: argparse.Namespace) -> None:
    settings = get_settings()
    database = (args.database or settings.NEO4J_DATABASE).strip().lower()
    if not database:
        raise SystemExit("--database is required when NEO4J_DATABASE is not set")

    uri = args.uri or settings.NEO4J_URI or "neo4j+s://demo.neo4jlabs.com"
    username = args.username or settings.NEO4J_USERNAME or database
    password = args.password or settings.NEO4J_PASSWORD or database
    profile_name = (args.profile_name or database).strip().lower()
    out = Path(args.out) if args.out else profile_path(profile_name)
    profile = build_profile_from_neo4j(
        uri=uri,
        username=username,
        password=password,
        database=database,
        profile_name=profile_name,
        output_path=out,
        sample_limit=args.sample_limit,
        max_hops=args.max_hops,
        value_limit=args.value_limit,
        include_value_profile=not args.no_values,
    )
    _print_preview(profile, out, args.preview)


def main() -> None:
    # Backward compatibility:
    #   py tools/build_query_profile.py inputs/northwind.csv
    if len(sys.argv) > 1 and sys.argv[1] not in {"csv", "live", "-h", "--help"}:
        legacy_parser = argparse.ArgumentParser(
            description="Build Text-to-Cypher data/query profile JSON from a benchmark CSV."
        )
        legacy_parser.add_argument("csv_path")
        legacy_parser.add_argument("--database", default="")
        legacy_parser.add_argument("--out", default="")
        legacy_parser.add_argument("--preview", type=int, default=5)
        _build_from_csv(legacy_parser.parse_args())
        return

    parser = argparse.ArgumentParser(
        description="Build Text-to-Cypher profile JSON from benchmark CSV or live Neo4j."
    )
    subparsers = parser.add_subparsers(dest="mode")

    csv_parser = subparsers.add_parser("csv", help="Build profile from a T2C CSV file.")
    csv_parser.add_argument("csv_path", help="Path to a CSV containing question and cypher columns.")
    csv_parser.add_argument("--database", default="", help="Database/profile name. Defaults to CSV stem.")
    csv_parser.add_argument("--out", default="", help="Output JSON path. Defaults to generated_profiles/<database>_profile.json.")
    csv_parser.add_argument("--preview", type=int, default=5, help="Number of top motifs/signatures to print.")
    csv_parser.set_defaults(func=_build_from_csv)

    live_parser = subparsers.add_parser("live", help="Build profile from a live Neo4j database.")
    live_parser.add_argument("--uri", default="", help="Neo4j URI. Defaults to NEO4J_URI or Neo4j demo URI.")
    live_parser.add_argument("--database", required=True, help="Physical Neo4j database name.")
    live_parser.add_argument("--profile-name", default="", help="Logical profile name. Defaults to the physical database name.")
    live_parser.add_argument("--username", default="", help="Neo4j username. Defaults to database name.")
    live_parser.add_argument("--password", default="", help="Neo4j password. Defaults to database name.")
    live_parser.add_argument("--out", default="", help="Output JSON path. Defaults to generated_profiles/<profile-name>_profile.json.")
    live_parser.add_argument("--preview", type=int, default=5, help="Number of top motifs/signatures to print.")
    live_parser.add_argument("--sample-limit", type=int, default=0, help="Max sampled nodes/rels per label/type for fallback profiling. 0 means unlimited.")
    live_parser.add_argument("--max-hops", type=int, default=3, help="Max schema path hops to turn into motifs and recipes.")
    live_parser.add_argument("--value-limit", type=int, default=0, help="Top values per profiled property. 0 means unlimited.")
    live_parser.add_argument("--no-values", action="store_true", help="Skip live value profiling.")
    live_parser.set_defaults(func=_build_from_live)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        raise SystemExit(2)
    args.func(args)


if __name__ == "__main__":
    main()
