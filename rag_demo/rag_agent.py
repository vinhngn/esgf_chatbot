import streamlit as st
from retry import retry
from langchain_community.llms import Ollama
from langchain_openai import ChatOpenAI
from graph_cypher_tool import graph_cypher_tool 
from langchain.schema import HumanMessage, SystemMessage, AIMessage


# llm = Ollama(model="llama3")
llm = ChatOpenAI(
    openai_api_key = st.secrets["OPENAI_API_KEY"],
    temperature = 0.5,
    model_name = "gpt-4o-mini"
)

interpreter_llm = ChatOpenAI(
    openai_api_key = st.secrets["OPENAI_API_KEY"],
    temperature = 0.3,
    model_name = "gpt-4o-mini"
)

def interpret_question(user_question: str, conversation_history: list[dict[str, str]]) -> str:
    system_prompt = (

    "You are an assistant that rewrites vague or unclear user questions "
    "into clear and precise English questions, and extracts pattern triples "
    "that can be used to generate Cypher queries for a Neo4j knowledge graph. "

    "Use triples in the form (subject, predicate, object). Use 'UNKNOWN' if the entity is not mentioned. Use '?' for the variable being asked. "




    )
    messages = [
        SystemMessage(content=system_prompt),
        # HumanMessage(content=user_question)
    ]
    
    for turn in conversation_history[-2:]:
        messages.append(HumanMessage(content=turn['input']))
        messages.append(AIMessage(content=turn['output']))
    
    messages.append(HumanMessage(content=user_question))
    
    return interpreter_llm(messages).content.strip()

# Conversation history
conversation_history = []

def process_with_llm(question: str) -> str:
    """Uses LLM to respond based on prior messages and graph result."""

    # Build past conversation text
    conversation_text = "\n".join([
        f"User: {msg['input']}\nBot: {msg['output']}"
        for msg in conversation_history[-2:]
    ])
    
    rewritten = interpret_question(question, conversation_history=conversation_history)
    
    print(f'Input = {question}\nRewrite = {rewritten}')
    st.write(f'Input = {question}\nRewrite = {rewritten}')

    #Run the question through the Cypher tool
    tool_output = graph_cypher_tool.invoke(f"{rewritten}///////////////{conversation_text}")
    result_only = tool_output if tool_output is str or type(tool_output) is str else tool_output.get("result", "No results found.")

    # LLM prompt with only the result
    final_prompt = f"""
Based on the conversation and the user question, provide a relevant and helpful response.

Conversation:
{conversation_text}

Current question: {question}
Rewritten question: {rewritten}

Here is the output from the database:
{result_only}

Please process the output and answer the user question clearly. If the output is not empty MUST add [[button_query]] in last answer.
    """.strip()

    final_response = llm.predict(final_prompt).strip()

    # Inject Neo4j button if there was a result
    if type(tool_output) is not str and tool_output.get("result"):
        last_query = tool_output.get("intermediate_steps", [{}])[-1].get("query", "")
        final_response = final_response.replace(
            "[[button_query]]",
            f"[Open Neo4J](https://neoforj.templeuni.com/browser/?preselectAuthMethod=NO_AUTH&cmd=edit&arg={last_query})"
        )
    else:
        final_response = final_response.replace("[[button_query]]", "")

    # Update history
    conversation_history.append({
        "input": question,
        "output": final_response
    })

    return final_response

@retry(tries = 2, delay = 10)
def get_results(question: str) -> dict:
    """Main processing function for external calls"""
    llm_processed_output = process_with_llm(question = question)
    return {
        "input": question,
        "output": llm_processed_output,
    }


