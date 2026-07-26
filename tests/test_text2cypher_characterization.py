"""Characterization tests for behavior preserved during pipeline cleanup."""

from langchain_core.messages import HumanMessage, SystemMessage

from services.text2cypher.execution import (
    cap_execution_cypher,
    result_quality_feedback,
)
from services.text2cypher.postprocessing import (
    repair_backticked_label_with_inline_map,
)
from services.text2cypher.prompting import (
    SYSTEM_PROMPT,
    build_coder_messages,
    build_coder_prompt,
)
from services.text2cypher.result_utils import extract_cypher_queries


def test_execution_cap_does_not_change_generated_query_when_under_cap() -> None:
    cypher = "MATCH (n) RETURN n LIMIT 25"

    assert cap_execution_cypher(cypher, row_cap=100) == (cypher, False)


def test_execution_cap_replaces_oversized_limit() -> None:
    cypher = "MATCH (n) RETURN n LIMIT 1000"

    assert cap_execution_cypher(cypher, row_cap=100) == (
        "MATCH (n) RETURN n LIMIT 100",
        True,
    )


def test_backticked_label_repair_preserves_inline_properties() -> None:
    malformed = "MATCH (m:`Movie {title: 'Inception'}`) RETURN m"

    actual = repair_backticked_label_with_inline_map(malformed)

    assert actual == "MATCH (m:Movie {title: 'Inception'}) RETURN m"


def test_all_null_projection_returns_retry_feedback() -> None:
    result = [{"m.title": None}, {"m.title": None}]

    feedback = result_quality_feedback(result)

    assert feedback is not None
    assert "null" in feedback.lower()


def test_empty_result_is_not_automatically_treated_as_invalid() -> None:
    assert result_quality_feedback([]) is None


def test_interactive_rag_can_request_empty_result_verification() -> None:
    feedback = result_quality_feedback([], retry_empty=True)

    assert feedback is not None
    assert "relationship direction" in feedback
    assert "do not remove" in feedback


def test_extract_cypher_queries_preserves_clean_query() -> None:
    cypher = "MATCH (m:Movie) RETURN m.title LIMIT 3"
    chain_result = {"intermediate_steps": [{"query": cypher}]}

    encoded, decoded = extract_cypher_queries(chain_result)

    assert decoded == cypher
    assert encoded == "MATCH%20%28m%3AMovie%29%20RETURN%20m.title%20LIMIT%203"


def test_retry_prompt_contains_previous_query_and_database_feedback() -> None:
    prompt = build_coder_prompt(
        schema="(:Movie {title: STRING})",
        learned_context="PROFILE CONTEXT",
        question="List movies.",
        current_cypher="MATCH (m:Movie) RETURN m",
        last_error="Unknown property",
    )

    assert "(:Movie {title: STRING})" in prompt
    assert "PROFILE CONTEXT" in prompt
    assert "MATCH (m:Movie) RETURN m" in prompt
    assert "Unknown property" in prompt


def test_coder_prompt_separates_stable_policy_from_profile_context() -> None:
    messages = build_coder_messages(
        schema="(:Company)-[:OWNS]->(:Product)",
        learned_context="Primary selected query recipe: MATCH (c:Company) RETURN c",
        evidence_context="Company.name is populated",
        question="List companies.",
        current_cypher="",
        last_error=None,
    )

    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    assert "Runtime schema defines valid graph structure" in messages[0].content
    assert "movies, products, users" not in messages[0].content
    assert "Primary selected query recipe" in messages[1].content
    assert "(:Company)-[:OWNS]->(:Product)" in messages[1].content
    assert "FALLBACK DOMAIN CONTEXT" not in messages[1].content


def test_system_prompt_is_a_small_evidence_protocol() -> None:
    assert len(SYSTEM_PROMPT) < 750
    assert "Output only raw Cypher" in SYSTEM_PROMPT
    assert "Runtime schema defines valid graph structure" in SYSTEM_PROMPT
    assert "movie" not in SYSTEM_PROMPT.casefold()
    assert "northwind" not in SYSTEM_PROMPT.casefold()
    assert "vector" not in SYSTEM_PROMPT.casefold()
