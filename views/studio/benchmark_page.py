from __future__ import annotations

from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from services.control_center.models import LlmProvider
from services.evaluation.models import BenchmarkConfig, DatabaseTarget, RunStatus
from services.evaluation.runner import discover_datasets
from views.studio.state import StudioState, runtime_api_ready


def _normalized_database_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _matches_active_database(
    dataset: str,
    *,
    profile_name: str,
    physical_database: str,
) -> bool:
    expected = _normalized_database_name(dataset)
    return expected in {
        _normalized_database_name(profile_name),
        _normalized_database_name(physical_database),
    }


def _format_score(value: float | None) -> str:
    return f"{100 * float(value or 0):.1f}%"


def _recommended_workers(state: StudioState) -> int:
    if state.model_configuration.primary_provider == LlmProvider.LOCAL:
        return 1
    return 2


def _current_target(state: StudioState) -> DatabaseTarget | None:
    connection = state.runtime.connection
    session = state.runtime.session
    if connection is None or session is None:
        return None
    return DatabaseTarget(
        uri=connection.runtime_uri,
        username=connection.username,
        password=session.database_password,
        database=connection.database,
        logical_database=connection.profile_name,
    )


def _resume_run(state: StudioState, run_id: str, summary: dict) -> None:
    if not runtime_api_ready(state):
        st.error("Start the T2C API from Connection before resuming.")
        return
    target = _current_target(state)
    if target is None:
        st.error("No active Neo4j connection.")
        return
    if not _matches_active_database(
        str(summary["dataset"]),
        profile_name=target.logical_database,
        physical_database=target.database,
    ):
        st.error(
            f"Connect to the '{summary['dataset']}' database before resuming this run."
        )
        return
    try:
        state.benchmarks.resume(
            run_id,
            target,
            endpoint=f"{state.runtime.api_url}/api/text2cypher",
        )
    except Exception as exc:
        st.error(str(exc))
        return
    st.session_state["benchmark_run_id"] = run_id
    st.rerun()


