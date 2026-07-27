# neo4j-t2c

Profile-grounded Text-to-Cypher generation for Neo4j.

The library separates database access, language models, profile storage, and
tracing behind small protocols. Applications can inject their own
implementations without using the legacy process-wide singletons.

## Library API

```python
from neo4j_t2c import create_engine
from neo4j_t2c.adapters import JsonProfileStore, LangChainNeo4jClient

graph = LangChainNeo4jClient(
    url="neo4j+s://example.neo4j.io",
    username="neo4j",
    password="secret",
    database="neo4j",
)

engine = create_engine(
    database="my_graph",
    graph=graph,
    model=my_chat_model,
    profile_store=JsonProfileStore("generated_profiles"),
)

result = engine.generate(
    "List the five newest movies",
    schema=graph.schema,
)

print(result.cypher)
print(result.rows)
```

`GraphClient`, `ChatModel`, `ProfileStore`, and `TraceSink` are structural
protocols. Existing LangChain-compatible chat models already satisfy
`ChatModel` when they provide `invoke(...)`.

## Package Boundaries

- `neo4j_t2c.profiles`: versioned profile data, retrieval, and builders
- `neo4j_t2c.schema`: schema parsing
- `neo4j_t2c.grounding`: schema, property, entity, and vector grounding
- `neo4j_t2c.generation`: prompt construction and Cypher repair
- `neo4j_t2c.execution`: validation, safety, and result normalization
- `neo4j_t2c.observability`: request-scoped tracing
- `neo4j_t2c.adapters`: Neo4j, filesystem, tracing, and compatibility adapters

Modules under `services`, `models`, `templates`, and `utils` remain as
compatibility entry points for the Flask, Streamlit, and benchmark
applications. New integrations should import from `neo4j_t2c`.

## Development

### UI-first setup

The Streamlit Studio is the preferred way to configure the system. You do not
need to create a `.env` file for normal use.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
streamlit run views/streamlit_app.py
```

On Windows, activate the environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

Then open `http://127.0.0.1:8501` and configure everything in the UI:

- `Connection`: Neo4j URI, username, password, physical database, and optional SSH tunnel.
- `AI model`: OpenAI or local OpenAI-compatible model, API key, base URL, and fallback mode.
- `Profile`: build or refresh the selected database profile.
- `Benchmark`: set the T2C framework root, start/resume benchmark runs, and inspect results.

The app stores connections and model settings in a local SQLite/keyring-backed
client store. `.env` remains optional for scripts, CI, and advanced
automation.

### Checks

```bash
python -m pytest -q
ruff check .
```

The compatibility applications continue to support `requirements.txt`.
