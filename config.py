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


class Settings:
    """Application settings loaded from .env"""

    # Neo4j
    NEO4J_URI: str = os.getenv("NEO4J_URI", "")
    NEO4J_USERNAME: str = os.getenv("NEO4J_USERNAME", "")
    NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "")
    NEO4J_DATABASE: str = os.getenv("NEO4J_DATABASE", "climate")

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # App
    FREE_QUESTIONS_PER_SESSION: int = int(
        os.getenv("FREE_QUESTIONS_PER_SESSION", "100")
    )
    FLASK_PORT: int = int(os.getenv("FLASK_PORT", "8954"))
    FLASK_HOST: str = os.getenv("FLASK_HOST", "0.0.0.0")
    SERVER_URL: str = os.getenv("SERVER_URL", "http://localhost")

    # Analytics
    SEGMENT_WRITE_KEY: str = os.getenv("SEGMENT_WRITE_KEY", "")

    @property
    def database_name(self) -> str:
        return self.NEO4J_DATABASE.lower()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
