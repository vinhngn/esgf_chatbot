from __future__ import annotations

from pathlib import Path

from services.control_center.connection_service import ConnectionSession
from services.control_center.models import DatabaseConnection
from services.control_center.secrets import SecretStore
from services.profile_analyzer import build_profile_from_neo4j


def build_connection_profile(
    *,
    project_root: Path,
    connection: DatabaseConnection,
    secrets: SecretStore,
    session: ConnectionSession | None = None,
) -> tuple[Path, dict]:
    owns_session = session is None
    active_session = session or ConnectionSession(connection, secrets)
    if owns_session:
        active_session.open()

    output = connection.profile_path(project_root)
    try:
        profile = build_profile_from_neo4j(
            uri=connection.runtime_uri,
            username=connection.username,
            password=active_session.database_password,
            database=connection.database,
            profile_name=connection.profile_name,
            output_path=output,
            sample_limit=connection.profile.sample_limit,
            max_hops=connection.profile.max_hops,
            value_limit=connection.profile.value_limit,
            include_value_profile=connection.profile.include_values,
        )
        return output, profile
    finally:
        if owns_session:
            active_session.close()
