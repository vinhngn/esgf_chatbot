"""Compatibility adapter for the pre-library service implementation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def legacy_database_names() -> tuple[str, str]:
    """Read database names from the legacy application settings lazily."""
    from config import get_settings

    settings = get_settings()
    return settings.profile_database_name, settings.database_name


class LegacyServiceBackend:
    """Load the existing service only when the first request is executed."""

    def __call__(
        self,
        question: str,
        schema: str,
    ) -> Mapping[str, Any]:
        from services.text2cypher.service import get_raw_results

        return get_raw_results(question, schema)
