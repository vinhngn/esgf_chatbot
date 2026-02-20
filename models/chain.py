"""
GraphCypherQAChain setup.
No Streamlit dependency.
"""
from __future__ import annotations

import logging
import urllib.parse

from langchain_community.chains.graph_qa.cypher import GraphCypherQAChain
from langchain_core.prompts import PromptTemplate

from config import get_settings
from models.graph import get_graph, maybe_refresh_schema
from models.llm import get_cypher_llm, get_qa_llm
from templates.cypher_templates import get_cypher_template
from utils.helpers import clean_cypher_query

logger = logging.getLogger(__name__)

_chain: GraphCypherQAChain | None = None


def _build_chain() -> GraphCypherQAChain:
    """Build the GraphCypherQAChain with current config."""
    template = get_cypher_template()
    prompt = PromptTemplate(
        input_variables=["schema", "question"],
        template=template,
    )
    return GraphCypherQAChain.from_llm(
        cypher_llm=get_cypher_llm(),
        qa_llm=get_qa_llm(),
        graph=get_graph(),
        cypher_prompt=prompt,
        validate_cypher=True,
        return_direct=True,
        verbose=True,
        allow_dangerous_requests=True,
        return_intermediate_steps=True,
        top_k=100,
    )


def get_chain() -> GraphCypherQAChain:
    """Get or create the chain (singleton)."""
    global _chain
    if _chain is None:
        _chain = _build_chain()
    return _chain


def invoke_chain(question: str) -> dict | str:
    """Invoke the chain with a question. Returns chain result dict or error string."""
    maybe_refresh_schema()
    chain = get_chain()

    try:
        result = chain.invoke({"query": question}, return_only_outputs=True)
    except Exception as e:
        logger.warning(f"GraphCypher chain error: {e}")
        return "Sorry, I couldn't find an answer to your question."

    if result is None:
        return "No answer was generated."

    # Clean and encode the generated Cypher query
    try:
        steps = result.get("intermediate_steps", [{}])
        if steps and isinstance(steps[-1], dict):
            query_raw = steps[-1].get("query", "")
            if query_raw:
                cleaned = clean_cypher_query(query_raw)
                encoded = urllib.parse.quote(cleaned)
                result["intermediate_steps"][-1]["query"] = encoded
    except Exception as e:
        logger.warning(f"Failed to extract/clean Cypher query: {e}")

    return result
