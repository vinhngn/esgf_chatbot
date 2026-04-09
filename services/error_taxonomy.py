"""
Cypher Error Taxonomy — systematic classification of Text-to-Cypher errors.

Adapted from SQL-of-Thought's error taxonomy for graph databases.
Each error type has:
  - Code: short identifier
  - Description: what went wrong
  - Detection: how to detect it (code logic)
  - Fix strategy: how to fix it (code logic)

9 categories, 18 error types.
Used by the Validator for auto-correction and by the Error Handler
for targeted feedback to the Planner.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorCategory(str, Enum):
    SCHEMA_LINK = "SCHEMA_LINK"
    DIRECTION = "DIRECTION"
    PROPERTY_LOCATION = "PROPERTY_LOCATION"
    MATCH_PATTERN = "MATCH_PATTERN"
    WHERE_CONDITION = "WHERE_CONDITION"
    AGGREGATION = "AGGREGATION"
    RETURN_CLAUSE = "RETURN_CLAUSE"
    VARIABLE_REUSE = "VARIABLE_REUSE"
    SYNTAX = "SYNTAX"


@dataclass
class CypherError:
    """A classified Cypher error."""
    code: str
    category: ErrorCategory
    description: str
    fix_hint: str
    auto_fixable: bool = False


# Error definitions
ERRORS = {
    # Schema Linking errors
    "SL01": CypherError("SL01", ErrorCategory.SCHEMA_LINK,
        "Wrong node label used",
        "Check available labels in schema and use closest match",
        auto_fixable=True),
    "SL02": CypherError("SL02", ErrorCategory.SCHEMA_LINK,
        "Wrong property name used",
        "Check property names for the specific label in schema",
        auto_fixable=True),
    "SL03": CypherError("SL03", ErrorCategory.SCHEMA_LINK,
        "Wrong relationship type used",
        "Check available relationship types in schema",
        auto_fixable=True),

    # Direction errors
    "DR01": CypherError("DR01", ErrorCategory.DIRECTION,
        "Relationship direction reversed",
        "Check direction truth table: (A)-[:REL]->(B) not (B)-[:REL]->(A)",
        auto_fixable=True),

    # Property location errors
    "PL01": CypherError("PL01", ErrorCategory.PROPERTY_LOCATION,
        "Property accessed on node but exists on relationship (or vice versa)",
        "Check property location map: is 'rating' on :Movie or on [:REVIEWED]?",
        auto_fixable=True),

    # Match pattern errors
    "MP01": CypherError("MP01", ErrorCategory.MATCH_PATTERN,
        "Missing intermediate hop in multi-hop query",
        "Add missing MATCH clause for the intermediate node",
        auto_fixable=False),
    "MP02": CypherError("MP02", ErrorCategory.MATCH_PATTERN,
        "Wrong chain: conditions from different entities merged into one path",
        "Use separate MATCH clauses with shared variables",
        auto_fixable=False),

    # WHERE condition errors
    "WC01": CypherError("WC01", ErrorCategory.WHERE_CONDITION,
        "WHERE filter on wrong property",
        "Check which property holds the value being filtered",
        auto_fixable=True),
    "WC02": CypherError("WC02", ErrorCategory.WHERE_CONDITION,
        "Wrong comparison operator",
        "Check if > should be <, = should be CONTAINS, etc.",
        auto_fixable=False),

    # Aggregation errors
    "AG01": CypherError("AG01", ErrorCategory.AGGREGATION,
        "Missing WITH clause for aggregation",
        "Add WITH clause before aggregation in RETURN",
        auto_fixable=False),
    "AG02": CypherError("AG02", ErrorCategory.AGGREGATION,
        "Wrong aggregation function",
        "Check if count should be avg, sum should be count, etc.",
        auto_fixable=False),

    # Return clause errors
    "RC01": CypherError("RC01", ErrorCategory.RETURN_CLAUSE,
        "Wrong columns in RETURN",
        "Check what the question asks for and return those columns",
        auto_fixable=False),
    "RC02": CypherError("RC02", ErrorCategory.RETURN_CLAUSE,
        "Missing columns in RETURN",
        "Add the missing requested columns",
        auto_fixable=False),

    # Variable reuse errors
    "VR01": CypherError("VR01", ErrorCategory.VARIABLE_REUSE,
        "Same entity uses different variables across MATCH clauses",
        "Reuse the same variable for the same entity",
        auto_fixable=True),

    # Syntax errors
    "SY01": CypherError("SY01", ErrorCategory.SYNTAX,
        "COUNT(pattern) instead of count{pattern}",
        "Use count{(a)-[:REL]->(b)} subquery syntax",
        auto_fixable=True),
    "SY02": CypherError("SY02", ErrorCategory.SYNTAX,
        "WHERE clause embedded inside MATCH pattern",
        "Move WHERE after the complete MATCH pattern",
        auto_fixable=True),
    "SY03": CypherError("SY03", ErrorCategory.SYNTAX,
        "Invalid property access syntax",
        "Use n.property not n['property']",
        auto_fixable=True),
}


def classify_neo4j_error(error_message: str) -> list[CypherError]:
    """Classify a Neo4j execution error into taxonomy categories."""
    msg = str(error_message).lower()
    errors = []

    if "syntaxerror" in msg:
        if "pattern expression" in msg or "count" in msg:
            errors.append(ERRORS["SY01"])
        elif "where" in msg:
            errors.append(ERRORS["SY02"])
        else:
            errors.append(ERRORS["SY03"])

    if "not found" in msg or "unknown" in msg:
        if "label" in msg or "type" in msg:
            errors.append(ERRORS["SL01"])
        elif "property" in msg:
            errors.append(ERRORS["SL02"])
        elif "relationship" in msg:
            errors.append(ERRORS["SL03"])

    if not errors:
        # Generic execution error
        errors.append(CypherError(
            "EX00", ErrorCategory.SYNTAX,
            f"Execution error: {str(error_message)[:200]}",
            "Review the generated Cypher for logical errors",
            auto_fixable=False,
        ))

    return errors


def format_errors_for_llm(errors: list[CypherError]) -> str:
    """Format errors as text for LLM feedback."""
    lines = []
    for e in errors:
        lines.append(f"[{e.code}] {e.category.value}: {e.description}")
        lines.append(f"  Fix: {e.fix_hint}")
    return "\n".join(lines)
