from __future__ import annotations

import csv
import gc
import json
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Iterator

import requests
from neo4j import GraphDatabase

from services.evaluation.framework_adapter import T2CScorer
from services.evaluation.models import BenchmarkConfig, DatabaseTarget, RunStatus
from services.evaluation.store import BenchmarkStore


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _json(value: Any, max_chars: int) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if max_chars and len(text) > max_chars:
        omitted = len(text) - max_chars
        return f"{text[:max_chars]}... [TRUNCATED {omitted} chars]"
    return text


def _trim_process_memory() -> None:
    gc.collect()
    if os.name != "nt":
        return
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.kernel32.SetProcessWorkingSetSize(handle, -1, -1)
    except Exception:
        return


def count_cases(input_file: Path, row_limit: int = 0) -> int:
    csv.field_size_limit(2_147_483_647)
    with input_file.open("r", encoding="utf-8", newline="") as handle:
        count = sum(1 for _ in csv.DictReader(handle))
    return min(count, row_limit) if row_limit else count


def discover_datasets(input_directory: Path) -> list[Path]:
    if not input_directory.is_dir():
        return []
    return sorted(input_directory.glob("*.csv"), key=lambda path: path.stem.casefold())


class _QueryExecutor:
    def __init__(self, target: DatabaseTarget, config: BenchmarkConfig) -> None:
        self.target = target
        self.config = config
        self.driver = GraphDatabase.driver(
            target.uri,
            auth=(target.username, target.password),
            connection_timeout=10,
        )
        self.driver.verify_connectivity()
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        self.driver.close()

    def _cap_query(self, cypher: str) -> str:
        query = (cypher or "").strip().rstrip(";")
        if (
            not query
            or not self.config.result_limit
            or re.search(r"(?i)\bLIMIT\s+\d+\s*$", query)
        ):
            return query
        return f"{query} LIMIT {self.config.result_limit}"

    def run(self, cypher: str) -> tuple[list[dict[str, Any]], str]:
        query = self._cap_query(cypher)
        if not query:
            return [], "No Cypher query was generated."
        with self._lock:
            cached = self._cache.get(query)
        if cached is not None:
            return [dict(row) for row in cached], ""
        try:
            with self.driver.session(database=self.target.database) as session:
                with session.begin_transaction(
                    timeout=self.config.query_timeout
                ) as transaction:
                    rows = [record.data() for record in transaction.run(query)]
        except Exception as exc:
            return [], f"{type(exc).__name__}: {exc}"
        with self._lock:
            self._cache[query] = [dict(row) for row in rows]
        return rows, ""


def _cases(
    config: BenchmarkConfig,
    *,
    skip_rows: set[int] | None = None,
) -> Iterator[tuple[int, dict[str, str]]]:
    skipped = skip_rows or set()
    csv.field_size_limit(2_147_483_647)
    with config.input_file.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"question", "schema", "cypher"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Dataset is missing columns: {sorted(missing)}")
        for row_id, raw in enumerate(reader, start=1):
            if config.row_limit and row_id > config.row_limit:
                return
            if row_id in skipped:
                continue
            yield row_id, {key: value or "" for key, value in raw.items()}


def _generate(config: BenchmarkConfig, raw: dict[str, str]) -> tuple[str, str]:
    try:
        response = requests.post(
            config.endpoint,
            json={
                "question": raw["question"],
                "schema": raw["schema"],
            },
            timeout=config.webhook_timeout,
        )
        try:
            body = response.json()
        except ValueError:
            return "", f"HTTP {response.status_code}: non-JSON response"
        if not response.ok:
            return "", str(body.get("error") or f"HTTP {response.status_code}")
        return str(body.get("cypher_query") or ""), str(body.get("error") or "")
    except requests.RequestException as exc:
        return "", f"{type(exc).__name__}: {exc}"


