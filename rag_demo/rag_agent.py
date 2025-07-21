import streamlit as st
from retry import retry
from langchain_community.llms import Ollama
from langchain_openai import ChatOpenAI
from graph_cypher_tool import graph_cypher_tool
from graph_cypher_chain import graph, parse_schema
from langchain.schema import HumanMessage, SystemMessage, AIMessage
import re
import logging

# === LLM Configuration ===

llm = ChatOpenAI(
    openai_api_key=st.secrets["OPENAI_API_KEY"],
    temperature=0.5,
    model_name="gpt-4o-mini"
)

interpreter_llm = ChatOpenAI(
    openai_api_key=st.secrets["OPENAI_API_KEY"],
    temperature=0.3,
    model_name="gpt-4o-mini"
)

# === Fetch & parse schema once ===
graph.refresh_schema()
schema_text = graph.get_schema
schema_labels, schema_relationships = parse_schema(schema_text)

# === Triple-Extractor Function ===

def interpret_question(user_question: str, conversation_history: list[dict[str, str]]) -> tuple[str, list[tuple[str,str,str]]]:
    system_prompt = (
        "You are a Neo4j graph assistant. Your job is to: \n"
        "1. Rewrite vague or unclear user questions into clear, formal English.\n"
        "2. Extract **semantic triples** from the clarified question using Neo4j schema terms.\n\n"
        "Each triple must be in the format: (subject, predicate, object)\n"
        "- Use `?` for the variable being asked about.\n"
        "- Use `UNKNOWN` if an entity isn't specified explicitly.\n\n"
        "Output format MUST be:\n"
        "Rewritten: <clarified question>\n"
        "Triples:\n"
        "1. (subject, predicate, object)\n"
        "2. ...\n\n"
        "Be concise. Do NOT add explanation or extra commentary.\n"
    )

    messages = [SystemMessage(content=system_prompt)]

    for turn in conversation_history[-2:]:
        messages.append(HumanMessage(content=turn['input']))
        messages.append(AIMessage(content=turn['output']))

    messages.append(HumanMessage(content=user_question))

    response = interpreter_llm(messages).content.strip()

    lines = response.splitlines()
    rewritten = ""
    triples = []
    for line in lines:
        if line.startswith("Rewritten:"):
            rewritten = line.replace("Rewritten:", "").strip()
        elif re.match(r"^\d+\.", line):
            match = re.search(r"\(([^,]+), ([^,]+), ([^)]+)\)", line)
            if match:
                triples.append(tuple(x.strip() for x in match.groups()))
    return rewritten, triples

def interpret_question_with_schema(user_question: str, conversation_history: list[dict[str, str]], schema_str: str) -> tuple[str, list[tuple[str,str,str]]]:
    system_prompt = (
        f"You are a Neo4j graph assistant.\n"
        f"The database schema is:\n{schema_str}\n\n"
        "Your job is to:\n"
        "1. Rewrite vague or unclear user questions into clear, formal English.\n"
        "2. Extract **semantic triples** from the clarified question using ONLY the labels and relationships listed above.\n\n"
        "Each triple must be in the format: (subject, predicate, object)\n"
        "- Use `?` for the variable being asked about.\n"
        "- Use `UNKNOWN` if an entity isn't specified explicitly.\n\n"
        "Output format MUST be:\n"
        "Rewritten: <clarified question>\n"
        "Triples:\n"
        "1. (subject, predicate, object)\n"
        "2. ...\n\n"
        "Be concise. Do NOT add explanation or extra commentary.\n"
    )

    messages = [SystemMessage(content=system_prompt)]

    for turn in conversation_history[-2:]:
        messages.append(HumanMessage(content=turn['input']))
        messages.append(AIMessage(content=turn['output']))

    messages.append(HumanMessage(content=user_question))

    response = interpreter_llm(messages).content.strip()

    lines = response.splitlines()
    rewritten = ""
    triples = []
    for line in lines:
        if line.startswith("Rewritten:"):
            rewritten = line.replace("Rewritten:", "").strip()
        elif re.match(r"^\d+\.", line):
            match = re.search(r"\(([^,]+), ([^,]+), ([^)]+)\)", line)
            if match:
                triples.append(tuple(x.strip() for x in match.groups()))
    return rewritten, triples

