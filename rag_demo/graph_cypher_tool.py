from typing import List, Tuple
from pydantic import BaseModel
from langchain.tools import tool
from graph_cypher_chain import get_results

class GraphToolInput(BaseModel):
    question: str
    rewritten: str
    verified_triples: List[Tuple[str, str, str]]
    history: str

@tool("graph-cypher-tool", args_schema=GraphToolInput)
def graph_cypher_tool(question: str, rewritten: str, verified_triples: List[Tuple[str, str, str]], history: str) -> str:
    """
    Useful when answer requires calculating numerical answers like aggregations.
    Use when question asks for a count or how many.
    Use full question as input.
    Also uses rephrased question, verified triples, and chat history for better Cypher generation.
    """
    return get_results(
    question=question,
    rewritten=rewritten,
    verified_triples=verified_triples,
    history=history
)

