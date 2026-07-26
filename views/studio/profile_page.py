from __future__ import annotations

import json

import streamlit as st

from services.control_center.models import ProfileOptions
from services.control_center.profile_service import build_connection_profile
from views.studio.state import StudioState


def render(state: StudioState) -> None:
    connection = state.active_connection
    path = connection.profile_path(state.runtime.project_root)
    st.header("Profile")
    st.caption(
        f"Selected profile: {connection.profile_name} | "
        f"Physical database: {connection.database}"
    )
    runtime_connection = state.runtime.connection
    if runtime_connection and runtime_connection.id != connection.id:
        st.warning(
            "The running T2C API uses "
            f"{runtime_connection.profile_name}/{runtime_connection.database}, "
            "not the database selected on this page."
        )
    if path.exists():
        st.success(f"Ready: {path.name}")
    else:
        st.warning(f"Missing: {path.name}")

    comprehensive_profile = st.checkbox(
        "Comprehensive profile",
        value=False,
        help=(
            "Use larger but bounded evidence budgets. Schema discovery remains exact; "
            "the application never loads every distinct database value into memory."
        ),
    )
    with st.form(f"profile-{connection.id}"):
        first, second, third = st.columns(3)
        sample_limit = first.number_input(
            "Fallback sample limit",
            min_value=1,
            value=(
                10_000
                if comprehensive_profile
                else max(1, connection.profile.sample_limit or 10_000)
            ),
            help="Used only if Neo4j schema procedures cannot expose properties.",
            disabled=comprehensive_profile,
        )
        max_hops = second.number_input(
            "Maximum path hops",
            min_value=1,
            max_value=8,
            value=connection.profile.max_hops,
        )
        value_limit = third.number_input(
            "Values per property",
            min_value=1,
            value=(
                50
                if comprehensive_profile
                else max(1, connection.profile.value_limit or 50)
            ),
            help="Maximum frequent values retained for each property.",
            disabled=comprehensive_profile,
        )
        include_values = st.checkbox(
            "Include sampled property values",
            value=True if comprehensive_profile else connection.profile.include_values,
            disabled=comprehensive_profile,
        )
        build = st.form_submit_button(
            "Build or refresh profile",
            type="primary",
            width="stretch",
        )

    if build:
        updated = connection.model_copy(
            update={
                "profile": ProfileOptions(
                    sample_limit=10_000 if comprehensive_profile else int(sample_limit),
                    max_hops=int(max_hops),
                    value_limit=50 if comprehensive_profile else int(value_limit),
                    include_values=True if comprehensive_profile else include_values,
                )
            }
        )
        state.store.save(updated)
        try:
            with st.spinner("Reading Neo4j schema and building profile..."):
                output, profile = build_connection_profile(
                    project_root=state.runtime.project_root,
                    connection=updated,
                    secrets=state.secrets,
                    session=state.runtime.session
                    if state.runtime.connection
                    and state.runtime.connection.id == updated.id
                    else None,
                    dataset_directory=state.benchmarks.framework_root / "inputs",
                )
        except Exception as exc:
            st.error(str(exc))
        else:
            st.success(
                f"Profile built: {profile.get('row_count', 0)} examples at {output.name}"
            )
            st.rerun()

    if not path.exists():
        return
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        st.error(f"Cannot read profile: {exc}")
        return

    summary = profile.get("schema_profile", {}).get("summary", {})
    labels = profile.get("schema_profile", {}).get("labels", [])
    relationships = profile.get("schema_profile", {}).get("relationships", [])
    paths = profile.get("schema_profile", {}).get("paths", [])
    vector_indexes = profile.get("schema_profile", {}).get(
        "vector_indexes",
        [],
    )
    vector_discovery = profile.get("schema_profile", {}).get(
        "vector_index_discovery",
        {},
    )
    path_discovery = profile.get("schema_profile", {}).get(
        "path_discovery",
        {},
    )
    one, two, three, four, five = st.columns(5)
    one.metric("Labels", len(labels))
    two.metric("Relationships", len(relationships))
    three.metric("Paths", len(paths))
    vector_status = vector_discovery.get("status", "unknown")
    four.metric(
        "Vector indexes",
        "Unavailable" if vector_status == "unavailable" else len(vector_indexes),
    )
    five.metric("Examples", profile.get("row_count", 0))

    with st.expander("Schema summary", expanded=True):
        st.json(
            {
                "logical_database": profile.get("database"),
                "physical_database": profile.get("physical_database"),
                "build_options": profile.get("build_options", {}),
                "summary": summary,
                "path_discovery": path_discovery,
                "vector_index_discovery": vector_discovery,
            }
        )
    if path_discovery.get("status") == "truncated":
        st.warning(
            "Schema path discovery reached its configured limit "
            f"({path_discovery.get('limit')}). Reduce max hops or raise "
            "T2C_PROFILE_PATH_LIMIT deliberately."
        )
    if vector_status == "unavailable":
        st.warning(
            "Neo4j vector-index discovery was unavailable: "
            f"{vector_discovery.get('message') or 'unknown error'}"
        )
    with st.expander("Generated recipes and examples"):
        st.json(
            {
                "query_recipe_profile": profile.get("query_recipe_profile", {}),
                "vector_indexes": vector_indexes,
                "examples": profile.get("examples", [])[:20],
            }
        )
