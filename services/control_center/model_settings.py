from __future__ import annotations

from config import get_settings
from services.control_center.models import LlmProvider, ModelConfiguration

MODEL_SECRET_ID = "global-model-configuration"
OPENAI_API_KEY_SECRET = "openai_api_key"
LOCAL_API_KEY_SECRET = "local_api_key"


def default_model_configuration() -> ModelConfiguration:
    settings = get_settings()
    return ModelConfiguration(
        primary_provider=(
            LlmProvider.LOCAL if settings.LLM_LOCAL_FIRST else LlmProvider.OPENAI
        ),
        fallback_enabled=settings.LLM_FALLBACK_ENABLED,
        openai_model=settings.OPENAI_MODEL,
        openai_base_url=settings.OPENAI_BASE_URL,
        openai_timeout=settings.OPENAI_REQUEST_TIMEOUT,
        local_model=settings.LOCAL_LLM_MODEL,
        local_base_url=settings.LOCAL_LLM_BASE_URL,
        local_timeout=settings.LOCAL_LLM_REQUEST_TIMEOUT,
        max_retries=settings.LLM_PROVIDER_MAX_RETRIES,
    )
