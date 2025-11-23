import streamlit as st
import logging
import re
import urllib.parse
from datetime import datetime, date, time
from retry import retry
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage, AIMessage
from graph_cypher_tool import graph_cypher_tool
from graph_cypher_chain import graph, parse_schema
from rag_demo.templates.entity_definitions import entity_definitions
from rag_demo.templates.match_properties_map import match_properties_map


# Shared helpers
def _extract_cypher_queries(chain_result):
    steps = chain_result.get("intermediate_steps", [])
    if not isinstance(steps, list):
        return None, None

    for step in steps:
        if isinstance(step, dict):
            encoded = step.get("query")
            if encoded:
                return encoded, urllib.parse.unquote(encoded)
    return None, None


def _normalize_value(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_normalize_value(v) for v in value)
    return value


# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# LLM Configuration
llm = ChatOpenAI(
    api_key=st.secrets["OPENAI_API_KEY"],
    temperature=0.5,
    model="gpt-4o-mini",
)

interpreter_llm = ChatOpenAI(
    api_key=st.secrets["OPENAI_API_KEY"],
    temperature=0.3,
    model="gpt-4o-mini",
)


@st.cache_data(show_spinner=False)
def get_schema_str():
    return (
        "Available Labels:\n"
        + "\n".join(f"- {label}" for label in sorted(schema_labels))
        + "\n\nAvailable Relationships:\n"
        + "\n".join(f"- {rel}" for rel in sorted(schema_relationships))
        + "\n"
    )


def strip_quotes(s):
    return s.strip("'").strip('"')


# Fetch & parse schema once
graph.refresh_schema()
schema_text = graph.get_schema
schema_labels, schema_relationships = parse_schema(schema_text)
logging.info("✅ Loaded schema labels:")
for label in sorted(schema_labels):
    logging.info(f"   - {label}")

logging.info("✅ Loaded schema relationships:")
for rel in sorted(schema_relationships):
    logging.info(f"   - {rel}")
schema_labels_str = "\n".join(f"- {label}" for label in sorted(schema_labels))
schema_rels_str = "\n".join(f"- {rel}" for rel in sorted(schema_relationships))


# Triple-Extractor Functions


def interpret_question(
    user_question: str, conversation_history: list[dict[str, str]]
) -> tuple[str, list[tuple[str, str, str]]]:
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

    messages: list = [SystemMessage(content=system_prompt)]
    for turn in conversation_history[-3:]:
        messages.append(HumanMessage(content=turn["input"]))
        messages.append(AIMessage(content=turn["output"]))
    messages.append(HumanMessage(content=user_question))

    response = interpreter_llm.invoke(messages).content.strip()

    lines = response.splitlines()
    rewritten = ""
    triples = []
    for line in lines:
        if line.startswith("Rewritten:"):
            rewritten = line.replace("Rewritten:", "").strip()
        elif re.match(r"^\d+\.", line):
            match = re.search(r"\(([^,]+), ([^,]+), ([^)]+)\)", line)
            if match:
                triples.append(tuple(strip_quotes(x.strip()) for x in match.groups()))
    return rewritten, triples


