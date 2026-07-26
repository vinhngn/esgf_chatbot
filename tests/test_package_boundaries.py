from __future__ import annotations

import importlib
import sys
from ast import Import, ImportFrom, parse
from pathlib import Path


def test_profile_paths_do_not_import_application_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("neo4j_t2c.profiles.paths", None)
    sys.modules.pop("config", None)

    paths = importlib.import_module("neo4j_t2c.profiles.paths")

    assert "config" not in sys.modules
    assert paths.profile_path("Movies") == tmp_path / "generated_profiles/movies_profile.json"


def test_legacy_and_public_trace_stores_share_state(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_TRACE_CAPTURE", "1")
    from neo4j_t2c.observability import clear_traces, get_trace, record_trace_event
    from services.observability.trace_store import get_trace as legacy_get_trace

    clear_traces()
    record_trace_event("boundary-test", "TEST", "shared")

    assert legacy_get_trace("boundary-test") == get_trace("boundary-test")
    clear_traces()


def test_package_has_no_static_imports_from_application_layers() -> None:
    package_root = Path(__file__).parents[1] / "src/neo4j_t2c"
    forbidden = {"config", "constants", "models", "services", "templates", "utils", "views"}
    violations: list[str] = []

    for path in package_root.rglob("*.py"):
        tree = parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in tree.body:
            module = ""
            if isinstance(node, ImportFrom):
                module = node.module or ""
            elif isinstance(node, Import):
                module = node.names[0].name if node.names else ""
            if module.split(".", 1)[0] in forbidden:
                violations.append(f"{path.relative_to(package_root)}:{node.lineno} {module}")

    assert violations == []
