import json
import logging
import streamlit as st
import urllib.parse
from retry import retry
from langchain.chains import GraphCypherQAChain
from langchain.chains.conversation.memory import ConversationBufferMemory
from langchain_community.graphs import Neo4jGraph
from langchain.prompts.prompt import PromptTemplate
from langchain_openai import ChatOpenAI
import re
from .templates.cypher_climate_template import CYPHER_GENERATION_TEMPLATE

CYPHER_GENERATION_PROMPT = PromptTemplate(
    input_variables=["schema", "question"], template = CYPHER_GENERATION_TEMPLATE
)

MEMORY = ConversationBufferMemory(
    memory_key="chat_history",
    input_key="question",
    output_key="answer",
    return_messages=True,
)

url = st.secrets["NEO4J_URI"]
username = st.secrets["NEO4J_USERNAME"]
password = st.secrets["NEO4J_PASSWORD"]

graph = Neo4jGraph(url=url, username=username, password=password, sanitize=True)

graph_chain = GraphCypherQAChain.from_llm(
    cypher_llm=ChatOpenAI(
        openai_api_key=st.secrets["OPENAI_API_KEY"],
        temperature=0.3,
        model_name="gpt-4o-mini",
    ),
    qa_llm=ChatOpenAI(
        openai_api_key=st.secrets["OPENAI_API_KEY"],
        temperature=0.7,
        model_name="gpt-4o-mini",
    ),
    graph=graph,
    cypher_prompt=CYPHER_GENERATION_PROMPT,
    validate_cypher=True,
    return_direct=True,
    verbose=True,
    allow_dangerous_requests=True,
    return_intermediate_steps=True,
)


def parse_schema(schema_text: str):
    labels = set()
    relationships = set()

    for line in schema_text.splitlines():
        line = line.strip()
        label_matches = re.findall(r"\(:([A-Za-z0-9_]+)\)", line)
        for label in label_matches:
            labels.add(label)
        rel_matches = re.findall(r"\[:([A-Za-z0-9_]+)\]", line)
        for rel in rel_matches:
            relationships.add(rel)

    return labels, relationships


@retry(tries=2, delay=12)
def get_results(
    question: str,
    rewritten: str = "",
    verified_triples: list[tuple[str, str, str]] = None,
    instance_triples: list[tuple[str, str, str]] = None,  
    history: str = "",
) -> str:

    logging.info(f"Using Neo4j database at URL: {url}")

    verified_triples = verified_triples or []
    triples_text = (
        "\n".join([f"({s}, {r}, {o})" for (s, r, o) in verified_triples or []])
        or "None"
    )
    instance_text = (
        "\n".join([f"({s}, {r}, {o})" for (s, r, o) in instance_triples or []])
        or "None"
    )

    graph.refresh_schema()

    print("\n========= Raw Schema from Neo4j =========\n")
    print(graph.get_schema)

    # FULL PROMPT INJECTION HERE
    prompt = CYPHER_GENERATION_PROMPT.format(
        schema=graph.get_schema,
        question=f"""
Conversation History:
{history}

Now generate a Cypher query for:
{question}

Rewritten Question:
{rewritten}

Verified Triples:
{triples_text}

Instance Triples:
{instance_text}
""",
    )

    print("\n========= Prompt to LLM =========\n")
    print(prompt)

    try:
        chain_result = graph_chain.invoke(
            {"query": question},
            return_only_outputs=True,
        )
    except Exception as e:
        logging.warning(f"Handled exception running GraphCypher chain: {e}")
        return "Sorry, I couldn't find an answer to your question"

    if chain_result is None:
        print("No answer was generated.")
        return "No answer was generated."

    try:
        intermediate = chain_result.get("intermediate_steps", [{}])
        query_raw = intermediate[-1].get("query", "")
        if not query_raw:
            logging.warning("⚠️ No query found in intermediate_steps.")
        else:
            query = query_raw.replace("cypher", "", 1).strip()
            print("\n========= Generated Cypher =========\n")
            print(query)
            encoded_query = urllib.parse.quote(query)
            chain_result["intermediate_steps"][-1]["query"] = encoded_query
    except Exception as e:
        logging.warning(f"Failed to extract Cypher query: {e}")

    print("\n========= Final Result =========\n")
    print(json.dumps(chain_result, indent=2))

    return chain_result