def _evaluate_case(
    *,
    row_id: int,
    raw: dict[str, str],
    config: BenchmarkConfig,
    executor: _QueryExecutor,
    scorer: T2CScorer,
) -> dict[str, Any]:
    started = time.monotonic()
    original_seconds = webhook_seconds = generated_seconds = 0.0
    original_rows: list[dict[str, Any]] = []
    generated_rows: list[dict[str, Any]] = []
    generated_cypher = ""
    api_error = original_error = generated_error = row_error = ""
    scores: dict[str, float | None] = {
        "exact_match": None,
        "strict_column_match": None,
        "soft_match": None,
        "dice_score": None,
    }

    try:
        phase = time.monotonic()
        original_rows, original_error = executor.run(raw["cypher"])
        original_seconds = time.monotonic() - phase

        phase = time.monotonic()
        generated_cypher, api_error = _generate(config, raw)
        webhook_seconds = time.monotonic() - phase

        if generated_cypher:
            phase = time.monotonic()
            generated_rows, generated_error = executor.run(generated_cypher)
            generated_seconds = time.monotonic() - phase

        if original_error:
            row_error = f"Gold query failed: {original_error}"
        elif api_error or generated_error or not generated_cypher:
            scores = {name: 0.0 for name in scores}
        else:
            scores = scorer.score(
                original_cypher=raw["cypher"],
                generated_cypher=generated_cypher,
                original_results=original_rows,
                generated_results=generated_rows,
            )
    except Exception as exc:
        row_error = f"{type(exc).__name__}: {exc}"

    return {
        "row_id": row_id,
        "question": raw["question"],
        "schema_text": "",
        "original_cypher": raw["cypher"],
        "generated_cypher": generated_cypher,
        "original_results_json": _json(
            original_rows,
            config.result_json_max_chars,
        ),
        "generated_results_json": _json(
            generated_rows,
            config.result_json_max_chars,
        ),
        "original_row_count": len(original_rows),
        "generated_row_count": len(generated_rows),
        **scores,
        "api_error": api_error,
        "original_error": original_error,
        "generated_error": generated_error,
        "row_error": row_error,
        "original_seconds": original_seconds,
        "webhook_seconds": webhook_seconds,
        "generated_seconds": generated_seconds,
        "total_seconds": time.monotonic() - started,
        "completed_at": _now(),
    }


def run_benchmark(
    *,
    run_id: str,
    config: BenchmarkConfig,
    target: DatabaseTarget,
    framework_root: Path,
    store: BenchmarkStore,
    stop_event: threading.Event,
) -> None:
    store.update_run(run_id, RunStatus.RUNNING)
    executor: _QueryExecutor | None = None
    try:
        scorer = T2CScorer(framework_root)
        executor = _QueryExecutor(target, config)
        cases = iter(
            _cases(
                config,
                skip_rows=store.completed_row_ids(run_id),
            )
        )
        pending: set[Future] = set()

        with ThreadPoolExecutor(max_workers=config.workers) as pool:
            while not stop_event.is_set():
                while len(pending) < config.workers * 2:
                    try:
                        row_id, raw = next(cases)
                    except StopIteration:
                        break
                    pending.add(
                        pool.submit(
                            _evaluate_case,
                            row_id=row_id,
                            raw=raw,
                            config=config,
                            executor=executor,
                            scorer=scorer,
                        )
                    )
                if not pending:
                    break
                done, pending = wait(
                    pending,
                    timeout=0.5,
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    store.save_result(run_id, future.result())
                if stop_event.is_set():
                    for future in pending:
                        future.cancel()

            while pending and not stop_event.is_set():
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    store.save_result(run_id, future.result())

        final_status = (
            RunStatus.CANCELLED if stop_event.is_set() else RunStatus.COMPLETED
        )
        store.update_run(run_id, final_status)
    except Exception as exc:
        store.update_run(
            run_id,
            RunStatus.FAILED,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if executor is not None:
            executor.close()
        _trim_process_memory()