def interpret_question_with_schema(
    user_question: str, conversation_history: list[dict[str, str]], schema_str: str
) -> tuple[str, list[tuple[str, str, str]]]:
    system_prompt = (
        f"""
You are a Neo4j graph assistant.

Your job is to:
1. Rewrite vague or ambiguous user questions into **clear, formal English**.
2. Extract semantic triples using **only the approved node labels and relationship types** below.

### STRICT INSTRUCTIONS ###
- All triples must follow the format: (SubjectLabel, RELATIONSHIP_TYPE, ObjectLabel)
- Subject and Object MUST be one of the valid node labels listed below.
- Relationship MUST be from the allowed relationship types.
- DO NOT use `?`, `UNKNOWN`, or invent new labels or relationships.
- If a required element is missing, leave out the triple entirely.
- If no valid triple can be made, just say: `Rewritten: <clarified question>` and no triples.

### Allowed Node Labels:
{schema_labels_str}

### Allowed Relationship Types:
{schema_rels_str}

Output format:
Rewritten: <clarified question>
Triples:
1. (<subject_label>, <relationship_type>, <object_label>)
2. ...
""".strip()
        + "\n\n"
        + entity_definitions
    )

    messages: list = [SystemMessage(content=system_prompt)]
    for turn in conversation_history[-3:]:
        messages.append(HumanMessage(content=turn["input"]))
        messages.append(AIMessage(content=turn["output"]))
    messages.append(HumanMessage(content=user_question))

    response = interpreter_llm.invoke(messages).content.strip()

    lines = response.splitlines()
    rewritten = ""
    triples = []
    for line in lines:
        if line.startswith("Rewritten:"):
            rewritten = line.replace("Rewritten:", "").strip()
        elif re.match(r"^\d+\.", line):
            match = re.search(r"\(([^,]+), ([^,]+), ([^)]+)\)", line)
            if match:
                triples.append(tuple(strip_quotes(x.strip()) for x in match.groups()))
    return rewritten, triples


# Triple Verification


def verify_triples(triples, schema_labels, schema_relationships):
    verified_triples = []
    instance_triples = []

    # Collect all literals from subject/object that are NOT labels or relationships
    literals = set()
    for s, p, o in triples:
        s_clean = strip_quotes(s)
        o_clean = strip_quotes(o)
        if s_clean not in schema_labels and s_clean not in schema_relationships:
            literals.add(s_clean)
        if o_clean not in schema_labels and o_clean not in schema_relationships:
            literals.add(o_clean)

    # Try matching each literal across schema labels + their properties
    for literal in literals:
        for label in schema_labels:
            properties_to_try = match_properties_map.get(
                label, ["name"]
            )  # fallback to "name"
            for prop in properties_to_try:
                try:
                    query = f"""
                    MATCH (n:{label})
                    WHERE toLower(toString(n.{prop})) = toLower(toString($name))
                    RETURN n LIMIT 1
                    """
                    result = graph.query(query, {"name": literal})
                    if result and result[0].get("n"):
                        triple = (literal, "instanceOf", label)
                        if triple not in instance_triples:
                            instance_triples.append(triple)
                            logging.info(
                                f"🔎 Matched instance: {literal} as {label}.{prop}"
                            )
                except Exception as e:
                    logging.warning(
                        f"⚠️ Error checking {literal} on {label}.{prop}: {e}"
                    )

    # Validate triples against schema relationships
    for s, p, o in triples:
        if p in schema_relationships and s in schema_labels and o in schema_labels:
            verified_triples.append((s, p, o))
        else:
            logging.warning(f"❌ Rejected triple: ({s}, {p}, {o})")
            if s not in schema_labels:
                logging.warning(f"   🚫 Invalid subject: {s}")
            if o not in schema_labels:
                logging.warning(f"   🚫 Invalid object: {o}")

    return verified_triples, instance_triples


# Conversation History

conversation_history = []

# Main LLM Pipeline


