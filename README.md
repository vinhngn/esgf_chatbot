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

```powershell
py -m pip install -e ".[dev]"
py -m pytest -q
ruff check .
```

The compatibility applications continue to use `requirements.txt`.
