import streamlit as st
from retry import retry
from langchain_community.llms import Ollama
from langchain_openai import ChatOpenAI
from graph_cypher_tool import graph_cypher_tool
from langchain.schema import HumanMessage, SystemMessage, AIMessage

# === LLM Configuration ===

# llm = Ollama(model="llama3")
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

# === Triple-Extractor Function ===

def interpret_question(user_question: str, conversation_history: list[dict[str, str]]) -> str:
    system_prompt = (
        "You are a Neo4j graph assistant. Your job is to: \n"
        "1. Rewrite vague or unclear user questions into clear, formal English.\n"
        "2. Extract **semantic triples** from the clarified question using Neo4j schema terms.\n\n"
        "Each triple must be in the format: (subject, predicate, object)\n"
        "- Use real labels/relationships from the Neo4j schema (e.g., Source, Variable, PRODUCES_VARIABLE).\n"
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

    # Add latest 2 interactions for context
    for turn in conversation_history[-2:]:
        messages.append(HumanMessage(content=turn['input']))
        messages.append(AIMessage(content=turn['output']))

    # Add current question
    messages.append(HumanMessage(content=user_question))

    return interpreter_llm(messages).content.strip()

# === Conversation State ===

conversation_history = []

# === LLM Response Processor ===

def process_with_llm(question: str) -> str:
    """Uses LLM to respond based on prior messages and graph result."""
    conversation_text = "\n".join([
        f"User: {msg['input']}\nBot: {msg['output']}"
        for msg in conversation_history[-2:]
    ])

    rewritten = interpret_question(question, conversation_history)
    print(f'Input = {question}\nRewrite = {rewritten}')
    st.write(f'Input = {question}\nRewrite = {rewritten}')

    # Pass to Cypher Tool
    tool_output = graph_cypher_tool.invoke(f"{rewritten}///////////////{conversation_text}")
    result_only = tool_output if isinstance(tool_output, str) else tool_output.get("result", "No results found.")

    # Final answer prompt to LLM
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

    # Insert Neo4j button if result exists
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
    """Main processing function for external calls"""
    llm_processed_output = process_with_llm(question)
    return {
        "input": question,
        "output": llm_processed_output,
    }
