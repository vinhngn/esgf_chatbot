from __future__ import annotations

import re


def clean_cypher_query(query_raw: str) -> str:
    """Clean up LLM-generated Cypher query (remove markdown, prefix, semicolons)."""
    query = re.sub(r"```cypher\s*", "", query_raw, flags=re.IGNORECASE)
    query = re.sub(r"```\s*", "", query)
    query = re.sub(r"^\s*cypher\s+", "", query, flags=re.IGNORECASE)
    # PromptTemplate examples escape literal Cypher maps as {{...}}. The LLM can
    # copy those braces verbatim, so normalize them before Neo4j sees the query.
    query = query.replace("{{", "{").replace("}}", "}")
    return query.rstrip(";").strip()
