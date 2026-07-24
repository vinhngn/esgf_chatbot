from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class BenchmarkConfig(BaseModel):
    dataset: str
    input_file: Path
    endpoint: str = "http://127.0.0.1:8954/api/text2cypher"
    row_limit: int = Field(default=50, ge=0)
    workers: int = Field(default=1, ge=1, le=8)
    webhook_timeout: float = Field(default=180, ge=1, le=900)
    query_timeout: float = Field(default=30, ge=1, le=600)
    result_limit: int = Field(default=1000, ge=0)
    result_json_max_chars: int = Field(default=100_000, ge=0)

    @field_validator("dataset")
    @classmethod
    def validate_dataset(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Dataset is required")
        return value

    @field_validator("input_file")
    @classmethod
    def validate_input_file(cls, value: Path) -> Path:
        value = value.expanduser().resolve()
        if not value.is_file():
            raise ValueError(f"Dataset file not found: {value}")
        return value

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("Endpoint must use http:// or https://")
        return value


class DatabaseTarget(BaseModel):
    uri: str
    username: str
    password: str
    database: str
    logical_database: str
