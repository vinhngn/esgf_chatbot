from __future__ import annotations

from pathlib import Path

from neo4j_t2c.adapters import JsonProfileStore
from neo4j_t2c.profiles import merge_profile_with_live_schema
from services.control_center.connection_service import ConnectionSession
from services.control_center.models import DatabaseConnection
from services.control_center.secrets import SecretStore
from services.profile_analyzer import build_profile_from_csv, build_profile_from_neo4j


def build_connection_profile(
    *,
    project_root: Path,
    connection: DatabaseConnection,
    secrets: SecretStore,
    session: ConnectionSession | None = None,
    dataset_directory: Path | None = None,
) -> tuple[Path, dict]:
    owns_session = session is None
    active_session = session or ConnectionSession(connection, secrets)
    if owns_session:
        active_session.open()

    output = connection.profile_path(project_root)
    try:
        live_profile = build_profile_from_neo4j(
            uri=connection.runtime_uri,
            username=connection.username,
            password=active_session.database_password,
            database=connection.database,
            profile_name=connection.profile_name,
            sample_limit=connection.profile.sample_limit,
            max_hops=connection.profile.max_hops,
            value_limit=connection.profile.value_limit,
            include_value_profile=connection.profile.include_values,
        )
        if live_profile.get("database") != connection.profile_name.lower():
            raise ValueError("Profile builder returned the wrong logical database")
        if live_profile.get("physical_database") != connection.database.lower():
            raise ValueError("Profile builder returned the wrong physical database")
        store = JsonProfileStore(output.parent)
        learned_profile = None
        if dataset_directory is not None:
            dataset_path = dataset_directory / f"{connection.profile_name}.csv"
            if dataset_path.is_file():
                learned_profile = build_profile_from_csv(
                    dataset_path,
                    database=connection.profile_name,
                )
        if learned_profile is None and store.exists(connection.profile_name):
            learned_profile = store.load(connection.profile_name)
        profile = (
            merge_profile_with_live_schema(learned_profile, live_profile)
            if learned_profile is not None
            else live_profile
        )
        store.save(connection.profile_name, profile)
        return output, store.load(connection.profile_name)
    finally:
        if owns_session:
            active_session.close()