def _render_summary(state: StudioState, run_id: str) -> None:
    summary = state.benchmarks.store.run_summary(run_id)
    if summary is None:
        st.error("Benchmark run not found.")
        return

    status = str(summary["status"])
    completed = int(summary["completed_rows"] or 0)
    target = int(summary["target_rows"] or 0)
    st.progress(
        float(summary["progress"]),
        text=f"{status.upper()} | {completed}/{target}",
    )

    exact, strict, dice, speed = st.columns(4)
    exact.metric("Exact match", _format_score(summary["exact_match"]))
    strict.metric("Strict columns", _format_score(summary["strict_column_match"]))
    dice.metric("Dice", _format_score(summary["dice_score"]))
    speed.metric("Average row", f"{float(summary['avg_seconds'] or 0):.1f}s")

    if summary.get("error"):
        st.error(str(summary["error"]))
    managed_by_this_client = state.benchmarks.active_run_id == run_id
    if status == RunStatus.RUNNING.value:
        if managed_by_this_client:
            if st.button(
                "Stop benchmark",
                type="secondary",
            ):
                state.benchmarks.stop()
                st.rerun()
    resumable = status in {
        RunStatus.INTERRUPTED.value,
        RunStatus.CANCELLED.value,
        RunStatus.FAILED.value,
    }
    if (
        resumable
        and completed < target
        and st.button(
            "Resume benchmark",
            type="primary",
            width="stretch",
            disabled=bool(state.benchmarks.active_run_id),
            key=f"resume-{run_id}",
        )
    ):
        _resume_run(state, run_id, summary)

    results = state.benchmarks.store.results(run_id, limit=100)
    if not results:
        return
    table = [
        {
            "row": item["row_id"],
            "question": item["question"],
            "exact": item["exact_match"],
            "strict": item["strict_column_match"],
            "dice": item["dice_score"],
            "seconds": round(float(item["total_seconds"]), 2),
            "error": (
                item["row_error"]
                or item["api_error"]
                or item["generated_error"]
                or item["original_error"]
            ),
        }
        for item in results
    ]
    st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        column_config={
            "exact": st.column_config.NumberColumn(format="%.2f"),
            "strict": st.column_config.NumberColumn(format="%.2f"),
            "dice": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    row_ids = [int(item["row_id"]) for item in results]
    selected_row = st.selectbox("Inspect row", row_ids)
    detail = state.benchmarks.store.result_detail(run_id, selected_row)
    if detail:
        original, generated = st.columns(2)
        with original:
            st.caption("Gold Cypher")
            st.code(detail["original_cypher"], language="cypher")
        with generated:
            st.caption("Generated Cypher")
            st.code(detail["generated_cypher"] or "No query", language="cypher")
        error = (
            detail["row_error"]
            or detail["api_error"]
            or detail["generated_error"]
            or detail["original_error"]
        )
        if error:
            st.error(error)

    st.download_button(
        "Download run JSON",
        data=state.benchmarks.store.export_run(run_id),
        file_name=f"{summary['dataset']}-{run_id}.json",
        mime="application/json",
    )


@st.fragment(run_every=2)
def _live_summary(state: StudioState, run_id: str) -> None:
    _render_summary(state, run_id)


def _start_form(state: StudioState) -> None:
    framework_value = state.store.get_state(
        "t2c_framework_root",
        str(state.benchmarks.framework_root),
    )
    framework_root = Path(
        st.text_input("T2C framework root", framework_value)
    ).expanduser()
    input_directory = framework_root / "inputs"
    datasets = discover_datasets(input_directory)
    if not datasets:
        st.warning(f"No CSV datasets found in {input_directory}")
        return

    with st.form("benchmark-configuration"):
        selected_name = st.selectbox(
            "Dataset",
            [path.name for path in datasets],
        )
        first, second, third = st.columns(3)
        row_limit = first.number_input(
            "Rows",
            min_value=0,
            value=0,
            help="0 runs the full dataset.",
        )
        workers = second.number_input(
            "Workers",
            min_value=1,
            max_value=8,
            value=_recommended_workers(state),
            help=(
                "Adaptive default: 1 for a local model, 2 for OpenAI. "
                "Increase only when the provider has spare concurrency."
            ),
        )
        result_limit = third.number_input(
            "Result row cap",
            min_value=0,
            value=0,
            help="0 compares complete Neo4j result sets.",
        )
        with st.expander("Timeouts"):
            webhook_timeout = st.number_input(
                "T2C API timeout",
                min_value=1,
                max_value=900,
                value=180,
            )
            query_timeout = st.number_input(
                "Neo4j query timeout",
                min_value=1,
                max_value=600,
                value=30,
            )
        start = st.form_submit_button(
            "Start benchmark",
            type="primary",
            width="stretch",
            disabled=bool(state.benchmarks.active_run_id),
        )

    if not start:
        return
    if not runtime_api_ready(state):
        st.error("Start the T2C API from Connection first.")
        return
    connection = state.runtime.connection
    session = state.runtime.session
    if connection is None or session is None:
        st.error("No active Neo4j connection.")
        return

    selected_file = next(path for path in datasets if path.name == selected_name)
    if not _matches_active_database(
        selected_file.stem,
        profile_name=connection.profile_name,
        physical_database=connection.database,
    ):
        st.error(
            f"Dataset '{selected_file.stem}' does not match the active "
            f"database profile '{connection.profile_name}'. Connect to the "
            "matching database before starting the benchmark."
        )
        return
    try:
        config = BenchmarkConfig(
            dataset=selected_file.stem,
            input_file=selected_file,
            endpoint=f"{state.runtime.api_url}/api/text2cypher",
            row_limit=int(row_limit),
            workers=int(workers),
            webhook_timeout=float(webhook_timeout),
            query_timeout=float(query_timeout),
            result_limit=int(result_limit),
        )
    except ValidationError as exc:
        st.error(str(exc))
        return

    state.store.set_state("t2c_framework_root", str(framework_root.resolve()))
    state.benchmarks.framework_root = framework_root.resolve()
    target = _current_target(state)
    if target is None:
        st.error("No active Neo4j connection.")
        return
    try:
        run_id = state.benchmarks.start(config, target)
    except Exception as exc:
        st.error(str(exc))
        return
    st.session_state["benchmark_run_id"] = run_id
    st.rerun()


def render(state: StudioState) -> None:
    st.header("T2C Benchmark")
    _start_form(state)

    runs = state.benchmarks.store.recent_runs(limit=20)
    if not runs:
        return
    active = state.benchmarks.active_run_id
    run_ids = [str(item["run_id"]) for item in runs]
    preferred = active or st.session_state.get("benchmark_run_id", "")
    selected = st.selectbox(
        "Run",
        run_ids,
        index=run_ids.index(preferred) if preferred in run_ids else 0,
        format_func=lambda run_id: next(
            (
                f"{item['dataset']} · {item['status']} · {run_id}"
                for item in runs
                if item["run_id"] == run_id
            ),
            run_id,
        ),
    )
    _live_summary(state, selected)
