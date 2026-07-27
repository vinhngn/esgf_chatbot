from __future__ import annotations

import streamlit as st
from pydantic import ValidationError

from services.control_center.model_settings import (
    LOCAL_API_KEY_SECRET,
    MODEL_SECRET_ID,
    OPENAI_API_KEY_SECRET,
)
from services.control_center.models import LlmProvider, ModelConfiguration
from views.studio.state import StudioState


def _configuration_from_form(values: dict) -> ModelConfiguration:
    return ModelConfiguration(
        primary_provider=LlmProvider(values["primary_provider"]),
        fallback_enabled=values["fallback_enabled"],
        openai_model=values["openai_model"],
        openai_base_url=values["openai_base_url"],
        openai_timeout=values["openai_timeout"],
        local_model=values["local_model"],
        local_base_url=values["local_base_url"],
        local_timeout=values["local_timeout"],
        max_retries=values["max_retries"],
    )


def _model_form(state: StudioState) -> tuple[str, dict]:
    current = state.model_configuration
    with st.form("model-configuration"):
        provider = st.segmented_control(
            "Primary provider",
            options=[LlmProvider.OPENAI.value, LlmProvider.LOCAL.value],
            default=current.primary_provider.value,
            format_func=lambda value: {
                LlmProvider.OPENAI.value: "OpenAI",
                LlmProvider.LOCAL.value: "Local / OpenAI-compatible",
            }[value],
            selection_mode="single",
        )
        fallback_enabled = st.toggle(
            "Use the other provider as fallback",
            value=current.fallback_enabled,
        )

        openai_column, local_column = st.columns(2)
        with openai_column:
            st.subheader("OpenAI")
            openai_model = st.text_input("OpenAI model", current.openai_model)
            openai_base_url = st.text_input(
                "OpenAI base URL",
                current.openai_base_url,
                placeholder="Official OpenAI endpoint",
            )
            openai_key = st.text_input(
                "OpenAI API key",
                value=state.secrets.get(MODEL_SECRET_ID, OPENAI_API_KEY_SECRET),
                type="password",
                help="Stored locally for this Studio. Leave empty if the selected provider does not need a key.",
            )
            openai_timeout = st.number_input(
                "OpenAI timeout (seconds)",
                min_value=1.0,
                max_value=600.0,
                value=float(current.openai_timeout),
                step=1.0,
            )

        with local_column:
            st.subheader("Local / compatible")
            local_model = st.text_input("Local model", current.local_model)
            local_base_url = st.text_input(
                "Local base URL",
                current.local_base_url,
                placeholder="http://localhost:20128/v1",
            )
            local_key = st.text_input(
                "Local API key",
                value=state.secrets.get(MODEL_SECRET_ID, LOCAL_API_KEY_SECRET),
                type="password",
                help="Optional for local servers that do not require authentication.",
            )
            local_timeout = st.number_input(
                "Local timeout (seconds)",
                min_value=1.0,
                max_value=600.0,
                value=float(current.local_timeout),
                step=1.0,
            )

        max_retries = st.number_input(
            "Provider retries",
            min_value=0,
            max_value=5,
            value=current.max_retries,
            help="Retries inside one provider. Keep at 0 for fast failure and fallback.",
        )
        save_column, restart_column = st.columns([1, 2])
        save = save_column.form_submit_button(
            "Save model settings",
            width="stretch",
        )
        restart = restart_column.form_submit_button(
            "Save and restart T2C API",
            type="primary",
            width="stretch",
        )

    action = "restart" if restart else "save" if save else ""
    values = {
        "primary_provider": provider or current.primary_provider.value,
        "fallback_enabled": fallback_enabled,
        "openai_model": openai_model,
        "openai_base_url": openai_base_url,
        "openai_key": openai_key,
        "openai_timeout": float(openai_timeout),
        "local_model": local_model,
        "local_base_url": local_base_url,
        "local_key": local_key,
        "local_timeout": float(local_timeout),
        "max_retries": int(max_retries),
    }
    return action, values


def render(state: StudioState) -> None:
    st.header("AI model")
    action, values = _model_form(state)
    if not action:
        return

    try:
        configuration = _configuration_from_form(values)
    except ValidationError as exc:
        st.error(str(exc))
        return

    state.store.save_model_configuration(configuration)
    state.secrets.set(
        MODEL_SECRET_ID,
        OPENAI_API_KEY_SECRET,
        values["openai_key"],
    )
    state.secrets.set(
        MODEL_SECRET_ID,
        LOCAL_API_KEY_SECRET,
        values["local_key"],
    )

    if action == "restart":
        try:
            with st.spinner("Restarting the T2C API with the selected model..."):
                state.runtime.stop_all()
                state.runtime.start_api(state.active_connection, configuration)
        except Exception as exc:
            st.error(str(exc))
            return
        st.rerun()

    if state.runtime.api_running:
        st.info("Settings saved. Restart the T2C API to apply them.")
    else:
        st.success("Model settings saved.")