def process_with_llm(question: str) -> str:
    # Rebuild conversation_history from Streamlit session
    conversation_history.clear()
    for msg in st.session_state.get("messages", []):
        if msg["role"] == "user":
            conversation_history.append({"input": msg["content"], "output": ""})
        elif msg["role"] == "ai" and conversation_history:
            conversation_history[-1]["output"] = msg["content"]

    conversation_text = "\n".join(
        [
            f"User: {msg['input']}\nBot: {msg['output']}"
            for msg in conversation_history[-3:]
        ]
    )

    # Retry loop for triple extraction
    MAX_ATTEMPTS = 5
    attempt = 0
    verified_triples = []
    instance_triples = []
    triples = []
    rewritten = ""

    while attempt < MAX_ATTEMPTS and not verified_triples:
        if attempt == 0:
            rewritten, triples = interpret_question(question, conversation_history)
        else:
            logging.warning(
                f"Retry #{attempt}: no valid triples yet — using schema-enforced mode."
            )
            schema_str = get_schema_str()

            st.code(schema_str, language="markdown")
            rewritten, triples = interpret_question_with_schema(
                question, conversation_history, schema_str
            )

        # Fix: preserve instance_triples across retries
        temp_verified, temp_instance = verify_triples(
            triples, schema_labels, schema_relationships
        )

        for t in temp_instance:
            if t not in instance_triples:
                instance_triples.append(t)

        if temp_verified:
            verified_triples = temp_verified

        attempt += 1

    if not verified_triples:
        if not verified_triples:
            logging.warning(
                f"❌ Still no verified triples after {attempt} attempts — using unverified ones: {triples}"
            )

        verified_triples = triples
        if not instance_triples:
            logging.warning("⚠️ No instance triples found — falling back without them.")

    st.write(f"Input: {question}")
    st.write(f"Rewritten: {rewritten}")
    st.write(f"Verified Triples: {verified_triples}")
    st.write(f"Instance Triples: {instance_triples}")

    # Send dict payload to tool
    logging.info(f"💾 FINAL instance_triples passed to LLM: {instance_triples}")
    tool_output = graph_cypher_tool.invoke(
        {
            "question": question,
            "rewritten": rewritten,
            "verified_triples": verified_triples,
            "instance_triples": instance_triples,
            "history": conversation_text,
        }
    )

    if isinstance(tool_output, dict):
        result_payload = tool_output
        encoded_query, decoded_query = _extract_cypher_queries(tool_output)
    else:
        result_payload = {"result": tool_output}
        encoded_query, decoded_query = "", ""

    encoded_query = encoded_query or ""
    decoded_query = decoded_query or ""
    result_payload["result"] = _normalize_value(result_payload.get("result"))
    result_only = result_payload.get("result")
    if not result_only:
        result_only = result_payload.get("error") or "No results found."
    if decoded_query:
        st.code(decoded_query, language="cypher")

    # --- Build Neo4j Browser link dynamically based on current secrets.toml ---
    neo4j_uri = st.secrets.get("NEO4J_URI", "neo4j+s://demo.neo4jlabs.com")
    neo4j_user = st.secrets.get("NEO4J_USERNAME", "")
    neo4j_db = st.secrets.get("NEO4J_DATABASE", "")

    # Extract host (for demo.neo4jlabs.com case)
    host = "demo.neo4jlabs.com"
    if "://" in neo4j_uri:
        host = neo4j_uri.split("://", 1)[1].split(":")[0].strip("/")

    # Construct correct Neo4j Browser URL
    if neo4j_user and neo4j_db:
        base_browser_url = f"https://{host}:7473/browser/?dbms=neo4j://{neo4j_user}@{host}&db={neo4j_db}"
    else:
        base_browser_url = f"https://{host}:7473/browser/"

    if encoded_query:
        neo4j_link = f"[Open Neo4J]({base_browser_url}&cmd=edit&arg={encoded_query})"
    else:
        neo4j_link = f"[Open Neo4J]({base_browser_url})"

    if not result_only:
        final_response = (
            f"It appears that there are no models that include the requested variable in the database. "
            f"Please click here to access the knowledge graph: {neo4j_link}"
        )
    else:
        final_prompt = f"""
Based on the conversation and the user question, provide a relevant and helpful response.

Conversation:
{conversation_text}

Current question: {question}
Rewritten question: {rewritten}

Here is the output from the database:
{result_only}

Please process the output and answer the user question clearly.
Always end your answer with the exact phrase:
"Please click here to access the knowledge graph: [[button_query]]"
Do not use any other wording for the link.
""".strip()

        final_response = llm.invoke(final_prompt).content.strip()
        final_response = final_response.replace("[[button_query]]", neo4j_link)

    conversation_history.append({"input": question, "output": final_response})

    return final_response


# Public Function


@retry(tries=2, delay=10)
def get_results(question: str) -> dict:
    llm_processed_output = process_with_llm(question)
    return {
        "input": question,
        "output": llm_processed_output,
    }
