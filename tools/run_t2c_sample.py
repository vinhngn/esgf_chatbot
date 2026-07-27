from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_T2C_ROOT = PROJECT_ROOT.parent / "t2c_eval_framework"


def _load_t2c(root: Path):
    sys.path.insert(0, str(root))
    from services.column_matcher import ColumnMatcher
    from services.config_loader import ConfigLoader
    from services.evaluator_pipeline import EvaluatorPipeline
    from services.logging_setup import setup_logging
    from services.metrics_calculator import MetricsCalculator
    from services.query_executer import QueryExecutor
    from services.result_comparator import ResultComparator
    from services.webhook_client import WebhookClient

    return {
        "ConfigLoader": ConfigLoader,
        "setup_logging": setup_logging,
        "WebhookClient": WebhookClient,
        "QueryExecutor": QueryExecutor,
        "ResultComparator": ResultComparator,
        "MetricsCalculator": MetricsCalculator,
        "ColumnMatcher": ColumnMatcher,
        "EvaluatorPipeline": EvaluatorPipeline,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a bounded T2C benchmark sample without editing T2C config files."
    )
    parser.add_argument("--t2c-root", default=str(DEFAULT_T2C_ROOT))
    parser.add_argument("--db", required=True, help="Logical database/profile name and CSV stem.")
    parser.add_argument("--rows", type=int, default=50)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8954/api/text2cypher")
    parser.add_argument("--neo4j-uri", default="", help="Override the Neo4j URI from T2C config.")
    parser.add_argument(
        "--neo4j-username", default="", help="Defaults to --db for Neo4j demo databases."
    )
    parser.add_argument(
        "--neo4j-password", default="", help="Defaults to --db for Neo4j demo databases."
    )
    parser.add_argument(
        "--neo4j-database", default="", help="Physical Neo4j database. Defaults to --db."
    )
    parser.add_argument("--output-suffix", default="")
    parser.add_argument("--query-timeout", type=int, default=20)
    parser.add_argument("--result-limit", type=int, default=1000)
    args = parser.parse_args()

    root = Path(args.t2c_root).resolve()
    if not root.exists():
        raise SystemExit(f"T2C root not found: {root}")

    modules = _load_t2c(root)
    if sys.maxsize > 2_147_483_647:
        # The upstream T2C runner passes sys.maxsize to csv.field_size_limit().
        # On Windows that overflows a C long before any benchmark row is read.
        sys.maxsize = 2_147_483_647
    config = modules["ConfigLoader"].load(str(root / "config" / "config.yml"))
    config["neo4j"].update(
        {
            "username": args.neo4j_username or args.db,
            "password": args.neo4j_password or args.db,
            "database": args.neo4j_database or args.db,
        }
    )
    if args.neo4j_uri:
        config["neo4j"]["uri"] = args.neo4j_uri
    config["webhook"]["endpoint"] = args.endpoint
    config["evaluation"].update(
        {
            "max_rows": args.rows,
            "file_name": f"{args.db}.csv",
            "input_path": str(root / "inputs"),
            "output_path": str(root / "outputs"),
            "log_queries": False,
        }
    )
    config.setdefault("agents", {}).setdefault("diagnoser", {})["enabled"] = False
    config.setdefault("agents", {}).setdefault("classifier", {})["enabled"] = False
    config.setdefault("agents", {}).setdefault("verifier", {})["enabled"] = False
    config.setdefault("embeddings", {})["enabled"] = False

    modules["setup_logging"](config)
    column_matcher = modules["ColumnMatcher"](cfg=config.get("embeddings", {}))

    class CappedQueryExecutor(modules["QueryExecutor"]):
        _result_cache: dict[tuple[str, str], list[dict]] = {}

        def _cap_query(self, cypher: str) -> str:
            if args.result_limit <= 0:
                return cypher
            cleaned = (cypher or "").strip().rstrip(";")
            if not cleaned:
                return cleaned
            if re.search(r"(?i)\bLIMIT\s+\d+\s*$", cleaned):
                return cleaned
            return f"{cleaned} LIMIT {args.result_limit}"

        def run(self, cypher: str, database: str = None):
            if not cypher or not cypher.strip() or self.driver is None:
                return []
            db = database or self.default_db
            if args.neo4j_database and db == args.db:
                db = args.neo4j_database
            capped_cypher = self._cap_query(cypher)
            cache_key = (db, capped_cypher)
            if cache_key in self._result_cache:
                return [dict(row) for row in self._result_cache[cache_key]]
            try:
                with self.driver.session(database=db) as session:
                    with session.begin_transaction(timeout=args.query_timeout) as tx:
                        rows = [record.data() for record in tx.run(capped_cypher)]
                        self._result_cache[cache_key] = [dict(row) for row in rows]
                        return rows
            except Exception as exc:
                logging.error("[CappedQueryExecutor] Query failed or timed out: %s", exc)
                self._result_cache[cache_key] = []
                return []

    pipeline = modules["EvaluatorPipeline"](
        webhook_client=modules["WebhookClient"](config["webhook"]),
        executor=CappedQueryExecutor(config["neo4j"]),
        comparator=modules["ResultComparator"](column_matcher),
        metrics=modules["MetricsCalculator"](),
        evaluation_conf=config["evaluation"],
        config=config,
    )
    suffix = args.output_suffix or f"sample{args.rows}"
    output = root / "outputs" / f"{args.db}_{suffix}_evaluated.csv"
    pipeline.run(f"{args.db}.csv", output_csv=str(output))
    print(output)


if __name__ == "__main__":
    main()
