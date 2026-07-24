from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Callable

from services.control_center.connection_service import ConnectionSession
from services.control_center.model_settings import (
    LOCAL_API_KEY_SECRET,
    MODEL_SECRET_ID,
    OPENAI_API_KEY_SECRET,
    default_model_configuration,
)
from services.control_center.models import DatabaseConnection, LlmProvider, ModelConfiguration
from services.control_center.secrets import SecretStore

LogCallback = Callable[[str], None]


def is_t2c_api_health(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("status") == "ok"
        and isinstance(payload.get("database"), str)
        and isinstance(payload.get("physical_database"), str)
    )


class RuntimeManager:
    def __init__(
        self,
        project_root: Path,
        secrets: SecretStore,
        log: LogCallback | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.secrets = secrets
        self.logs: deque[str] = deque(maxlen=2_000)
        self._log_callback = log
        self._lock = threading.RLock()
        self.connection: DatabaseConnection | None = None
        self.session: ConnectionSession | None = None
        self.api_process: subprocess.Popen[str] | None = None
        self.api_port = 8954

    def log(self, message: str) -> None:
        self.logs.append(message)
        if self._log_callback:
            self._log_callback(message)

    @property
    def api_running(self) -> bool:
        return bool(self.api_process and self.api_process.poll() is None)

    @property
    def api_ready(self) -> bool:
        if not self.api_running:
            return False
        try:
            with urllib.request.urlopen(f"{self.api_url}/health", timeout=1) as response:
                if response.status != 200:
                    return False
                health = json.loads(response.read().decode("utf-8", errors="replace"))
        except (OSError, ValueError, urllib.error.URLError):
            return False
        return health.get("status") == "ok"

    def connect(self, connection: DatabaseConnection) -> str:
        with self._lock:
            return self._connect(connection)

    def _connect(self, connection: DatabaseConnection) -> str:
        if self.connection and self.connection.id != connection.id:
            self.stop_all()
        if self.session is None:
            session = ConnectionSession(connection, self.secrets)
            session.open()
            self.connection = connection
            self.session = session
        return connection.runtime_uri

    def _environment(
        self,
        connection: DatabaseConnection,
        model_configuration: ModelConfiguration | None = None,
    ) -> dict[str, str]:
        session = self.session or ConnectionSession(connection, self.secrets)
        environment = os.environ.copy()
        model_configuration = model_configuration or default_model_configuration()
        openai_key = (
            self.secrets.get(MODEL_SECRET_ID, OPENAI_API_KEY_SECRET)
            or environment.get("OPENAI_API_KEY", "")
        )
        local_key = (
            self.secrets.get(MODEL_SECRET_ID, LOCAL_API_KEY_SECRET)
            or environment.get("LOCAL_LLM_API_KEY", "local")
        )
        environment.update(
            {
                "NEO4J_URI": connection.runtime_uri,
                "NEO4J_USERNAME": connection.username,
                "NEO4J_PASSWORD": session.database_password,
                "NEO4J_DATABASE": connection.database,
                "T2C_PROFILE_DATABASE": connection.profile_name,
                "T2C_PROFILE_DIR": str(self.project_root / "generated_profiles"),
                "FLASK_HOST": "127.0.0.1",
                "FLASK_PORT": str(self.api_port),
                "SERVER_URL": "http://127.0.0.1",
                "ESGF_START_EMBEDDED_FLASK": "0",
                "ENABLE_TRACE_CAPTURE": "1",
                "ENABLE_TRACE_API": "1",
                "API_LOG_LEVEL": environment.get("API_LOG_LEVEL", "ERROR"),
                "OPENAI_API_KEY": openai_key,
                "OPENAI_BASE_URL": model_configuration.openai_base_url,
                "OPENAI_MODEL": model_configuration.openai_model,
                "OPENAI_REQUEST_TIMEOUT": str(model_configuration.openai_timeout),
                "LOCAL_LLM_API_KEY": local_key,
                "LOCAL_LLM_BASE_URL": model_configuration.local_base_url,
                "LOCAL_LLM_MODEL": model_configuration.local_model,
                "LOCAL_LLM_REQUEST_TIMEOUT": str(model_configuration.local_timeout),
                "LLM_LOCAL_FIRST": str(
                    model_configuration.primary_provider == LlmProvider.LOCAL
                ).lower(),
                "LLM_FALLBACK_ENABLED": str(
                    model_configuration.fallback_enabled
                ).lower(),
                "LLM_PROVIDER_MAX_RETRIES": str(model_configuration.max_retries),
                "PYTHONIOENCODING": "utf-8",
            }
        )
        return environment

    def _spawn(self, command: list[str], environment: dict[str, str], name: str) -> subprocess.Popen[str]:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            cwd=self.project_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation_flags,
        )

        def stream_output() -> None:
            if process.stdout is None:
                return
            for line in process.stdout:
                self.log(f"[{name}] {line.rstrip()}")

        threading.Thread(
            target=stream_output,
            daemon=True,
            name=f"{name}-output",
        ).start()
        return process

    def start_api(
        self,
        connection: DatabaseConnection,
        model_configuration: ModelConfiguration | None = None,
    ) -> str:
        with self._lock:
            self._connect(connection)
            if self.api_running:
                return self.api_url
            self._require_available_port(self.api_port, "T2C API")
            environment = self._environment(connection, model_configuration)
            self.api_process = self._spawn(
                [sys.executable, "-u", "views/flask_api.py"],
                environment,
                "api",
            )
            try:
                self._wait_for_health(connection)
            except Exception:
                self.stop_api()
                raise
            self.log(
                "T2C API ready | "
                f"database={connection.profile_name} | "
                f"model={(model_configuration or default_model_configuration()).primary_provider.value} | "
                f"{self.api_url}"
            )
            return self.api_url

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    @property
    def api_port_in_use(self) -> bool:
        try:
            with socket.create_connection(
                ("127.0.0.1", self.api_port),
                timeout=0.5,
            ):
                return True
        except OSError:
            return False

    def api_health_on_port(self) -> dict:
        try:
            with urllib.request.urlopen(f"{self.api_url}/health", timeout=1) as response:
                if response.status != 200:
                    return {}
                payload = json.loads(
                    response.read().decode("utf-8", errors="replace")
                )
        except (OSError, ValueError, urllib.error.URLError):
            return {}
        return payload if is_t2c_api_health(payload) else {}

    def stop_api_on_port(self) -> None:
        with self._lock:
            if self.api_running:
                self.stop_api()
                return
            if not self.api_port_in_use:
                return
            if not self.api_health_on_port():
                raise RuntimeError(
                    f"Port {self.api_port} is not an ESGF T2C API. "
                    "It was left untouched."
                )

            npx = shutil.which("npx.cmd") or shutil.which("npx")
            if not npx:
                raise RuntimeError(
                    "npx is required to stop a detached T2C API process."
                )
            creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            completed = subprocess.run(
                [npx, "--yes", "kill-port", str(self.api_port)],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=creation_flags,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()
                raise RuntimeError(
                    f"Could not stop T2C API on port {self.api_port}: {detail}"
                )

            deadline = time.monotonic() + 10
            while self.api_port_in_use and time.monotonic() < deadline:
                time.sleep(0.1)
            if self.api_port_in_use:
                raise RuntimeError(
                    f"T2C API did not release port {self.api_port}."
                )
            self.log(f"Stopped detached T2C API on port {self.api_port}")

    def _wait_for_health(
        self,
        connection: DatabaseConnection,
        timeout: float = 30,
    ) -> None:
        body = self._wait_for_url(
            f"{self.api_url}/health",
            timeout=timeout,
            process=self.api_process,
        )
        health = json.loads(body)
        if health.get("database") != connection.profile_name:
            raise RuntimeError(
                "API started with the wrong logical profile: "
                f"{health.get('database')!r}"
            )
        if health.get("physical_database") != connection.database.lower():
            raise RuntimeError(
                "API started with the wrong physical database: "
                f"{health.get('physical_database')!r}"
            )

    def _wait_for_url(
        self,
        url: str,
        timeout: float,
        process: subprocess.Popen[str] | None = None,
    ) -> str:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if process and process.poll() is not None:
                raise RuntimeError(
                    f"Service exited before becoming ready (exit code {process.returncode})"
                )
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status < 500:
                        return response.read().decode("utf-8", errors="replace")
            except (OSError, urllib.error.URLError) as exc:
                last_error = exc
                time.sleep(0.25)
        raise TimeoutError(f"Service did not become ready at {url}: {last_error}")

    @staticmethod
    def _require_available_port(port: int, service: str) -> None:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                pass
        except OSError:
            return
        raise RuntimeError(
            f"{service} port {port} is already in use. Stop the existing service first."
        )

    def stop_api(self) -> None:
        self._stop_process(self.api_process, "api")
        self.api_process = None

    def stop_all(self) -> None:
        with self._lock:
            self.stop_api()
            if self.session:
                self.session.close()
            self.session = None
            self.connection = None

    def _stop_process(self, process: subprocess.Popen[str] | None, name: str) -> None:
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        self.log(f"Stopped {name}")