# === Softer verification ===

def verify_triples(triples, labels, rels):
    """
    Keep triples where at least the relationship matches the schema.
    Log what doesn't match.
    """
    verified = []
    for subj, rel, obj in triples:
        subj_valid = subj in labels or subj == "UNKNOWN" or subj == "?"
        obj_valid = obj in labels or obj == "UNKNOWN" or obj == "?"
        rel_valid = rel in rels

        if rel_valid:
            verified.append((subj, rel, obj))
        else:
            logging.warning(f"Invalid triple (bad relationship): ({subj}, {rel}, {obj})")

        if not subj_valid:
            logging.warning(f"Invalid subject label: {subj}")
        if not obj_valid:
            logging.warning(f"Invalid object label: {obj}")
    return verified

# === Conversation State ===

conversation_history = []

# === LLM Response Processor ===

def process_with_llm(question: str) -> str:
    conversation_text = "\n".join([
        f"User: {msg['input']}\nBot: {msg['output']}"
        for msg in conversation_history[-2:]
    ])

    # First attempt
    rewritten, triples = interpret_question(question, conversation_history)
    verified_triples = verify_triples(triples, schema_labels, schema_relationships)

    # Retry with schema if no verified triples
    if not verified_triples:
        logging.warning("No verified triples matched schema — retrying with schema provided to LLM.")

        schema_str = (
            "Available Labels:\n" +
            "\n".join(f"- {label}" for label in sorted(schema_labels)) +
            "\n\nAvailable Relationships:\n" +
            "\n".join(f"- {rel}" for rel in sorted(schema_relationships)) +
            "\n"
        )

        rewritten, triples = interpret_question_with_schema(question, conversation_history, schema_str)
        verified_triples = verify_triples(triples, schema_labels, schema_relationships)

    # Fallback if still nothing
    if not verified_triples:
        logging.warning("Still no verified triples after retry — proceeding with unverified triples.")
        verified_triples = triples

    st.write(f'Input = {question}\nRewrite = {rewritten}\nVerified triples: {verified_triples}')

    # Pass rewritten question to graph_cypher_tool as before
    tool_output = graph_cypher_tool.invoke(f"{rewritten}///////////////{conversation_text}")
    result_only = tool_output if isinstance(tool_output, str) else tool_output.get("result", "No results found.")

    final_prompt = f"""
Based on the conversation and the user question, provide a relevant and helpful response.

Conversation:
{conversation_text}

Current question: {question}
Rewritten question: {rewritten}

Here is the output from the database:
{result_only}

Please process the output and answer the user question clearly.
If the output is not empty, you MUST end your answer with the exact phrase:
"Please click here to access the knowledge graph: [[button_query]]"
Do not use any other wording for the link.
""".strip()

    final_response = llm.predict(final_prompt).strip()

    if isinstance(tool_output, dict) and tool_output.get("result"):
        last_query = tool_output.get("intermediate_steps", [{}])[-1].get("query", "")
        final_response = final_response.replace(
            "[[button_query]]",
            f"[Open Neo4J](https://neoforjcmip.templeuni.com/browser/?preselectAuthMethod=NO_AUTH&cmd=edit&arg={last_query})"
        )
    else:
        final_response = final_response.replace("[[button_query]]", "")

    conversation_history.append({
        "input": question,
        "output": final_response
    })

    return final_response

# === Main Access Function ===

@retry(tries=2, delay=10)
def get_results(question: str) -> dict:
    llm_processed_output = process_with_llm(question)
    return {
        "input": question,
        "output": llm_processed_output,
    }
