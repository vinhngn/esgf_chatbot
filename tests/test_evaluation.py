from __future__ import annotations

from pathlib import Path

from services.evaluation.framework_adapter import T2CScorer
from services.evaluation.manager import BenchmarkManager
from services.evaluation.models import (
    BenchmarkConfig,
    DatabaseTarget,
    RunStatus,
)
from services.evaluation.runner import _cases, _evaluate_case, count_cases
from services.evaluation.store import BenchmarkStore


def _write_framework(root: Path) -> None:
    services = root / "services"
    services.mkdir(parents=True)
    (services / "result_comparator.py").write_text(
        """
class ResultComparator:
    def __init__(self, column_matcher=None):
        pass

    def build_table(self, row):
        row.comparison_table = [{
            "col_name": "value",
            "original_result_values": [item["value"] for item in row.original_results],
            "generated_result_values": [item["value"] for item in row.generated_results],
        }]
        row.generated_only_columns = []
""",
        encoding="utf-8",
    )
    (services / "metrics_calculator.py").write_text(
        """
class MetricsCalculator:
    def compute_exact_match(self, row):
        item = row.comparison_table[0]
        row.exact_match = int(
            item["original_result_values"] == item["generated_result_values"]
        )

    def compute_strict_column_match(self, row):
        row.strict_column_match = row.exact_match

    def compute_soft_match(self, row):
        row.soft_match = float(row.exact_match)

    def compute_dice_score(self, row):
        row.dice_score = float(row.exact_match)
""",
        encoding="utf-8",
    )


def _dataset(path: Path) -> Path:
    path.write_text(
        "question,schema,cypher,database_name\n"
        'List values.,(:Value),MATCH (n:Value) RETURN n.value,test\n',
        encoding="utf-8",
    )
    return path


def _multi_row_dataset(path: Path) -> Path:
    path.write_text(
        "question,schema,cypher,database_name\n"
        'One.,(:Value),RETURN 1 AS value,test\n'
        'Two.,(:Value),RETURN 2 AS value,test\n'
        'Three.,(:Value),RETURN 3 AS value,test\n',
        encoding="utf-8",
    )
    return path


def _result(row_id: int) -> dict:
    return {
        "row_id": row_id,
        "question": f"Row {row_id}",
        "schema_text": "",
        "original_cypher": f"RETURN {row_id}",
        "generated_cypher": f"RETURN {row_id}",
        "original_results_json": "[]",
        "generated_results_json": "[]",
        "original_row_count": 0,
        "generated_row_count": 0,
        "exact_match": 1.0,
        "strict_column_match": 1.0,
        "soft_match": 1.0,
        "dice_score": 1.0,
        "api_error": "",
        "original_error": "",
        "generated_error": "",
        "row_error": "",
        "original_seconds": 0.1,
        "webhook_seconds": 0.2,
        "generated_seconds": 0.1,
        "total_seconds": 0.4,
        "completed_at": "now",
    }


def test_t2c_scorer_loads_framework_without_package_name_collisions(
    tmp_path: Path,
) -> None:
    framework = tmp_path / "t2c"
    _write_framework(framework)
    scorer = T2CScorer(framework)

    scores = scorer.score(
        original_cypher="RETURN 1",
        generated_cypher="RETURN 1",
        original_results=[{"value": 1}],
        generated_results=[{"value": 1}],
    )

    assert scores == {
        "exact_match": 1.0,
        "strict_column_match": 1.0,
        "soft_match": 1.0,
        "dice_score": 1.0,
    }


def test_benchmark_store_persists_incremental_summary(tmp_path: Path) -> None:
    input_file = _dataset(tmp_path / "sample.csv")
    config = BenchmarkConfig(dataset="sample", input_file=input_file)
    store = BenchmarkStore(tmp_path / "results.sqlite3")
    store.create_run(
        "run-1",
        config,
        logical_database="sample",
        physical_database="neo4j",
        target_rows=1,
    )
    store.save_result(
        "run-1",
        _result(1),
    )

    summary = store.run_summary("run-1")

    assert summary is not None
    assert summary["completed_rows"] == 1
    assert summary["progress"] == 1.0
    assert summary["exact_match"] == 1.0


def test_resume_preserves_completed_rows_and_skips_them(
    monkeypatch,
    tmp_path: Path,
) -> None:
    framework = tmp_path / "t2c"
    framework.mkdir()
    input_file = _multi_row_dataset(tmp_path / "sample.csv")
    config = BenchmarkConfig(
        dataset="sample",
        input_file=input_file,
        row_limit=0,
        workers=2,
        result_limit=0,
    )
    store = BenchmarkStore(tmp_path / "results.sqlite3")
    store.create_run(
        "run-resume",
        config,
        logical_database="sample",
        physical_database="neo4j",
        target_rows=3,
    )
    store.save_result("run-resume", _result(1))
    store.update_run("run-resume", RunStatus.INTERRUPTED, error="stopped")

    captured: dict = {}

    def fake_run_benchmark(**kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        "services.evaluation.manager.run_benchmark",
        fake_run_benchmark,
    )
    manager = BenchmarkManager(framework_root=framework, store=store)
    manager.resume(
        "run-resume",
        DatabaseTarget(
            uri="bolt://localhost:7687",
            username="neo4j",
            password="secret",
            database="neo4j",
            logical_database="sample",
        ),
    )
    assert manager._thread is not None
    manager._thread.join(timeout=2)

    restored = store.benchmark_config("run-resume")
    remaining = list(_cases(restored, skip_rows=store.completed_row_ids("run-resume")))
    summary = store.run_summary("run-resume")

    assert captured["run_id"] == "run-resume"
    assert [row_id for row_id, _ in remaining] == [2, 3]
    assert summary is not None
    assert summary["completed_rows"] == 1
    assert summary["status"] == RunStatus.PENDING.value
    assert summary["error"] == ""


def test_api_failure_is_scored_as_failure_not_empty_result_match(
    monkeypatch,
    tmp_path: Path,
) -> None:
    input_file = _dataset(tmp_path / "sample.csv")
    config = BenchmarkConfig(dataset="sample", input_file=input_file)

    class FakeExecutor:
        def run(self, cypher: str):
            return ([{"value": 1}], "") if cypher else ([], "empty")

    class FakeScorer:
        def score(self, **kwargs):
            raise AssertionError("Scorer must not run after an API failure")

    monkeypatch.setattr(
        "services.evaluation.runner._generate",
        lambda config, raw: ("", "LLM unavailable"),
    )

    result = _evaluate_case(
        row_id=1,
        raw={
            "question": "List values.",
            "schema": "(:Value)",
            "cypher": "MATCH (n:Value) RETURN n.value",
        },
        config=config,
        executor=FakeExecutor(),
        scorer=FakeScorer(),
    )

    assert result["exact_match"] == 0.0
    assert result["api_error"] == "LLM unavailable"
    assert count_cases(input_file) == 1
