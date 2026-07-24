from __future__ import annotations

import streamlit as st
from pydantic import ValidationError

from services.control_center.connection_service import suggested_database_password
from services.control_center.models import ConnectionKind, DatabaseConnection, SshTunnel
from services.control_center.profile_service import build_connection_profile
from views.studio.state import StudioState


def _connection_from_form(
    current: DatabaseConnection,
    values: dict,
) -> DatabaseConnection:
    connection_kind = ConnectionKind(values["kind"])
    ssh = None
    if connection_kind == ConnectionKind.SSH_TUNNEL:
        ssh = SshTunnel(
            jump_host=values["jump_host"],
            jump_port=values["jump_port"],
            ssh_username=values["ssh_username"],
            target_host=values["target_host"],
            remote_port=values["remote_port"],
            local_port=values["local_port"],
        )
    return DatabaseConnection(
        id=current.id,
        name=values["name"],
        profile_name=values["profile_name"],
        kind=connection_kind,
        uri=values["uri"],
        database=values["database"],
        username=values["username"],
        ssh=ssh,
        profile=current.profile,
        built_in=current.built_in,
    )


def _connection_form(
    state: StudioState,
    connection: DatabaseConnection,
) -> tuple[str, dict]:
    with st.form(f"connection-{connection.id}"):
        first, second = st.columns(2)
        with first:
            name = st.text_input("Display name", connection.name)
            profile_name = st.text_input("Profile name", connection.profile_name)
            kinds = [item.value for item in ConnectionKind]
            kind = st.selectbox(
                "Protocol",
                kinds,
                index=kinds.index(connection.kind.value),
                format_func=lambda value: {
                    "direct": "Direct Neo4j",
                    "ssh_tunnel": "SSH tunnel",
                }[value],
            )
            uri = st.text_input("Neo4j URI", connection.uri)
        with second:
            database = st.text_input("Physical database", connection.database)
            username = st.text_input("Neo4j username", connection.username)
            database_password = st.text_input(
                "Neo4j password",
                value=state.secrets.get(connection.id, "database_password")
                or suggested_database_password(connection),
                type="password",
            )

        ssh_defaults = connection.ssh
        if kind == ConnectionKind.SSH_TUNNEL.value:
            st.subheader("SSH tunnel")
            ssh_one, ssh_two, ssh_three = st.columns(3)
            with ssh_one:
                jump_host = st.text_input(
                    "Jump host",
                    ssh_defaults.jump_host if ssh_defaults else "",
                )
                jump_port = st.number_input(
                    "Jump port",
                    min_value=1,
                    max_value=65535,
                    value=ssh_defaults.jump_port if ssh_defaults else 22,
                )
            with ssh_two:
                ssh_username = st.text_input(
                    "SSH username",
                    ssh_defaults.ssh_username if ssh_defaults else "",
                )
                ssh_password = st.text_input(
                    "SSH password",
                    value=state.secrets.get(connection.id, "ssh_password"),
                    type="password",
                )
            with ssh_three:
                target_host = st.text_input(
                    "Target host",
                    ssh_defaults.target_host if ssh_defaults else "",
                )
                remote_port = st.number_input(
                    "Remote Neo4j port",
                    min_value=1,
                    max_value=65535,
                    value=ssh_defaults.remote_port if ssh_defaults else 7687,
                )
                local_port = st.number_input(
                    "Local port",
                    min_value=1,
                    max_value=65535,
                    value=ssh_defaults.local_port if ssh_defaults else 7687,
                )
        else:
            jump_host = target_host = ssh_username = ssh_password = ""
            jump_port = 22
            remote_port = local_port = 7687
        save_col, start_col = st.columns([1, 2])
        save = save_col.form_submit_button("Save", use_container_width=True)
        start = start_col.form_submit_button(
            "Save and start T2C API",
            type="primary",
            use_container_width=True,
        )

    action = "start" if start else "save" if save else ""
    return action, {
        "name": name,
        "profile_name": profile_name,
        "kind": kind,
        "uri": uri,
        "database": database,
        "username": username,
        "database_password": database_password,
        "jump_host": jump_host,
        "jump_port": int(jump_port),
        "ssh_username": ssh_username,
        "ssh_password": ssh_password,
        "target_host": target_host,
        "remote_port": int(remote_port),
        "local_port": int(local_port),
    }


def render(state: StudioState) -> None:
    connection = state.active_connection
    st.header("Connection")
    action, values = _connection_form(state, connection)
    if action:
        try:
            updated = _connection_from_form(connection, values)
        except ValidationError as exc:
            st.error(str(exc))
        else:
            state.store.save(updated)
            state.secrets.set(
                updated.id,
                "database_password",
                values["database_password"],
            )
            state.secrets.set(updated.id, "ssh_password", values["ssh_password"])
            if action == "start":
                try:
                    with st.spinner("Connecting to Neo4j and starting the API..."):
                        state.runtime.stop_all()
                        if state.runtime.api_port_in_use:
                            state.runtime.stop_api_on_port()
                        state.runtime.connect(updated)
                        if not updated.profile_path(
                            state.runtime.project_root
                        ).exists():
                            build_connection_profile(
                                project_root=state.runtime.project_root,
                                connection=updated,
                                secrets=state.secrets,
                                session=state.runtime.session,
                            )
                        state.runtime.start_api(
                            updated,
                            state.model_configuration,
                        )
                except Exception as exc:
                    state.runtime.stop_all()
                    st.error(str(exc))
                else:
                    st.rerun()
            else:
                st.success("Connection saved.")
                st.rerun()

    disconnect_col, delete_col = st.columns(2)
    with disconnect_col:
        if st.button("Disconnect", use_container_width=True):
            state.runtime.stop_all()
            st.rerun()
    with delete_col:
        if st.button("Delete", use_container_width=True, disabled=connection.built_in):
            if state.runtime.connection and state.runtime.connection.id == connection.id:
                state.runtime.stop_all()
            state.secrets.delete(connection.id, "database_password")
            state.secrets.delete(connection.id, "ssh_password")
            state.store.delete(connection.id)
            st.rerun()
