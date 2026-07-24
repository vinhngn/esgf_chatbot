from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load T2C module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    logging.getLogger(name).setLevel(logging.ERROR)
    return module


@lru_cache(maxsize=4)
def _load_scorer_classes(framework_root: str) -> tuple[type, type]:
    root = Path(framework_root)
    comparator_module = _load_module(
        "_t2c_result_comparator",
        root / "services" / "result_comparator.py",
    )
    metrics_module = _load_module(
        "_t2c_metrics_calculator",
        root / "services" / "metrics_calculator.py",
    )
    return comparator_module.ResultComparator, metrics_module.MetricsCalculator


@dataclass
class _ScoringRow:
    original_cypher: str
    generated_cypher: str
    original_results: list[dict[str, Any]]
    generated_results: list[dict[str, Any]]
    comparison_table: list[dict[str, Any]] | None = None
    exact_match: float | None = None
    strict_column_match: float | None = None
    soft_match: float | None = None
    dice_score: float | None = None
    generated_only_columns: list[str] = field(default_factory=list)
    original_only_columns: list[str] = field(default_factory=list)


class T2CScorer:
    """Adapter around the upstream T2C comparison and metric contracts."""

    def __init__(self, framework_root: Path) -> None:
        comparator_class, metrics_class = _load_scorer_classes(
            str(framework_root.resolve())
        )
        self.comparator = comparator_class(column_matcher=None)
        self.metrics = metrics_class()

    def score(
        self,
        *,
        original_cypher: str,
        generated_cypher: str,
        original_results: list[dict[str, Any]],
        generated_results: list[dict[str, Any]],
    ) -> dict[str, float]:
        row = _ScoringRow(
            original_cypher=original_cypher,
            generated_cypher=generated_cypher,
            original_results=original_results,
            generated_results=generated_results,
        )
        self.comparator.build_table(row)
        self.metrics.compute_exact_match(row)
        self.metrics.compute_strict_column_match(row)
        self.metrics.compute_soft_match(row)
        self.metrics.compute_dice_score(row)
        return {
            "exact_match": float(row.exact_match or 0),
            "strict_column_match": float(row.strict_column_match or 0),
            "soft_match": float(row.soft_match or 0),
            "dice_score": float(row.dice_score or 0),
        }
