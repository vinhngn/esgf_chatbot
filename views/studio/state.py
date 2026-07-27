from __future__ import annotations

import atexit
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from services.control_center.models import DatabaseConnection, ModelConfiguration
from services.control_center.runtime import RuntimeManager
from services.control_center.secrets import KeyringSecretStore
from services.control_center.store import ConnectionStore
from services.evaluation import BenchmarkManager, BenchmarkStore


def default_t2c_framework_root(project_root: Path, store: ConnectionStore) -> Path:
    saved = store.get_state("t2c_framework_root")
    if saved:
        return Path(saved).expanduser().resolve()
    configured = os.getenv("T2C_FRAMEWORK_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return project_root.parent / "t2c_eval_framework"


@dataclass
class StudioState:
    store: ConnectionStore
    secrets: KeyringSecretStore
    runtime: RuntimeManager
    benchmarks: BenchmarkManager

    @property
    def active_connection(self) -> DatabaseConnection:
        connection_id = self.store.active_connection_id or "preset-movies"
        connection = self.store.get(connection_id)
        if connection is None:
            connection = self.store.list()[0]
            self.store.active_connection_id = connection.id
        return connection

    @property
    def model_configuration(self) -> ModelConfiguration:
        return self.store.get_model_configuration()


@st.cache_resource(show_spinner=False)
def get_studio_state() -> StudioState:
    store = ConnectionStore()
    secrets = KeyringSecretStore()
    project_root = Path(__file__).resolve().parents[2]
    runtime = RuntimeManager(
        project_root=project_root,
        secrets=secrets,
    )
    benchmarks = BenchmarkManager(
        framework_root=default_t2c_framework_root(project_root, store)
    )
    state = StudioState(
        store=store,
        secrets=secrets,
        runtime=runtime,
        benchmarks=benchmarks,
    )
    atexit.register(runtime.stop_all)
    atexit.register(benchmarks.stop_all)
    return state


def get_compatible_studio_state() -> StudioState:
    state = get_studio_state()
    runtime_is_current = all(
        hasattr(state.runtime, attribute)
        for attribute in (
            "api_ready",
            "api_port_in_use",
            "api_health_on_port",
            "stop_api_on_port",
        )
    )
    benchmarks = getattr(state, "benchmarks", None)
    benchmark_is_current = bool(
        benchmarks
        and hasattr(benchmarks, "resume")
        and hasattr(benchmarks.store, "benchmark_config")
        and hasattr(benchmarks.store, "completed_row_ids")
    )
    if runtime_is_current and benchmark_is_current:
        return state
    if runtime_is_current and benchmarks is not None:
        benchmarks.stop_all()
        store_path = getattr(benchmarks.store, "path", None)
        state.benchmarks = BenchmarkManager(
            framework_root=benchmarks.framework_root,
            store=BenchmarkStore(store_path),
        )
        return state
    state.runtime.stop_all()
    if benchmarks is not None:
        benchmarks.stop_all()
    get_studio_state.clear()
    return get_studio_state()


def runtime_matches_connection(
    health: dict,
    connection: DatabaseConnection,
) -> bool:
    return (
        str(health.get("database") or "").casefold()
        == connection.profile_name.casefold()
        and str(health.get("physical_database") or "").casefold()
        == connection.database.casefold()
    )


def runtime_api_ready(state: StudioState) -> bool:
    if not state.runtime.api_running:
        return False
    selected = state.active_connection
    if (
        state.runtime.connection is None
        or state.runtime.connection.id != selected.id
    ):
        return False
    try:
        with urllib.request.urlopen(f"{state.runtime.api_url}/health", timeout=1) as response:
            if response.status != 200:
                return False
            health = json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, ValueError, urllib.error.URLError):
        return False
    return health.get("status") == "ok" and runtime_matches_connection(
        health,
        selected,
    )


def select_connection(state: StudioState, connection_id: str) -> None:
    state.store.active_connection_id = connection_id
