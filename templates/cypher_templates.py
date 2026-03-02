"""
Generalized Cypher generation template -- SADP (Schema-Aware Dynamic Prompting).

Replaces the previous 5 hardcoded per-database templates with a single
dynamically-built template.  The template is assembled from:

1. Generic rules (shared across ALL databases)
2. Auto-detected relationship-property warnings (from SchemaGraph)
3. Auto-generated few-shot examples (from PatternLibrary + BFS)
4. {schema} placeholder (filled at runtime by LangChain)
5. {question} placeholder

Public API (unchanged signatures):
    get_cypher_template(db_name) -> str
"""

from __future__ import annotations

import logging
from config import get_settings
from templates.schema_loader import load_schema, get_available_databases as _get_dbs
from templates.schema_graph import build_examples_for_db, build_property_warnings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Generic rules -- shared across ALL Neo4j databases
# ---------------------------------------------------------------------------

GENERIC_RULES = """
=== CRITICAL RULES (Follow in order of priority) ===

RULE 1 - OUTPUT FORMAT (CRITICAL):
- Output ONLY valid Cypher query
- NO markdown code blocks (```)
- NO "cypher" prefix
- NO explanations before or after the query

RULE 2 - RELATIONSHIP PROPERTIES (CRITICAL - #1 error source):
- ALWAYS check the SCHEMA to see which properties belong to nodes vs relationships
- If a property is on a RELATIONSHIP, access it via the relationship variable: r.property
- NEVER access relationship properties on the node: WRONG m.rating, CORRECT r.rating
- See the PROPERTY LOCATION WARNINGS section below for this database

RULE 3 - RETURN FORMAT:
| Question Pattern | Return Format |
|------------------|---------------|
| "Find/Show all X" / "List X" | RETURN n (full node) |
| "top N X by Y" / "highest/lowest" | RETURN n ORDER BY n.Y DESC/ASC LIMIT N |
| "What is the X of Y" | RETURN specific property |
| "first N X" (simple retrieval) | RETURN n LIMIT N |
| "Which X have..." with JOINs | RETURN DISTINCT x.name |

RULE 4 - RELATIONSHIP DIRECTION:
- NEVER reverse relationship direction from the schema
- If schema says (A)-[:REL]->(B), always write it that way
- Check the SCHEMA section for correct directions

RULE 5 - DISTINCT USAGE:
- "Which X..." / "Find X who..." with JOINs -> RETURN DISTINCT
- "top N" / "first N" -> NO DISTINCT (LIMIT handles uniqueness)
- Multiple MATCH patterns -> likely needs DISTINCT

RULE 6 - LIMIT:
- "top N" / "first N" -> LIMIT N
- No specification -> LIMIT 50
- "most" without number -> ORDER BY ... DESC LIMIT 1

RULE 7 - ORDER BY:
- "top N" / "most" / "highest" -> ORDER BY ... DESC
- "lowest" / "least" / "oldest" -> ORDER BY ... ASC
- "youngest" / "most recent" -> ORDER BY date_prop DESC

RULE 8 - AGGREGATION WITH WITH:
- Use WITH for intermediate aggregations:
  WITH x, COUNT(y) AS cnt WHERE cnt > N RETURN x.name, cnt
- "how many" -> COUNT(*)
- "which X has most Y" -> WITH x, COUNT(y) AS cnt ORDER BY cnt DESC LIMIT 1

RULE 9 - SAME NODE PATTERN:
- "X who both A and B the same Y" -> use same variable on both sides
- CORRECT: MATCH (p)-[:REL1]->(t)<-[:REL2]-(p)
- WRONG: MATCH (p1)-[:REL1]->(t)<-[:REL2]-(p2)

RULE 10 - EXISTS / NOT EXISTS:
- "X who have also done Y" -> WHERE exists{{(x)-[:Y]->(:Z)}}
- "X that have NOT been Y" -> WHERE NOT EXISTS {{(x)<-[:Y]-()}}

RULE 11 - NULL HANDLING:
- When ordering by a property, add: WHERE x.prop IS NOT NULL
- Don't add IS NOT NULL when comparison already filters nulls

RULE 12 - CASE SENSITIVITY:
- For name matching, prefer exact match first
- Use toLower() for case-insensitive search when needed
- Use CONTAINS for partial string matching

RULE 13 - OPTIONAL MATCH:
- Use ONLY when the relationship might not exist for all nodes
- Default to MATCH for required relationships
- "if available" / counting with possible zeros -> OPTIONAL MATCH

RULE 14 - LIST PROPERTIES (UNWIND):
- Properties like countries, languages, genres may be LISTS in Neo4j
- To aggregate over list elements, UNWIND first:
  MATCH (m:Movie) UNWIND m.countries AS country
  WITH country, COUNT(DISTINCT m) AS cnt ORDER BY cnt DESC LIMIT 5
  RETURN country, cnt
- "how many movies per country" -> UNWIND m.countries AS country
- "movies in more than one language" -> UNWIND + collect + size

RULE 15 - COLLECT + SIZE FOR DISTINCTNESS:
- "X in more than N different Y" -> use collect(DISTINCT) + size():
  WITH x, collect(DISTINCT y.prop) AS items WHERE size(items) > N
- "directors who directed in more than one language":
  WITH d, collect(DISTINCT m.languages) AS langs WHERE size(langs) > 1
- Use DISTINCT inside collect() to avoid duplicates

RULE 16 - AGGREGATION BEST PRACTICE (WITH before RETURN):
- For aggregation queries, prefer WITH for intermediate grouping:
  MATCH (a)-[:REL]->(b) WITH b, COUNT(a) AS cnt ORDER BY cnt DESC LIMIT N RETURN b.name, cnt
- This is more reliable than RETURN + ORDER BY for aggregation
- Use AVG() for average: WITH x, AVG(y.prop) AS avg ORDER BY avg DESC
""".strip()


