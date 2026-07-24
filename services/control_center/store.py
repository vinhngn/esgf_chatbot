from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from services.control_center.model_settings import default_model_configuration
from services.control_center.models import DatabaseConnection, ModelConfiguration
from services.control_center.presets import built_in_connections


def default_client_home() -> Path:
    configured = os.getenv("ESGF_CLIENT_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    appdata = os.getenv("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / "ESGF Text2Cypher Studio"


class ConnectionStore:
    def __init__(self, path: str | Path | None = None) -> None:
        home = default_client_home()
        self.path = Path(path) if path else home / "client.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._install_presets()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS database_connections (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    built_in INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS client_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def _install_presets(self) -> None:
        with self._connect() as db:
            for item in built_in_connections():
                db.execute(
                    """
                    INSERT OR IGNORE INTO database_connections
                        (id, payload, built_in, updated_at)
                    VALUES (?, ?, 1, ?)
                    """,
                    (
                        item.id,
                        item.model_dump_json(),
                        datetime.now(UTC).isoformat(),
                    ),
                )

    def list(self) -> list[DatabaseConnection]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT payload
                FROM database_connections
                """
            ).fetchall()
        items = [DatabaseConnection.model_validate_json(row["payload"]) for row in rows]
        preset_order = {
            item.id: index for index, item in enumerate(built_in_connections())
        }
        return sorted(
            items,
            key=lambda item: (
                0 if item.id in preset_order else 1,
                preset_order.get(item.id, 0),
                item.name.casefold(),
            ),
        )

    def get(self, connection_id: str) -> DatabaseConnection | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM database_connections WHERE id = ?",
                (connection_id,),
            ).fetchone()
        return DatabaseConnection.model_validate_json(row["payload"]) if row else None

    def save(self, item: DatabaseConnection) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO database_connections (id, payload, built_in, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload = excluded.payload,
                    built_in = excluded.built_in,
                    updated_at = excluded.updated_at
                """,
                (
                    item.id,
                    item.model_dump_json(),
                    int(item.built_in),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def delete(self, connection_id: str) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT built_in FROM database_connections WHERE id = ?",
                (connection_id,),
            ).fetchone()
            if row and not row["built_in"]:
                db.execute("DELETE FROM database_connections WHERE id = ?", (connection_id,))
        if self.active_connection_id == connection_id:
            self.active_connection_id = ""

    def get_state(self, key: str, default: str = "") -> str:
        with self._connect() as db:
            row = db.execute("SELECT value FROM client_state WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO client_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_model_configuration(self) -> ModelConfiguration:
        payload = self.get_state("model_configuration")
        if not payload:
            return default_model_configuration()
        return ModelConfiguration.model_validate_json(payload)

    def save_model_configuration(self, configuration: ModelConfiguration) -> None:
        self.set_state("model_configuration", configuration.model_dump_json())

    @property
    def active_connection_id(self) -> str:
        return self.get_state("active_connection_id")

    @active_connection_id.setter
    def active_connection_id(self, value: str) -> None:
        self.set_state("active_connection_id", value)
