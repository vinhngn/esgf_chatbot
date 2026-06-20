# Teacher Trace Excerpt

Question: List the 5 most recent users who started following 'Neo4j'.

Trace ID: d180151c7d

Final Cypher returned by Cypher LLM:

`cypher
MATCH (neo4j:Me {screen_name: 'neo4j'})<-[:FOLLOWS]-(user:User) RETURN user.screen_name, user.name ORDER BY user.followers DESC LIMIT 5
`

What this proves:

- Schema grounding is not the final Cypher.
- The grounding LLM returns a JSON object: labels, relationships, node properties, paths, entity bindings, return contract, and optional metric contract.
- The validator checks that JSON against the live Neo4j schema, drops unsafe/hallucinated items, and formats a grounded schema text block.
- That grounded schema text is inserted into the final Cypher prompt.
- The Cypher LLM returns one raw Cypher query string, which is then EXPLAIN-validated and executed.

`	ext
INFO: [PipelineTrace:d180151c7d] [GROUND-03] Grounding LLM return contract: raw text parsed into a JSON object
{
  "raw_llm_text": "{\n  \"intent\": \"List recent users who followed a specific user\",\n  \"labels\": [\"User\", \"Me\"],\n  \"relationships\": [\"FOLLOWS\"],\n  \"node_properties\": {\n    \"User\": [\"screen_name\", \"name\"],\n    \"Me\": [\"screen_name\"]\n  },\n  \"rel_properties\": {\n    \"FOLLOWS\": []\n  },\n  \"paths\": [\n    {\"from\": \"User\", \"relationship\": \"FOLLOWS\", \"to\": \"Me\"}\n  ],\n  \"entity_bindings\": [\n    {\"text\": \"Neo4j\", \"candidate_label\": \"Me\", \"candidate_property\": \"screen_name\"}\n  ],\n  \"return_contract\": [\"User.screen_name\", \"User.name\"],\n  \"metric_contract\": \"recently followed\",\n  \"notes\": [\"The question specifies a user by screen name, which is mapped to the Me label.\"]\n}",
  "parsed_json": {
    "intent": "List recent users who followed a specific user",
    "labels": [
      "User",
      "Me"
    ],
    "relationships": [
      "FOLLOWS"
    ],
    "node_properties": {
      "User": [
        "screen_name",
        "name"
      ],
      "Me": [
        "screen_name"
      ]
    },
    "rel_properties": {
      "FOLLOWS": []
    },
    "paths": [
      {
        "from": "User",
        "relationship": "FOLLOWS",
`

`	ext
INFO: [PipelineTrace:d180151c7d] [GROUND-04] Grounding JSON validated against the runtime schema
{
  "validated_labels": [
    "Me",
    "User"
  ],
  "validated_relationships": [
    "FOLLOWS"
  ],
  "validated_paths": [
    {
      "from": "User",
      "relationship": "FOLLOWS",
      "to": "Me"
    }
  ],
  "entity_bindings": [
    {
      "text": "Neo4j",
      "candidate_label": "Me",
      "candidate_property": "screen_name"
    }
  ],
  "return_contract": [
    "User.screen_name",
    "User.name"
  ],
  "metric_contract": "",
`

`	ext
INFO: [PipelineTrace:d180151c7d] [GROUND-06] Schema grounder return value: text inserted into the final Cypher prompt
=== GROUNDED SUBSCHEMA (twitter) ===

Nodes:
  - Me {screen_name}
  - User {name, screen_name}

Relationships:
  - (:User)-[:FOLLOWS]->(:Me)

Entity bindings:
  - "Neo4j" -> Me.screen_name

Return contract:
  - User.screen_name
  - User.name

INFO: [Chain] Using grounded subschema (242 chars)
`

`	ext
INFO: [PipelineTrace:d180151c7d] [CHAIN-05] Full final prompt sent to the Cypher LLM (attempt 1)
Output only one raw Cypher query.

=== SCHEMA ===
=== GROUNDED SUBSCHEMA (twitter) ===

Nodes:
  - Me {screen_name}
  - User {name, screen_name}

Relationships:
  - (:User)-[:FOLLOWS]->(:Me)

Entity bindings:
  - "Neo4j" -> Me.screen_name

Return contract:
  - User.screen_name
  - User.name


You are a Cypher expert for the Neo4j Twitter graph database.

=== SHARED CORE RULES ===
- Output only one raw Cypher query. No markdown, no explanations.
- Use only labels, relationships, properties, and directions that appear in the schema or selected examples.
- Treat the schema as the source of truth for graph structure.
- Use domain facts for semantic meaning, domain hints for relationship guidance, and examples for query shape.
`

`	ext
INFO: [PipelineTrace:d180151c7d] [CHAIN-06] Raw return from the Cypher LLM: one text string
{
  "return_type": "str",
  "raw_text": "MATCH (neo4j:Me {screen_name: 'neo4j'})<-[:FOLLOWS]-(user:User) RETURN user.screen_name, user.name ORDER BY user.followers DESC LIMIT 5"
}
`

`	ext
INFO: [PipelineTrace:d180151c7d] [CHAIN-09] Neo4j execution completed
{
  "type": "list",
  "row_count": 5,
  "sample_rows": [
    {
      "user.screen_name": "verified",
      "user.name": "Twitter Verified"
    },
    {
      "user.screen_name": "6BillionPeople",
      "user.name": "MarQuis Trill � Youngest Black Billionaire"
    }
  ]
}
INFO: 127.0.0.1 - - [19/Jun/2026 23:39:42] "POST /api/text2cypher HTTP/1.1" 200 -
`