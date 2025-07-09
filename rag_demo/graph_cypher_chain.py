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

CYPHER_GENERATION_TEMPLATE = """
You are a Cypher expert who translates natural language questions into Cypher queries for a Neo4j graph database.

The graph includes data about:
- Climate models, variables, experiments, institutions, forcings, regions, and resolution
- Connections between models and experiments, or variables produced, or regions covered
- Properties such as `name`, `code`, `cf_standard_name`, `experiment_title`, etc.

Cypher generation rules:
- Use only node types, properties, and relationships defined in the schema.
- Use exact matching for known names (e.g., Variable {{name: "pr"}}).
- Use WHERE clauses for text matching and logical conditions (wrap with parentheses if needed).
- Use OPTIONAL MATCH where appropriate to avoid losing nodes with missing relationships.
- Use ORDER BY where it improves result readability.
- Always include LIMIT to prevent overly large result sets.
- Return all relevant nodes/relationships explicitly and clearly in the RETURN clause — avoid just `RETURN *`.
- Use directional relationships based on schema structure.
- Match labels and node names exactly — do not invent or abbreviate unless known.
- Where applicable, use case-insensitive matching for names (e.g., `=~ '(?i).*foo.*'`).

Schema:
{schema}

Examples:

### Example 1
Natural Language Question:
Show all climate models that include the variable 'pr'.

MATCH (s:Source)-[:PRODUCES_VARIABLE]->(v:Variable {{name: "pr"}})
RETURN s, v
LIMIT 20;

---

### Example 2
Natural Language Question:
Show regional climate models that predict precipitation over Florida.

MATCH (s:Source)-[:PRODUCES_VARIABLE]->(v:Variable {{name: "pr"}})
MATCH (r:RCM)-[:DRIVEN_BY_SOURCE]->(s)
MATCH (r)-[:COVERS_REGION]->(c:Country_Subdivision {{name: "Florida", code: "US.FL"}})
RETURN s, r, c, v;

---

### Example 3
Natural Language Question:
Which variables are associated with the experiment historical, and which models (sources) provide them?

MATCH (e:Experiment {{name: "historical"}})<-[:USED_IN_EXPERIMENT]-(s:Source)-[:PRODUCES_VARIABLE]->(v:Variable)
RETURN e, s, v
LIMIT 20;

---

### Example 4
Natural Language Question:
Show the components, shared models, and realm that ACCESS models belong to.

MATCH (s1:Source)
WHERE s1.name =~ '(?i).*access.*'
OPTIONAL MATCH (s1)-[:IS_OF_TYPE]->(type:SourceType)
OPTIONAL MATCH (s1)-[:HAS_SOURCE_COMPONENT]->(sc:SourceComponent)
OPTIONAL MATCH (sc)<-[:HAS_SOURCE_COMPONENT]-(s2:Source)
OPTIONAL MATCH (s1)-[:APPLIES_TO_REALM]->(realm:Realm)
RETURN s1, type, sc, s2, realm
LIMIT 50;

---

### Example 5
Natural Language Question:
Show all models produced by NASA-GISS, their components, and any other models that use the same components.

MATCH (i:Institute)<-[:PRODUCED_BY_INSTITUTE]-(s1:Source)
WHERE toLower(i.name) = "nasa-giss"
OPTIONAL MATCH (s1)-[:HAS_SOURCE_COMPONENT]->(sc:SourceComponent)
OPTIONAL MATCH (sc)<-[:HAS_SOURCE_COMPONENT]-(s2:Source)
RETURN i, s1, sc, s2
ORDER BY s1.name
LIMIT 50;

---

### Example 6
Natural Language Question:
Which realms are targeted by AOGCM models?

MATCH (s:Source)-[:IS_OF_TYPE]->(type:SourceType)
WHERE type.name = "AOGCM"
OPTIONAL MATCH (s)-[:APPLIES_TO_REALM]->(r:Realm)
RETURN type, s, r
ORDER BY s.name
LIMIT 50;

---

### Example 7
Natural Language Question:
Show pairs of models producing the variable "AEROD_v".

MATCH (s1:Source)-[:PRODUCES_VARIABLE]->(v:Variable)<-[:PRODUCES_VARIABLE]-(s2:Source)
WHERE v.name = "AEROD_v" AND s1 <> s2
RETURN s1, s2, v
ORDER BY s1.name
LIMIT 50;

---

{question}
"""


CYPHER_GENERATION_PROMPT = PromptTemplate(
    input_variables=["schema", "question"], template=CYPHER_GENERATION_TEMPLATE
)

MEMORY = ConversationBufferMemory(
    memory_key="chat_history",
    input_key='question',
    output_key='answer',
    return_messages=True
)

url = st.secrets["NEO4J_URI"]
username = st.secrets["NEO4J_USERNAME"]
password = st.secrets["NEO4J_PASSWORD"]

graph = Neo4jGraph(
    url=url,
    username=username,
    password=password,
    sanitize=True
)

graph_chain = GraphCypherQAChain.from_llm(
    cypher_llm=ChatOpenAI(
        openai_api_key=st.secrets["OPENAI_API_KEY"],
        temperature=0.3,
        model_name="gpt-4o-mini"
    ),
    qa_llm=ChatOpenAI(
        openai_api_key=st.secrets["OPENAI_API_KEY"],
        temperature=0.7,
        model_name="gpt-4o-mini"
    ),
    graph=graph,
    cypher_prompt=CYPHER_GENERATION_PROMPT,
    validate_cypher=True,
    return_direct=True,
    verbose=True,
    allow_dangerous_requests=True,
    return_intermediate_steps=True,
)

@retry(tries=2, delay=12)
def get_results(question: str) -> str:
    [question, history] = question.split('///////////////')
    logging.info(f'Using Neo4j database at URL: {url}')
    print(f'History: {history}')

    graph.refresh_schema()

    print("\n========= Raw Schema from Neo4j =========\n")
    print(graph.get_schema)

    prompt = CYPHER_GENERATION_PROMPT.format(schema=graph.get_schema, question=f'''
Here is history chat bot:
{history}

Now generate a Cypher query for:
{question}''')

    print('\n========= Prompt to LLM =========\n')
    print(prompt)

    try:
        chain_result = graph_chain.invoke(
            {"query": question},
            return_only_outputs=True,
        )
    except Exception as e:
        logging.warning(f'Handled exception running GraphCypher chain: {e}')
        return "Sorry, I couldn't find an answer to your question"

    if chain_result is None:
        print('No answer was generated.')
        return "No answer was generated."

    try:
        query = chain_result["intermediate_steps"][-1]["query"].replace("cypher", "", 1).strip()
        print("\n========= Generated Cypher =========\n")
        print(query)
        chain_result["intermediate_steps"][-1]["query"] = urllib.parse.quote(query)
    except Exception as e:
        logging.warning(f"Failed to extract Cypher query: {e}")

    print("\n========= Final Result =========\n")
    print(json.dumps(chain_result, indent=2))

    return chain_result
