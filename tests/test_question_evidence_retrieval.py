from neo4j_t2c.profiles.question_evidence import extract_question_evidence
from neo4j_t2c.profiles.retrieval import select_profile_context


def _example(row: int, question: str, cypher: str) -> dict:
    return {
        "row": row,
        "question": question,
        "question_evidence": extract_question_evidence(question).to_dict(),
        "cypher": cypher,
        "cypher_shape": {
            "signature": f"shape-{row}",
            "path_motifs": [],
            "return_items": [],
        },
    }


def test_question_evidence_preserves_language_without_domain_normalization() -> None:
    evidence = extract_question_evidence("Quels quasars ont une luminosite > 42?")

    assert evidence.terms == [
        "quels",
        "quasars",
        "ont",
        "une",
        "luminosite",
        "42",
    ]
    assert evidence.numbers == ["42"]


def test_retrieval_uses_statistical_similarity_and_cypher_structure() -> None:
    profile = {
        "source_type": "benchmark_csv",
        "examples": [
            _example(
                1,
                "Rank quasar records by luminosity.",
                "MATCH (q:Quasar) RETURN q.code, q.luminosity "
                "ORDER BY q.luminosity DESC LIMIT 5",
            ),
            _example(
                2,
                "List nebula locations.",
                "MATCH (n:Nebula) RETURN n.location",
            ),
        ],
        "schema_profile": {
            "paths": [
                {
                    "signature": "(Quasar)-[:OBSERVED_AT]->(Observatory)",
                    "hops": 1,
                }
            ]
        },
    }

    context = select_profile_context(
        "Which quasars have the greatest luminosity?",
        profile,
    )

    assert context["selected_recipes"][0]["row"] == 1
    assert context["query_plan_contract"]["expected_operation"] == "rank"
    assert "question_intents" not in context["query_plan_contract"]


def test_live_generated_examples_do_not_become_learned_query_contracts() -> None:
    profile = {
        "source_type": "neo4j_live",
        "examples": [
            _example(
                1,
                "Find Category records connected to Supplier.",
                "MATCH (c:Category)<-[:PART_OF]-(p:Product)"
                "<-[:SUPPLIES]-(s:Supplier) RETURN c, p, s LIMIT 10",
            )
        ],
        "schema_profile": {
            "paths": [
                {
                    "signature": "(Supplier)-[:SUPPLIES]->(Product)",
                    "hops": 1,
                }
            ]
        },
    }

    context = select_profile_context(
        "Which suppliers have the highest average product price?",
        profile,
    )

    assert context["selected_examples"] == []
    assert context["selected_recipes"] == []
    assert context["query_plan_contract"] == {}
    assert context["schema_paths"][0]["signature"] == (
        "(Supplier)-[:SUPPLIES]->(Product)"
    )