# ---------------------------------------------------------------------------
# Template builder -- assembles the final prompt dynamically
# ---------------------------------------------------------------------------

# In-memory cache: db_name -> built template string
_template_cache: dict[str, str] = {}


def _build_template(db_name: str) -> str:
    """
    Build the generalized Cypher template for *db_name*.

    Steps:
      1. Load schema JSON (SchemaLoader)
      2. Build property warnings (SchemaGraph analysis)
      3. Generate few-shot examples (PatternLibrary + BFS)
      4. Assemble: system prompt + rules + warnings + examples + placeholders
    """
    schema_data = load_schema(db_name)

    # Phase 1 + 2: property warnings from SchemaGraph
    if schema_data:
        warnings = build_property_warnings(schema_data)
        examples = build_examples_for_db(schema_data)
    else:
        warnings = []
        examples = []

    # Format property warnings
    if warnings:
        warnings_section = "\n=== PROPERTY LOCATION WARNINGS (AUTO-DETECTED) ===\n"
        for i, w in enumerate(warnings, 1):
            warnings_section += f"\n{i}. {w}"
        warnings_section += "\n"
    else:
        warnings_section = ""

    # Format few-shot examples
    if examples:
        examples_section = "\n=== FEW-SHOT EXAMPLES (AUTO-GENERATED FROM SCHEMA) ===\n"
        for q, c in examples:
            examples_section += f"\nQ: {q}\n{c}\n"
    else:
        examples_section = ""

    # Assemble template
    template = f"""You are a Cypher expert for a Neo4j graph database.

CRITICAL: Output ONLY the raw Cypher query. NO markdown, NO code blocks, NO explanation.

=== SCHEMA ===
{{schema}}

{warnings_section}

{GENERIC_RULES}

{examples_section}

{{question}}
"""

    logger.info(
        "[CypherTemplate] Built generalized template for '%s': "
        "%d chars, %d warnings, %d examples",
        db_name,
        len(template),
        len(warnings),
        len(examples),
    )
    return template


# ---------------------------------------------------------------------------
# Public API -- signature unchanged for backward compatibility
# ---------------------------------------------------------------------------


def get_cypher_template(db_name: str | None = None) -> str:
    """
    Get the Cypher generation template for the given database.

    The template is dynamically built from schema introspection data
    using the SADP (Schema-Aware Dynamic Prompting) algorithm.

    Unchanged signature -- drop-in replacement for the old hardcoded lookup.
    """
    db = db_name or get_settings().database_name
    if db not in _template_cache:
        _template_cache[db] = _build_template(db)
    return _template_cache[db]


def get_available_databases() -> list[str]:
    """Return database names that have schema data available."""
    return _get_dbs()


def clear_template_cache() -> None:
    """Clear the template cache (useful after schema refresh)."""
    _template_cache.clear()


# Backward compatibility: _TEMPLATE_MAP emulation
# Some code (rag_service.py) imports this directly.
class _LazyTemplateMap(dict):
    """Dict-like that dynamically builds templates on access."""

    def keys(self):
        return get_available_databases()

    def __contains__(self, key):
        return key in get_available_databases()

    def __getitem__(self, key):
        return get_cypher_template(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except Exception:
            return default


_TEMPLATE_MAP = _LazyTemplateMap()
