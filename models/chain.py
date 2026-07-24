"""Backward-compatible import for the Text-to-Cypher pipeline."""

from services.text2cypher.pipeline import invoke_chain

__all__ = ["invoke_chain"]
