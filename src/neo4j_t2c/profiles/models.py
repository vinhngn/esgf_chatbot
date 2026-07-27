"""Pydantic contracts for versioned database profiles."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROFILE_VERSION = "2.0"


class ProfileProvenance(BaseModel):
    """Where a profile came from and which generator produced it."""

    model_config = ConfigDict(extra="allow")

    source: str = ""
    source_type: str = "unknown"
    generator: str = "neo4j-t2c"
    generator_version: str = "0.1.0"
    generated_at: str | None = None


class ProfileDocument(BaseModel):
    """Validated superset of benchmark-derived and live Neo4j profiles."""

    model_config = ConfigDict(extra="allow")

    profile_version: str = PROFILE_VERSION
    database: str = Field(min_length=1)
    physical_database: str = ""
    source: str = ""
    source_type: str = "unknown"
    provenance: ProfileProvenance = Field(
        default_factory=ProfileProvenance
    )
    schema_fingerprint: str = ""
    row_count: int = Field(default=0, ge=0)
    schema_profile: dict[str, Any] = Field(default_factory=dict)
    planner_profile: dict[str, Any] = Field(default_factory=dict)
    value_profile: dict[str, Any] = Field(default_factory=dict)
    query_recipe_profile: dict[str, Any] = Field(default_factory=dict)
    build_options: dict[str, Any] = Field(default_factory=dict)
    question_summary: dict[str, Any] = Field(default_factory=dict)
    cypher_summary: dict[str, Any] = Field(default_factory=dict)
    intent_to_shape_signatures: dict[str, Any] = Field(
        default_factory=dict
    )
    motif_to_return_contracts: dict[str, Any] = Field(
        default_factory=dict
    )
    token_to_path_motifs: dict[str, Any] = Field(default_factory=dict)
    examples: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("database")
    @classmethod
    def normalize_database(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("Database is required")
        return value

    def to_payload(self) -> dict[str, Any]:
        """Return the backward-compatible JSON mapping."""
        return self.model_dump(mode="json", exclude_none=True)
