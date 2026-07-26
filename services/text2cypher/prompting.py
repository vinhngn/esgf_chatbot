"""Compatibility shim for ``neo4j_t2c.generation.prompts``."""

from neo4j_t2c.generation.prompts import (
    SYSTEM_PROMPT,
    build_coder_messages,
    build_coder_prompt,
)

__all__ = ["SYSTEM_PROMPT", "build_coder_messages", "build_coder_prompt"]
