from __future__ import annotations

import threading
from pathlib import Path
from uuid import uuid4

from services.evaluation.models import BenchmarkConfig, DatabaseTarget, RunStatus
from services.evaluation.runner import count_cases, run_benchmark
from services.evaluation.store import BenchmarkStore


class BenchmarkManager:
    def __init__(
        self,
        *,
        framework_root: Path,
        store: BenchmarkStore | None = None,
    ) -> None:
        self.framework_root = framework_root.resolve()
        self.store = store or BenchmarkStore()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._active_run_id = ""

    @property
    def active_run_id(self) -> str:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self._active_run_id
            return ""

    def start(
        self,
        config: BenchmarkConfig,
        target: DatabaseTarget,
    ) -> str:
        with self._lock:
            if self.active_run_id:
                raise RuntimeError("A benchmark is already running.")
            if not self.framework_root.is_dir():
                raise FileNotFoundError(
                    f"T2C framework not found: {self.framework_root}"
                )
            run_id = uuid4().hex[:12]
            target_rows = count_cases(config.input_file, config.row_limit)
            self.store.create_run(
                run_id,
                config,
                logical_database=target.logical_database,
                physical_database=target.database,
                target_rows=target_rows,
            )
            self._start_thread(run_id, config, target)
            return run_id

    def resume(
        self,
        run_id: str,
        target: DatabaseTarget,
        *,
        endpoint: str | None = None,
    ) -> str:
        """Resume an interrupted run while preserving completed row results."""
        with self._lock:
            if self.active_run_id:
                raise RuntimeError("A benchmark is already running.")
            if not self.framework_root.is_dir():
                raise FileNotFoundError(
                    f"T2C framework not found: {self.framework_root}"
                )
            summary = self.store.run_summary(run_id)
            if summary is None:
                raise KeyError(f"Benchmark run not found: {run_id}")
            resumable = {
                RunStatus.INTERRUPTED.value,
                RunStatus.CANCELLED.value,
                RunStatus.FAILED.value,
            }
            if str(summary["status"]) not in resumable:
                raise RuntimeError(
                    f"Benchmark status '{summary['status']}' cannot be resumed."
                )
            if int(summary["completed_rows"] or 0) >= int(summary["target_rows"] or 0):
                raise RuntimeError("Benchmark already contains all target rows.")
            if target.logical_database.casefold() != str(
                summary["logical_database"]
            ).casefold():
                raise RuntimeError(
                    "Active logical database does not match the benchmark run."
                )
            if target.database.casefold() != str(summary["physical_database"]).casefold():
                raise RuntimeError(
                    "Active physical database does not match the benchmark run."
                )

            config = self.store.benchmark_config(run_id)
            if endpoint:
                config = config.model_copy(update={"endpoint": endpoint})
            self.store.prepare_resume(run_id)
            self._start_thread(run_id, config, target)
            return run_id

    def _start_thread(
        self,
        run_id: str,
        config: BenchmarkConfig,
        target: DatabaseTarget,
    ) -> None:
        self._stop_event = threading.Event()
        self._active_run_id = run_id
        self._thread = threading.Thread(
            target=self._run,
            args=(run_id, config, target, self._stop_event),
            daemon=True,
            name=f"benchmark-{run_id}",
        )
        self._thread.start()

    def _run(
        self,
        run_id: str,
        config: BenchmarkConfig,
        target: DatabaseTarget,
        stop_event: threading.Event,
    ) -> None:
        try:
            run_benchmark(
                run_id=run_id,
                config=config,
                target=target,
                framework_root=self.framework_root,
                store=self.store,
                stop_event=stop_event,
            )
        finally:
            with self._lock:
                if self._active_run_id == run_id:
                    self._active_run_id = ""

    def stop(self) -> None:
        with self._lock:
            if self._stop_event:
                self._stop_event.set()

    def stop_all(self) -> None:
        self.stop()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
