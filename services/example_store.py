"""
Example Store — tagged few-shot examples for intent-based selection.

Each example has tags indicating what patterns it demonstrates.
Context Assembler uses these tags to select the most relevant examples.
"""

from __future__ import annotations


def get_examples(database: str) -> list[dict]:
    """Get tagged examples for a database."""
    return _EXAMPLES.get(database, [])


_EXAMPLES = {
    "twitter": [
        {"question": "Who are the top 5 users that 'Neo4j' follows?",
         "cypher": "MATCH (me:Me {name: 'Neo4j'})-[:FOLLOWS]->(user:User)\nRETURN user.name, user.screen_name, user.followers\nORDER BY user.followers DESC\nLIMIT 5",
         "tags": ["entity_match", "aggregation", "limit", "FOLLOWS"]},

        {"question": "List the 5 most recent users who started following 'Neo4j'.",
         "cypher": "MATCH (me:Me {screen_name: 'neo4j'})<-[:FOLLOWS]-(user:User)\nRETURN user.screen_name, user.name, user.followers\nORDER BY user.followers DESC\nLIMIT 5",
         "tags": ["entity_match", "reverse_direction", "limit", "FOLLOWS"]},

        {"question": "Who are the users that 'neo4j' mentions most frequently in their tweets?",
         "cypher": "MATCH (me:Me {screen_name: 'neo4j'})-[:POSTS]->(t:Tweet)-[:MENTIONS]->(mentioned:User)\nRETURN mentioned.screen_name, count(t) AS mentions_count\nORDER BY mentions_count DESC",
         "tags": ["entity_match", "multi_hop", "aggregation", "POSTS", "MENTIONS"]},

        {"question": "Find the tweets that contain links posted by users who follow 'Neo4j'.",
         "cypher": "MATCH (u:User)-[:FOLLOWS]->(me:Me {name: 'Neo4j'})\nMATCH (u)-[:POSTS]->(t:Tweet)-[:CONTAINS]->(l:Link)\nRETURN t, l\nLIMIT 25",
         "tags": ["multi_condition", "multi_hop", "FOLLOWS", "POSTS", "CONTAINS"]},

        {"question": "List users who follow Neo4j and have posted tweets using 'Buffer'.",
         "cypher": "MATCH (u:User)-[:FOLLOWS]->(me:Me {name: 'Neo4j'})\nMATCH (u)-[:POSTS]->(t:Tweet)-[:USING]->(s:Source {name: 'Buffer'})\nRETURN DISTINCT u.screen_name, u.name",
         "tags": ["multi_condition", "entity_match", "FOLLOWS", "POSTS", "USING"]},

        {"question": "Show the tweets where 'neo4j' is mentioned and favorites over 100.",
         "cypher": "MATCH (t:Tweet)-[:MENTIONS]->(u:User {screen_name: 'neo4j'})\nWHERE t.favorites > 100\nRETURN t.text, t.favorites, t.created_at",
         "tags": ["entity_match", "filter", "MENTIONS"]},

        {"question": "Identify the top 3 users by the number of people they are following.",
         "cypher": "MATCH (u:User)\nRETURN u.name, u.screen_name, count{(u)-[:FOLLOWS]->(:User)} AS followingCount\nORDER BY followingCount DESC\nLIMIT 3",
         "tags": ["aggregation", "count_pattern", "limit", "FOLLOWS"]},

        {"question": "Which users are amplified by 'Me'?",
         "cypher": "MATCH (me:Me)-[:AMPLIFIES]->(user:User)\nRETURN user.screen_name",
         "tags": ["entity_match", "AMPLIFIES"]},

        {"question": "List the first 5 tweets with the highest number of favorites.",
         "cypher": "MATCH (t:Tweet)\nRETURN t.text, t.favorites\nORDER BY t.favorites DESC\nLIMIT 5",
         "tags": ["aggregation", "limit", "simple"]},

        {"question": "Who does 'neo4j' interact with most frequently?",
         "cypher": "MATCH (me:Me {screen_name: 'neo4j'})-[:INTERACTS_WITH]->(user:User)\nRETURN user.screen_name, COUNT(*) AS interaction_count\nORDER BY interaction_count DESC\nLIMIT 1",
         "tags": ["entity_match", "aggregation", "INTERACTS_WITH"]},
    ],

    "movies": [
        {"question": "Find the top 5 movies with the most votes.",
         "cypher": "MATCH (m:Movie)\nRETURN m.title, m.votes\nORDER BY m.votes DESC\nLIMIT 5",
         "tags": ["aggregation", "limit", "simple"]},

        {"question": "What are the roles of Keanu Reeves in 'The Matrix'?",
         "cypher": "MATCH (p:Person {name: 'Keanu Reeves'})-[r:ACTED_IN]->(m:Movie {title: 'The Matrix'})\nRETURN r.roles",
         "tags": ["entity_match", "rel_property", "ACTED_IN"]},

        {"question": "Find all movies with a rating above 90.",
         "cypher": "MATCH (:Person)-[r:REVIEWED]->(m:Movie)\nWHERE r.rating > 90\nRETURN m.title, r.rating",
         "tags": ["filter", "rel_property", "REVIEWED"]},

        {"question": "List all people who have written and directed the same movie.",
         "cypher": "MATCH (p:Person)-[:WROTE]->(m:Movie)<-[:DIRECTED]-(p)\nRETURN p.name, m.title",
         "tags": ["multi_condition", "variable_reuse", "WROTE", "DIRECTED"]},

        {"question": "List the names of people who acted in movies directed by Nancy Meyers.",
         "cypher": "MATCH (director:Person {name: 'Nancy Meyers'})-[:DIRECTED]->(m:Movie)\nMATCH (actor:Person)-[:ACTED_IN]->(m)\nRETURN DISTINCT actor.name",
         "tags": ["multi_hop", "entity_match", "DIRECTED", "ACTED_IN"]},

        {"question": "Which top 5 people have directed movies with more than 200 votes?",
         "cypher": "MATCH (p:Person)-[:DIRECTED]->(m:Movie)\nWHERE m.votes > 200\nWITH p, count(m) AS num_movies\nORDER BY num_movies DESC\nLIMIT 5\nRETURN p.name, num_movies",
         "tags": ["filter", "aggregation", "limit", "DIRECTED"]},

        {"question": "What are the first 3 movies with a released year of 2008?",
         "cypher": "MATCH (m:Movie)\nWHERE m.released = 2008\nRETURN m.title, m.released\nORDER BY m.title\nLIMIT 3",
         "tags": ["filter", "limit", "simple"]},

        {"question": "Who reviewed the movie with the highest rating?",
         "cypher": "MATCH (m:Movie)<-[r:REVIEWED]-(p:Person)\nRETURN p.name, r.rating, r.summary\nORDER BY r.rating DESC\nLIMIT 1",
         "tags": ["aggregation", "rel_property", "REVIEWED"]},
    ],

    "northwind": [
        {"question": "Which 3 suppliers supply the most products?",
         "cypher": "MATCH (s:Supplier)-[:SUPPLIES]->(p:Product)\nWITH s, count(p) AS productCount\nORDER BY productCount DESC\nLIMIT 3\nRETURN s.companyName, productCount",
         "tags": ["aggregation", "limit", "SUPPLIES"]},

        {"question": "What is the average unitPrice of products ordered in quantities greater than 10?",
         "cypher": "MATCH (:Order)-[r:ORDERS]->(:Product)\nWHERE r.quantity > 10\nRETURN avg(toFloat(r.unitPrice)) AS avgPrice",
         "tags": ["filter", "aggregation", "rel_property", "ORDERS"]},

        {"question": "List all suppliers that provide products to 'Dairy Products' category.",
         "cypher": "MATCH (s:Supplier)-[:SUPPLIES]->(p:Product)-[:PART_OF]->(c:Category {categoryName: 'Dairy Products'})\nRETURN s.companyName",
         "tags": ["entity_match", "multi_hop", "SUPPLIES", "PART_OF"]},
    ],

    "recommendations": [
        {"question": "What are the top 5 highest-rated movies by users?",
         "cypher": "MATCH (m:Movie)<-[r:RATED]-(:User)\nWITH m, avg(r.rating) AS avgRating\nORDER BY avgRating DESC\nLIMIT 5\nRETURN m.title, avgRating",
         "tags": ["aggregation", "limit", "rel_property", "RATED"]},

        {"question": "Which movies have been both acted in and directed by the same person?",
         "cypher": "MATCH (p)-[:ACTED_IN]->(m:Movie)<-[:DIRECTED]-(p)\nRETURN DISTINCT m.title",
         "tags": ["multi_condition", "variable_reuse", "ACTED_IN", "DIRECTED"]},

        {"question": "List movies released before 2000.",
         "cypher": "MATCH (m:Movie)\nWHERE m.year < 2000\nRETURN m.title",
         "tags": ["filter", "simple"]},
    ],
}
