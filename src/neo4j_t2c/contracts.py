"""Stable request and result contracts for the public library API."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class Text2CypherRequest(BaseModel):
    """One natural-language request and its optional authoritative schema."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    question: str = Field(min_length=1)
    schema_text: str = Field(
        default="",
        validation_alias=AliasChoices("schema_text", "schema"),
        serialization_alias="schema",
    )

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Question is required")
        return value

    @field_validator("schema_text")
    @classmethod
    def normalize_schema(cls, value: str) -> str:
        return value.strip()


class Text2CypherResult(BaseModel):
    """Typed result returned by :class:`Text2CypherEngine`."""

    model_config = ConfigDict(frozen=True)

    cypher: str = ""
    rows: list[Any] = Field(default_factory=list)
    error: str | None = None
    trace_id: str = ""
    rewritten_question: str = ""
    verified_triples: list[tuple[str, str, str]] = Field(default_factory=list)
    instance_triples: list[tuple[str, str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.error is None

    @classmethod
    def from_service_payload(
        cls,
        payload: Mapping[str, Any],
    ) -> Text2CypherResult:
        """Translate the current service payload into the stable public contract."""
        known_keys = {
            "cypher_query",
            "result",
            "error",
            "trace_id",
            "rewritten",
            "verified_triples",
            "instance_triples",
        }
        return cls(
            cypher=str(payload.get("cypher_query") or ""),
            rows=list(payload.get("result") or []),
            error=(
                str(payload["error"])
                if payload.get("error") is not None
                else None
            ),
            trace_id=str(payload.get("trace_id") or ""),
            rewritten_question=str(payload.get("rewritten") or ""),
            verified_triples=list(payload.get("verified_triples") or []),
            instance_triples=list(payload.get("instance_triples") or []),
            metadata={
                key: value
                for key, value in payload.items()
                if key not in known_keys
            },
        )


class TraceEvent(BaseModel):
    """One implementation-neutral pipeline observation."""

    model_config = ConfigDict(frozen=True)

    trace_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    message: str = Field(min_length=1)
    payload: Any = None
    verbose: bool = False
