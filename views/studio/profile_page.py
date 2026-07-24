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
    if path.exists():
        st.success(f"Ready: {path.name}")
    else:
        st.warning(f"Missing: {path.name}")

    full_profile = st.checkbox(
        "Full profile",
        value=(
            connection.profile.sample_limit == 0
            and connection.profile.value_limit == 0
            and connection.profile.include_values
        ),
        help=(
            "Read all fallback schema samples and distinct property values. "
            "Large databases can take considerably longer."
        ),
    )
    with st.form(f"profile-{connection.id}"):
        first, second, third = st.columns(3)
        sample_limit = first.number_input(
            "Fallback sample limit",
            min_value=0,
            value=0 if full_profile else connection.profile.sample_limit,
            help="0 means unlimited.",
            disabled=full_profile,
        )
        max_hops = second.number_input(
            "Maximum path hops",
            min_value=1,
            max_value=8,
            value=connection.profile.max_hops,
        )
        value_limit = third.number_input(
            "Values per property",
            min_value=0,
            value=0 if full_profile else connection.profile.value_limit,
            help="0 means unlimited.",
            disabled=full_profile,
        )
        include_values = st.checkbox(
            "Include sampled property values",
            value=True if full_profile else connection.profile.include_values,
            disabled=full_profile,
        )
        build = st.form_submit_button(
            "Build or refresh profile",
            type="primary",
            use_container_width=True,
        )

    if build:
        updated = connection.model_copy(
            update={
                "profile": ProfileOptions(
                    sample_limit=0 if full_profile else int(sample_limit),
                    max_hops=int(max_hops),
                    value_limit=0 if full_profile else int(value_limit),
                    include_values=True if full_profile else include_values,
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
    one, two, three, four = st.columns(4)
    one.metric("Labels", len(labels))
    two.metric("Relationships", len(relationships))
    three.metric("Paths", len(paths))
    four.metric("Examples", profile.get("row_count", 0))

    with st.expander("Schema summary", expanded=True):
        st.json(summary)
    with st.expander("Generated recipes and examples"):
        st.json(
            {
                "query_recipe_profile": profile.get("query_recipe_profile", {}),
                "examples": profile.get("examples", [])[:20],
            }
        )
