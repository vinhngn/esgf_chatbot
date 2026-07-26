from neo4j_t2c.execution.schema_validation import (
    relationship_schema_feedback,
)

SCHEMA = """
Node properties:
Lake {name: STRING}
Country {name: STRING}
Relationship properties:

The relationships:
(:Lake)-[:locatedIn]->(:Country)
"""


def test_relationship_schema_feedback_accepts_declared_direction() -> None:
    assert (
        relationship_schema_feedback(
            "MATCH (l:Lake)-[:locatedIn]->(c:Country) RETURN l.name",
            SCHEMA,
        )
        is None
    )
    assert (
        relationship_schema_feedback(
            "MATCH (c:Country)<-[:locatedIn]-(l:Lake) RETURN l.name",
            SCHEMA,
        )
        is None
    )


def test_relationship_schema_feedback_reports_reversed_direction() -> None:
    feedback = relationship_schema_feedback(
        "MATCH (l:Lake)<-[:locatedIn]-(c:Country) RETURN l.name",
        SCHEMA,
    )

    assert feedback is not None
    assert "(:Lake)-[:locatedIn]->(:Country)" in feedback
    assert "(:Country)-[:locatedIn]->(:Lake)" in feedback


def test_relationship_schema_feedback_ignores_untyped_or_undirected_patterns() -> None:
    assert (
        relationship_schema_feedback(
            "MATCH (l:Lake)--(c:Country) RETURN l.name",
            SCHEMA,
        )
        is None
    )
