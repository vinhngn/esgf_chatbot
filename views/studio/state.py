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
from services.evaluation import BenchmarkManager


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
    runtime = RuntimeManager(
        project_root=Path(__file__).resolve().parents[2],
        secrets=secrets,
    )
    benchmarks = BenchmarkManager(
        framework_root=Path(
            os.getenv(
                "T2C_FRAMEWORK_ROOT",
                r"D:\Agent\t2c_eval_framework",
            )
        )
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
    if runtime_is_current and hasattr(state, "benchmarks"):
        return state
    state.runtime.stop_all()
    if hasattr(state, "benchmarks"):
        state.benchmarks.stop_all()
    get_studio_state.clear()
    return get_studio_state()


def runtime_api_ready(state: StudioState) -> bool:
    if not state.runtime.api_running:
        return False
    try:
        with urllib.request.urlopen(f"{state.runtime.api_url}/health", timeout=1) as response:
            if response.status != 200:
                return False
            health = json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, ValueError, urllib.error.URLError):
        return False
    return health.get("status") == "ok"


def select_connection(state: StudioState, connection_id: str) -> None:
    state.store.active_connection_id = connection_id
