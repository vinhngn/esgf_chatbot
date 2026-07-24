"""Validated connection models shared by the Streamlit control pages."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class ConnectionKind(str, Enum):
    DIRECT = "direct"
    SSH_TUNNEL = "ssh_tunnel"


class LlmProvider(str, Enum):
    OPENAI = "openai"
    LOCAL = "local"


class ModelConfiguration(BaseModel):
    primary_provider: LlmProvider = LlmProvider.OPENAI
    fallback_enabled: bool = True
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""
    openai_timeout: float = Field(default=30, ge=1, le=600)
    local_model: str = "antigravity/gemini-2.5-flash"
    local_base_url: str = "http://localhost:20128/v1"
    local_timeout: float = Field(default=120, ge=1, le=600)
    max_retries: int = Field(default=0, ge=0, le=5)

    @field_validator("openai_model", "local_model")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Model name is required")
        return value

    @field_validator("openai_base_url", "local_base_url")
    @classmethod
    def validate_provider_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("Provider URL must use http:// or https://")
        return value


class ProfileOptions(BaseModel):
    sample_limit: int = Field(default=0, ge=0)
    max_hops: int = Field(default=3, ge=1, le=8)
    value_limit: int = Field(default=0, ge=0)
    include_values: bool = True


class SshTunnel(BaseModel):
    jump_host: str
    jump_port: int = Field(default=22, ge=1, le=65535)
    ssh_username: str = ""
    target_host: str
    remote_port: int = Field(ge=1, le=65535)
    local_port: int = Field(ge=1, le=65535)

    @field_validator("jump_host", "target_host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Host is required")
        return value


class DatabaseConnection(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    profile_name: str
    kind: ConnectionKind = ConnectionKind.DIRECT
    uri: str
    database: str = "neo4j"
    username: str = ""
    ssh: SshTunnel | None = None
    profile: ProfileOptions = Field(default_factory=ProfileOptions)
    built_in: bool = False

    @field_validator("name", "profile_name", "database")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value is required")
        return value

    @field_validator("profile_name")
    @classmethod
    def normalize_profile_name(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        value = value.strip()
        supported = (
            "neo4j://",
            "neo4j+s://",
            "neo4j+ssc://",
            "bolt://",
            "bolt+s://",
            "bolt+ssc://",
        )
        if not value.startswith(supported):
            raise ValueError("Use a Neo4j or Bolt URI")
        return value

    @model_validator(mode="after")
    def validate_tunnel(self) -> DatabaseConnection:
        if self.kind == ConnectionKind.SSH_TUNNEL and self.ssh is None:
            raise ValueError("SSH tunnel settings are required")
        return self

    @property
    def runtime_uri(self) -> str:
        if self.kind == ConnectionKind.SSH_TUNNEL and self.ssh:
            return f"bolt://127.0.0.1:{self.ssh.local_port}"
        return self.uri

    def profile_path(self, root: Path) -> Path:
        return root / "generated_profiles" / f"{self.profile_name}_profile.json"
