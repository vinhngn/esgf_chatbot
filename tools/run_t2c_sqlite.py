"""CLI wrapper around the same benchmark service used by Streamlit."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_T2C_ROOT = PROJECT_ROOT.parent / "t2c_eval_framework"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.evaluation.models import BenchmarkConfig, DatabaseTarget  # noqa: E402
from services.evaluation.runner import count_cases, run_benchmark  # noqa: E402
from services.evaluation.store import BenchmarkStore  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a T2C-compatible benchmark with SQLite persistence."
    )
    parser.add_argument(
        "--t2c-root",
        type=Path,
        default=DEFAULT_T2C_ROOT,
    )
    parser.add_argument("--db", required=True, help="CSV stem and logical database.")
    parser.add_argument("--rows", type=int, default=50, help="Use 0 for all rows.")
    parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:8954/api/text2cypher",
    )
    parser.add_argument("--neo4j-uri", required=True)
    parser.add_argument("--neo4j-username", required=True)
    parser.add_argument("--neo4j-password", required=True)
    parser.add_argument("--neo4j-database", required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--webhook-timeout", type=float, default=180)
    parser.add_argument("--query-timeout", type=float, default=30)
    parser.add_argument("--result-limit", type=int, default=1000)
    parser.add_argument("--result-json-max-chars", type=int, default=100_000)
    parser.add_argument("--sqlite", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    root = args.t2c_root.expanduser().resolve()
    config = BenchmarkConfig(
        dataset=args.db,
        input_file=root / "inputs" / f"{args.db}.csv",
        endpoint=args.endpoint,
        row_limit=args.rows,
        workers=args.workers,
        webhook_timeout=args.webhook_timeout,
        query_timeout=args.query_timeout,
        result_limit=args.result_limit,
        result_json_max_chars=args.result_json_max_chars,
    )
    target = DatabaseTarget(
        uri=args.neo4j_uri,
        username=args.neo4j_username,
        password=args.neo4j_password,
        database=args.neo4j_database,
        logical_database=args.db,
    )
    store = BenchmarkStore(args.sqlite)
    run_id = uuid4().hex[:12]
    store.create_run(
        run_id,
        config,
        logical_database=target.logical_database,
        physical_database=target.database,
        target_rows=count_cases(config.input_file, config.row_limit),
    )
    run_benchmark(
        run_id=run_id,
        config=config,
        target=target,
        framework_root=root,
        store=store,
        stop_event=threading.Event(),
    )
    print(
        json.dumps(
            store.run_summary(run_id),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
