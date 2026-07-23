from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config import get_settings  # noqa: E402
from services.profile_analyzer import build_profile_from_csv, build_profile_from_neo4j  # noqa: E402
from services.profile_analyzer.store import profile_path  # noqa: E402


DEMO_URI = "neo4j+s://demo.neo4jlabs.com"
DEMO_DATABASES = ("movies", "northwind", "recommendations", "twitter", "stackoverflow")
CYPHERBENCH_PORTS = {
    "company": 15062,
    "fictional_character": 15063,
    "flight_accident": 15064,
    "geography": 15065,
}


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def _ask_non_negative_int(prompt: str, default: int) -> int:
    while True:
        raw = _ask(prompt, str(default))
        try:
            value = int(raw)
        except ValueError:
            print("Please enter a number.")
            continue
        if value < 0:
            print("Please enter zero or a positive number.")
            continue
        return value


def _ask_yes_no(prompt: str, default: bool = True) -> bool:
    default_text = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} [{default_text}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please answer y or n.")


def _print_profile_summary(profile: dict, output_path: Path, preview: int) -> None:
    schema_summary = profile.get("schema_profile", {}).get("summary", {})
    payload = {
        "database": profile["database"],
        "source_type": profile.get("source_type", "csv"),
        "row_count": profile["row_count"],
        "schema_summary": schema_summary,
        "query_recipe_profile": profile.get("query_recipe_profile", {}),
        "value_profile_labels": list((profile.get("value_profile") or {}).keys())[:preview],
        "top_path_motifs": profile["cypher_summary"]["path_motifs"][:preview],
        "top_shape_signatures": profile["cypher_summary"]["shape_signatures"][:preview],
        "output": str(output_path),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _interactive_live(args: argparse.Namespace) -> None:
    settings = get_settings()
    print("\nText-to-Cypher Live Profile Client")
    print("Leave blank to accept the value in brackets.\n")
    print("Neo4j Labs demo DBs: " + ", ".join(DEMO_DATABASES))
    print(
        "CypherBench via SSH tunnel: "
        + ", ".join(f"{name}=localhost:{port}" for name, port in CYPHERBENCH_PORTS.items())
    )

    uri = _ask("Neo4j URI", args.uri or settings.NEO4J_URI or DEMO_URI)
    database = _ask("Database", args.database or settings.NEO4J_DATABASE or "stackoverflow").lower()
    profile_name = _ask("Logical profile name", args.profile_name or database).lower()
    settings_db = (settings.NEO4J_DATABASE or "").strip().lower()
    same_as_env_db = bool(settings_db and settings_db == database)
    default_user = args.username or (settings.NEO4J_USERNAME if same_as_env_db else "") or database
    username = _ask("Username", default_user)

    if args.password:
        password = args.password
    else:
        default_password = (settings.NEO4J_PASSWORD if same_as_env_db else "") or database
        use_default_password = _ask_yes_no(f"Use password '{default_password}'?", True)
        password = default_password if use_default_password else getpass.getpass("Password: ")

    sample_limit = args.sample_limit if args.sample_limit is not None else _ask_non_negative_int("Sample limit (0 = unlimited)", 0)
    max_hops = args.max_hops if args.max_hops is not None else _ask_non_negative_int("Max schema path hops", 3)
    value_limit = args.value_limit if args.value_limit is not None else _ask_non_negative_int("Value profile limit (0 = unlimited)", 0)
    include_values = args.include_values if args.include_values is not None else _ask_yes_no("Include value profile?", True)
    preview = args.preview
    out = Path(args.out) if args.out else profile_path(profile_name)
    if out.exists() and not args.force:
        overwrite = _ask_yes_no(f"Profile already exists at {out}. Overwrite?", False)
        if not overwrite:
            print("Cancelled. Existing profile was kept.")
            return

    print("\nBuilding profile...")
    print(f"- uri: {uri}")
    print(f"- database: {database}")
    print(f"- profile: {profile_name}")
    print(f"- username: {username}")
    print(f"- output: {out}")
    print(f"- max_hops: {max_hops}")
    print(f"- value_profile: {include_values}")

    profile = build_profile_from_neo4j(
        uri=uri,
        username=username,
        password=password,
        database=database,
        profile_name=profile_name,
        output_path=out,
        sample_limit=sample_limit,
        max_hops=max_hops,
        value_limit=value_limit,
        include_value_profile=include_values,
    )
    print("\nProfile built successfully.\n")
    _print_profile_summary(profile, out, preview)


def _interactive_csv(args: argparse.Namespace) -> None:
    print("\nText-to-Cypher CSV Profile Client")
    print("Leave blank to accept the value in brackets.\n")

    csv_path = Path(_ask("CSV path", args.csv_path or "D:\\Agent\\t2c_eval_framework\\inputs\\northwind.csv"))
    database = _ask("Profile/database name", args.database or csv_path.stem).lower()
    out = Path(args.out) if args.out else profile_path(database)
    preview = args.preview
    if out.exists() and not args.force:
        overwrite = _ask_yes_no(f"Profile already exists at {out}. Overwrite?", False)
        if not overwrite:
            print("Cancelled. Existing profile was kept.")
            return

    profile = build_profile_from_csv(csv_path, output_path=out, database=database)
    print("\nProfile built successfully.\n")
    _print_profile_summary(profile, out, preview)


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive client for building Text-to-Cypher profile JSON files.")
    parser.add_argument("--mode", choices=("live", "csv"), default="live", help="Profile source mode.")
    parser.add_argument("--uri", default="", help="Neo4j URI for live mode.")
    parser.add_argument("--database", default="", help="Physical Neo4j database name.")
    parser.add_argument("--profile-name", default="", help="Logical profile name when it differs from the physical Neo4j database.")
    parser.add_argument("--username", default="", help="Neo4j username for live mode.")
    parser.add_argument("--password", default="", help="Neo4j password for live mode.")
    parser.add_argument("--csv-path", default="", help="CSV path for csv mode.")
    parser.add_argument("--out", default="", help="Output JSON path.")
    parser.add_argument("--sample-limit", type=int, default=None, help="Live sampling limit. 0 means unlimited.")
    parser.add_argument("--max-hops", type=int, default=None, help="Max schema path hops to turn into motifs and recipes.")
    parser.add_argument("--value-limit", type=int, default=None, help="Top values per profiled property. 0 means unlimited.")
    value_group = parser.add_mutually_exclusive_group()
    value_group.add_argument("--include-values", dest="include_values", action="store_true", default=None, help="Include live value profile.")
    value_group.add_argument("--no-values", dest="include_values", action="store_false", help="Skip live value profile.")
    parser.add_argument("--preview", type=int, default=5, help="Preview rows to print.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing profile without asking.")
    args = parser.parse_args()

    if args.mode == "csv":
        _interactive_csv(args)
    else:
        _interactive_live(args)


if __name__ == "__main__":
    main()
