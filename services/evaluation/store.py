from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.control_center.store import default_client_home
from services.evaluation.models import BenchmarkConfig, RunStatus


def _now() -> str:
    return datetime.now(UTC).isoformat()


class BenchmarkStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path)
            if path
            else default_client_home() / "benchmark_results.sqlite3"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self.mark_interrupted_runs()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    run_id TEXT PRIMARY KEY,
                    dataset TEXT NOT NULL,
                    input_file TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    logical_database TEXT NOT NULL,
                    physical_database TEXT NOT NULL,
                    row_limit INTEGER NOT NULL,
                    workers INTEGER NOT NULL,
                    target_rows INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    config_json TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS benchmark_results (
                    run_id TEXT NOT NULL,
                    row_id INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    schema_text TEXT NOT NULL DEFAULT '',
                    original_cypher TEXT NOT NULL,
                    generated_cypher TEXT NOT NULL,
                    original_results_json TEXT NOT NULL,
                    generated_results_json TEXT NOT NULL,
                    original_row_count INTEGER NOT NULL,
                    generated_row_count INTEGER NOT NULL,
                    exact_match REAL,
                    strict_column_match REAL,
                    soft_match REAL,
                    dice_score REAL,
                    api_error TEXT NOT NULL DEFAULT '',
                    original_error TEXT NOT NULL DEFAULT '',
                    generated_error TEXT NOT NULL DEFAULT '',
                    row_error TEXT NOT NULL DEFAULT '',
                    original_seconds REAL NOT NULL,
                    webhook_seconds REAL NOT NULL,
                    generated_seconds REAL NOT NULL,
                    total_seconds REAL NOT NULL,
                    completed_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, row_id),
                    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_benchmark_results_run
                ON benchmark_results(run_id, row_id);
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(benchmark_runs)").fetchall()
            }
            if "config_json" not in columns:
                db.execute(
                    "ALTER TABLE benchmark_runs "
                    "ADD COLUMN config_json TEXT NOT NULL DEFAULT ''"
                )

    def mark_interrupted_runs(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                UPDATE benchmark_runs
                SET status = ?, finished_at = ?, error = ?
                WHERE status IN (?, ?)
                """,
                (
                    RunStatus.INTERRUPTED.value,
                    _now(),
                    "Application stopped before the run completed.",
                    RunStatus.PENDING.value,
                    RunStatus.RUNNING.value,
                ),
            )

    def create_run(
        self,
        run_id: str,
        config: BenchmarkConfig,
        *,
        logical_database: str,
        physical_database: str,
        target_rows: int,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO benchmark_runs (
                    run_id, dataset, input_file, endpoint,
                    logical_database, physical_database,
                    row_limit, workers, target_rows, status, started_at,
                    config_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    config.dataset,
                    str(config.input_file),
                    config.endpoint,
                    logical_database,
                    physical_database,
                    config.row_limit,
                    config.workers,
                    target_rows,
                    RunStatus.PENDING.value,
                    _now(),
                    config.model_dump_json(),
                ),
            )

    def benchmark_config(self, run_id: str) -> BenchmarkConfig:
        """Load the original run configuration, including legacy run fallback."""
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM benchmark_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Benchmark run not found: {run_id}")

        config_json = str(row["config_json"] or "")
        if config_json:
            return BenchmarkConfig.model_validate_json(config_json)
        return BenchmarkConfig(
            dataset=str(row["dataset"]),
            input_file=Path(str(row["input_file"])),
            endpoint=str(row["endpoint"]),
            row_limit=int(row["row_limit"]),
            workers=int(row["workers"]),
            result_limit=0,
        )

    def completed_row_ids(self, run_id: str) -> set[int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT row_id FROM benchmark_results WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        return {int(row["row_id"]) for row in rows}

    def prepare_resume(self, run_id: str) -> None:
        """Reset terminal run state without deleting completed row results."""
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE benchmark_runs
                SET status = ?, finished_at = NULL, error = ''
                WHERE run_id = ?
                """,
                (RunStatus.PENDING.value, run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Benchmark run not found: {run_id}")

    def update_run(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: str = "",
    ) -> None:
        finished_at = (
            _now()
            if status
            in {
                RunStatus.COMPLETED,
                RunStatus.CANCELLED,
                RunStatus.FAILED,
                RunStatus.INTERRUPTED,
            }
            else None
        )
        with self._connect() as db:
            db.execute(
                """
                UPDATE benchmark_runs
                SET status = ?, finished_at = COALESCE(?, finished_at), error = ?
                WHERE run_id = ?
                """,
                (status.value, finished_at, error, run_id),
            )

    def save_result(self, run_id: str, result: dict[str, Any]) -> None:
        columns = (
            "row_id",
            "question",
            "schema_text",
            "original_cypher",
            "generated_cypher",
            "original_results_json",
            "generated_results_json",
            "original_row_count",
            "generated_row_count",
            "exact_match",
            "strict_column_match",
            "soft_match",
            "dice_score",
            "api_error",
            "original_error",
            "generated_error",
            "row_error",
            "original_seconds",
            "webhook_seconds",
            "generated_seconds",
            "total_seconds",
            "completed_at",
        )
        placeholders = ", ".join("?" for _ in range(len(columns) + 1))
        with self._connect() as db:
            db.execute(
                f"""
                INSERT OR REPLACE INTO benchmark_results (
                    run_id, {", ".join(columns)}
                ) VALUES ({placeholders})
                """,
                (run_id, *(result.get(column) for column in columns)),
            )

    def run_summary(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            run = db.execute(
                "SELECT * FROM benchmark_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                return None
            metrics = db.execute(
                """
                SELECT
                    COUNT(*) AS completed_rows,
                    SUM(CASE WHEN row_error <> '' THEN 1 ELSE 0 END) AS failed_rows,
                    SUM(CASE WHEN api_error <> '' THEN 1 ELSE 0 END) AS api_errors,
                    AVG(exact_match) AS exact_match,
                    AVG(strict_column_match) AS strict_column_match,
                    AVG(soft_match) AS soft_match,
                    AVG(dice_score) AS dice_score,
                    AVG(total_seconds) AS avg_seconds
                FROM benchmark_results
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return {
            **dict(run),
            **dict(metrics),
            "progress": (
                min(1.0, metrics["completed_rows"] / run["target_rows"])
                if run["target_rows"]
                else 0.0
            ),
        }

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT run_id
                FROM benchmark_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [
            summary
            for row in rows
            if (summary := self.run_summary(str(row["run_id"]))) is not None
        ]

    def results(self, run_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT *
                FROM benchmark_results
                WHERE run_id = ?
                ORDER BY row_id DESC
                LIMIT ?
                """,
                (run_id, max(1, min(limit, 1000))),
            ).fetchall()
        return [dict(row) for row in rows]

    def result_detail(self, run_id: str, row_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT *
                FROM benchmark_results
                WHERE run_id = ? AND row_id = ?
                """,
                (run_id, row_id),
            ).fetchone()
        return dict(row) if row else None

    def export_run(self, run_id: str) -> str:
        summary = self.run_summary(run_id)
        results = self.results(run_id, limit=1000)
        return json.dumps(
            {"summary": summary, "results": results},
            ensure_ascii=False,
            indent=2,
        )
