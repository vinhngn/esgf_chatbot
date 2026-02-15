"""
Webhook controller - DEPRECATED, use rag_service instead.
Kept for backward compatibility only.
"""
from __future__ import annotations

import logging
import re

from openai import OpenAI

from config import get_settings
from templates.cypher_templates import get_cypher_template

logger = logging.getLogger(__name__)


def _get_openai_client() -> OpenAI:
    return OpenAI(api_key=get_settings().OPENAI_API_KEY)


def generate_cypher(question: str, schema: str, database: str | None = None) -> str:
    """
    Generate Cypher query using OpenAI with the appropriate template.
    
    DEPRECATED: Use rag_service.get_raw_results() instead for better results.
    This is a simple direct OpenAI call without RAG pipeline.
    """
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
