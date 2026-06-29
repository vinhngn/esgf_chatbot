from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path


def _load_t2c(root: Path):
    sys.path.insert(0, str(root))
    from services.config_loader import ConfigLoader
    from services.logging_setup import setup_logging
    from services.webhook_client import WebhookClient
    from services.query_executer import QueryExecutor
    from services.result_comparator import ResultComparator
    from services.metrics_calculator import MetricsCalculator
    from services.column_matcher import ColumnMatcher
    from services.evaluator_pipeline import EvaluatorPipeline

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
    parser = argparse.ArgumentParser(description="Run a bounded T2C benchmark sample without editing T2C config files.")
    parser.add_argument("--t2c-root", default=r"D:\Agent\t2c_eval_framework")
    parser.add_argument("--db", required=True, choices=["movies", "northwind", "recommendations", "twitter"])
    parser.add_argument("--rows", type=int, default=50)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8954/api/text2cypher")
    parser.add_argument("--output-suffix", default="")
    parser.add_argument("--query-timeout", type=int, default=20)
    parser.add_argument("--result-limit", type=int, default=1000)
    args = parser.parse_args()

    root = Path(args.t2c_root).resolve()
    if not root.exists():
        raise SystemExit(f"T2C root not found: {root}")

    modules = _load_t2c(root)
    config = modules["ConfigLoader"].load(str(root / "config" / "config.yml"))
    config["neo4j"].update({
        "username": args.db,
        "password": args.db,
        "database": args.db,
    })
    config["webhook"]["endpoint"] = args.endpoint
    config["evaluation"].update({
        "max_rows": args.rows,
        "file_name": f"{args.db}.csv",
        "input_path": str(root / "inputs"),
        "output_path": str(root / "outputs"),
        "log_queries": False,
    })
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
            return f"CALL {{ {cleaned} }} RETURN * LIMIT {args.result_limit}"

        def run(self, cypher: str, database: str = None):
            if not cypher or not cypher.strip() or self.driver is None:
                return []
            db = database or self.default_db
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
