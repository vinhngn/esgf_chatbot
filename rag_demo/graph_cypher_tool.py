from typing import List, Tuple
from pydantic import BaseModel
from langchain.tools import tool
from graph_cypher_chain import get_results


class GraphToolInput(BaseModel):
    question: str
    rewritten: str
    verified_triples: List[Tuple[str, str, str]]
    instance_triples: List[Tuple[str, str, str]]
    history: str


@tool("graph-cypher-tool", args_schema=GraphToolInput)
def graph_cypher_tool(
    question: str,
    rewritten: str,
    verified_triples: List[Tuple[str, str, str]],
    instance_triples: List[Tuple[str, str, str]],
    history: str,
) -> str:
    """
    Converts natural language questions into Cypher queries using schema-based and instance-based semantic triples.

    - Use when the question requires structured data retrieval from Neo4j.
    - Supports both verified triples (from schema) and instance triples (matched to known node values).
    - Ensures correct query generation by combining semantic structure and known values.

    Returns:
        A result string or dict with the Cypher result.
    """
    return get_results(
        question=question,
        rewritten=rewritten,
        verified_triples=verified_triples,
        instance_triples=instance_triples,
        history=history,
    )
