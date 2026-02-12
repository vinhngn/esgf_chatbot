"""
Webhook controller - handles Flask API requests.
No Streamlit dependency. Uses config for API key.

FIX: No more hardcoded API key.
"""
from __future__ import annotations

import logging
import re

from openai import OpenAI

from config import get_settings
from templates.cypher_templates import get_cypher_template, _TEMPLATE_MAP

logger = logging.getLogger(__name__)


def _get_openai_client() -> OpenAI:
    return OpenAI(api_key=get_settings().OPENAI_API_KEY)


def generate_cypher(question: str, schema: str, database: str | None = None) -> str:
    """Generate Cypher query using OpenAI with the appropriate template."""
    settings = get_settings()
    db = database or settings.database_name
    template = get_cypher_template(db)

    prompt = (
        template
        .replace("{schema}", schema)
        .replace("{question}", f"Question: {question}\n\nCypher Query:")
    )

    try:
        client = _get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=1024,
        )
        cypher = response.choices[0].message.content.strip()

        # Clean up
        cypher = re.sub(r"```cypher\s*", "", cypher, flags=re.IGNORECASE)
        cypher = re.sub(r"```\s*", "", cypher)
        cypher = cypher.rstrip(";").strip()
        return cypher

    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return ""


def get_available_databases() -> list[str]:
    return list(_TEMPLATE_MAP.keys())
