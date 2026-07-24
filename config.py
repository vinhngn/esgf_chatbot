"""
Centralized configuration - single source of truth.
Reads from .env file, no more scattered secrets.toml files.
"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

# Load .env from project root
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_env_path)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """Application settings loaded from .env"""

    # Neo4j
    NEO4J_URI: str = os.getenv("NEO4J_URI", "")
    NEO4J_USERNAME: str = os.getenv("NEO4J_USERNAME", "")
    NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "")
    NEO4J_DATABASE: str = os.getenv("NEO4J_DATABASE", "climate")
    T2C_PROFILE_DATABASE: str = os.getenv("T2C_PROFILE_DATABASE", "")

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_REQUEST_TIMEOUT: float = float(os.getenv("OPENAI_REQUEST_TIMEOUT", "30"))

    # Local OpenAI-compatible provider
    LOCAL_LLM_BASE_URL: str = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:20128/v1")
    LOCAL_LLM_API_KEY: str = os.getenv("LOCAL_LLM_API_KEY", "local")
    LOCAL_LLM_MODEL: str = os.getenv("LOCAL_LLM_MODEL", "antigravity/gemini-2.5-flash")
    LOCAL_LLM_REQUEST_TIMEOUT: float = float(os.getenv("LOCAL_LLM_REQUEST_TIMEOUT", "120"))

    # Provider routing
    LLM_LOCAL_FIRST: bool = _env_bool("LLM_LOCAL_FIRST", False)
    LLM_FALLBACK_ENABLED: bool = _env_bool("LLM_FALLBACK_ENABLED", True)
    LLM_PROVIDER_MAX_RETRIES: int = int(os.getenv("LLM_PROVIDER_MAX_RETRIES", "0"))

    # App
    FLASK_PORT: int = int(os.getenv("FLASK_PORT", "8954"))
    FLASK_HOST: str = os.getenv("FLASK_HOST", "0.0.0.0")
    SERVER_URL: str = os.getenv("SERVER_URL", "http://localhost")

    @property
    def database_name(self) -> str:
        return self.NEO4J_DATABASE.lower()

    @property
    def profile_database_name(self) -> str:
        return (self.T2C_PROFILE_DATABASE or self.NEO4J_DATABASE).lower()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
