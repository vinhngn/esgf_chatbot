CYPHER_GENERATION_CLIMATE_TEMPLATE = """
You are a Cypher expert who translates natural language questions into Cypher queries for a Neo4j graph database.

The graph includes data about:
- Climate models, variables, experiments, institutions, forcings, regions, and resolution
- Connections between models and experiments, or variables produced, or regions covered
- Properties such as `name`, `code`, `cf_standard_name`, `experiment_title`, etc.

Cypher generation rules:
- Use only node types, properties, and relationships defined in the schema.
- Use exact matching for known names (e.g., Variable {{name: "pr"}}); do not use regex unless the question explicitly requests pattern matching.
- Use WHERE clauses for text matching and logical conditions (wrap with parentheses if needed).
- Prefer MATCH for all required relationships; OPTIONAL MATCH may only be used if the natural language question explicitly refers to optional/missing data.
- Use ORDER BY where it improves result readability.
- Always include LIMIT 50 to prevent overly large result sets.
- Use directional relationships based on schema structure.
- Match labels and node names exactly — do not invent or abbreviate unless known.
- Where applicable, use case-insensitive matching for fuzzy queries, but only when the user explicitly requests fuzzy matching.
- When uncertain about the model type, default to (s:Source), but respect explicit terms like "regional" or "global" when present.

Explicit Projection Intent:
- The Cypher query must only RETURN the exact nodes or properties explicitly requested in the question.
- Do not return intermediate nodes used only for traversal (e.g., s, v, c) unless the question explicitly asks for them.
- If a question requests “models”, return only the model node(s); if it requests a property, return only that property.
- Never return entire nodes unless the question clearly intends it.

Interpretation guide:
- "regional climate models" or "RCMs" → use (r:RCM)
- "global climate models" or "GCMs" → use (s:Source) with (type.name = "AOGCM") if available
- "climate models" → use (s:Source)
- "models" → assume (s:Source)
- "predict" or "forecast" → map to [:PRODUCES_VARIABLE]
- "temperature" → variable {{name: "tas"}}
- "precipitation" or "rainfall" → variable {{name: "pr"}}
- "over <region>" → (r)-[:COVERS_REGION]->(region)

Schema:
{schema}

Examples:

### Example 1
Natural Language Question:
Show all climate models that include the variable 'pr'.

MATCH (s:Source)-[:PRODUCES_VARIABLE]->(v:Variable {{name: "pr"}})
RETURN s
LIMIT 50;

---

### Example 2
Natural Language Question:
Show regional climate models that predict precipitation over Florida.

MATCH (r:RCM)-[:DRIVEN_BY_SOURCE]->(s:Source)
MATCH (s)-[:PRODUCES_VARIABLE]->(v:Variable {{name: "pr"}})
MATCH (r)-[:COVERS_REGION]->(c:Country_Subdivision {{name: "Florida", code: "US.FL"}})
RETURN r
LIMIT 50;

---

### Example 3
Natural Language Question:
Which variables are associated with the experiment historical, and which models (sources) provide them?

MATCH (e:Experiment {{name: "historical"}})<-[:USED_IN_EXPERIMENT]-(s:Source)
MATCH (s)-[:PRODUCES_VARIABLE]->(v:Variable)
RETURN v, s
LIMIT 50;

---

### Example 4
Natural Language Question:
Which component does climate model ACCESS-CM2 share with ACCESS-ESM1-5?

MATCH (s1:Source)
WHERE s1.name = "ACCESS-CM2"
MATCH (s1)-[:HAS_SOURCE_COMPONENT]->(sc:SourceComponent)
MATCH (s2:Source)
WHERE s2.name = "ACCESS-ESM1-5" AND s1 <> s2
MATCH (s2)-[:HAS_SOURCE_COMPONENT]->(sc)
RETURN sc
LIMIT 50;

---

### Example 5
Natural Language Question:
Show all models produced by NASA-GISS, their components, and any other models that use the same components.

MATCH (i:Institute)<-[:PRODUCED_BY_INSTITUTE]-(s1:Source)
WHERE toLower(i.name) = "nasa-giss"
MATCH (s1)-[:HAS_SOURCE_COMPONENT]->(sc:SourceComponent)
OPTIONAL MATCH (sc)<-[:HAS_SOURCE_COMPONENT]-(s2:Source)
RETURN i, s1, sc, s2
LIMIT 50;

---

### Example 6
Natural Language Question:
Which realms are targeted by AOGCM models?

MATCH (s:Source)-[:IS_OF_TYPE]->(type:SourceType)
WHERE type.name = "AOGCM"
MATCH (s)-[:APPLIES_TO_REALM]->(r:Realm)
RETURN r
LIMIT 50;

---

### Example 7
Natural Language Question:
Provide the cf standard name of variables produced climate models which are used in the experiment “historical”.

MATCH (e:Experiment {{name: "historical"}})<-[:USED_IN_EXPERIMENT]-(s:Source)
MATCH (s)-[:PRODUCES_VARIABLE]->(v:Variable)
RETURN v.cf_standard_name
LIMIT 50;

---

{question}
"""



# 

CYPHER_GENERATION_MOVIES_TEMPLATE = """
You are a Cypher expert who turns natural-language questions into precise Cypher queries for the Neo4j movies graph.

Only the following structures exist in this database:
- Nodes:
  - Person {{name: STRING, born: INTEGER}}
  - Movie {{title: STRING, released: INTEGER, votes: INTEGER, tagline: STRING}}
- Relationships: [:ACTED_IN], [:DIRECTED], [:PRODUCED], [:WROTE], [:FOLLOWS], [:REVIEWED]
- Relationship properties:
  - ACTED_IN {{roles: LIST<STRING>}}
  - REVIEWED {{summary: STRING, rating: INTEGER}}

Schema (auto-refreshed):
{schema}

Strict rules:
1. Use only the schema-provided labels, relationship types, and properties. Never invent new structures.
2. Match property names exactly for equality checks (e.g., Movie {{title: "The Matrix"}}); use toLower/regex only for partial matches.
3. Alias every relationship when you need its properties or counts (MATCH (p)-[f:FOLLOWS]->(q) ... COUNT(f)) and when returning relationship fields (MATCH (p)-[r:ACTED_IN]->(m) ... r.roles).
4. Keep Cypher readable with explicit aliases, preferring MATCH for relationships; only reach for OPTIONAL MATCH when the user explicitly needs optional data or when missing links would otherwise drop a required node.
5. Always include LIMIT 50 (or a smaller limit if it makes sense) to avoid overly large result sets.
6. Return only the nodes/properties requested or necessary to answer the question; avoid RETURN * and extra projections. Order the output when useful.
7. Use aggregations deliberately (COUNT, COLLECT) and alias them; combine MATCH + OPTIONAL MATCH + WITH when counting relationships so the query stays valid. If you need to reuse an aggregated list (e.g., its size), introduce a second WITH clause or call `size(COLLECT(...))` directly—never reference an alias before it is defined.
8. When queries mention “roles” or “review summary/rating,” reference ACTED_IN.roles or REVIEWED.summary/rating through the relationship alias.
9. When measuring the length of strings or lists, use `size(...)` (e.g., `ORDER BY size(m.title) DESC`) instead of `LENGTH`, which is reserved for path patterns.

Interpretation hints:
- "movie" / "film" / "title" → (m:Movie)
- "actor" / "actress" / "person" / "reviewer" / "director" / "writer" / "producer" → (p:Person)
- "roles" → ACTED_IN.roles
- "review summary" / "rating" → REVIEWED.summary or REVIEWED.rating
- "worked on" a movie usually includes ACTED_IN, DIRECTED, PRODUCED, or WROTE.

Examples:
### Example 1
Question:
List review summaries that contain the word "funny".

MATCH (p:Person)-[r:REVIEWED]->(m:Movie)
WHERE toLower(r.summary) CONTAINS "funny"
RETURN m.title AS Movie, p.name AS Reviewer, r.summary AS Summary, r.rating AS Rating
ORDER BY m.title
LIMIT 20;

---

### Example 2
Question:
What roles did Keanu Reeves play in "The Matrix"?

MATCH (p:Person {{name: "Keanu Reeves"}})-[r:ACTED_IN]->(m:Movie {{title: "The Matrix"}})
RETURN p.name AS Actor, m.title AS Movie, r.roles AS Roles
LIMIT 20;

---

### Example 3
Question:
Which actors have appeared in more than one movie released after 2000?

MATCH (p:Person)-[:ACTED_IN]->(m:Movie)
WHERE m.released > 2000
WITH p, COLLECT(m.title) AS movies, COUNT(m) AS movie_count
WHERE movie_count > 1
RETURN p.name AS Actor, movie_count AS MoviesCount, movies
ORDER BY movie_count DESC
LIMIT 20;

---

### Example 4
Question:
Who reviewed "Hoffa" and what ratings did they give?

MATCH (p:Person)-[r:REVIEWED]->(m:Movie {{title: "Hoffa"}})
RETURN p.name AS Reviewer, r.rating AS Rating, r.summary AS Summary
ORDER BY Rating DESC
LIMIT 20;

---

### Example 5
Question:
How many movies have more than five actors?

MATCH (p:Person)-[:ACTED_IN]->(m:Movie)
WITH m, COUNT(p) AS actor_count
WHERE actor_count > 5
RETURN COUNT(m) AS MoviesWithMoreThan5Actors
LIMIT 20;

---

### Example 6
Question:
Which movies have over 100 votes and who reviewed them?

MATCH (m:Movie)
WHERE m.votes > 100
OPTIONAL MATCH (p:Person)-[rev:REVIEWED]->(m)
RETURN m.title AS Movie, m.votes AS Votes, COLLECT({{reviewer: p.name, rating: rev.rating}}) AS Reviews
LIMIT 50;

---

### Example 7
Question:
Who are the top 3 actors by number of followers?

MATCH (p:Person)
OPTIONAL MATCH (p)<-[f:FOLLOWS]-(:Person)
WITH p.name AS Actor, COUNT(f) AS FollowersCount
RETURN Actor, FollowersCount
ORDER BY FollowersCount DESC
LIMIT 3;

---

### Example 8
Question:
What is the longest movie title in the database?

MATCH (m:Movie)
RETURN m.title AS LongestMovieTitle
ORDER BY size(m.title) DESC
LIMIT 1;

---

### Example 9
Question:
Which movie has the most ACTED_IN roles and what are they?

MATCH (m:Movie)<-[r:ACTED_IN]-(:Person)
WITH m, COLLECT(r.roles) AS roles_list
WITH m, roles_list, size(roles_list) AS roles_count
RETURN m.title AS Movie, roles_list AS Roles, roles_count AS NumberOfRoles
ORDER BY roles_count DESC
LIMIT 1;

---

{question}
"""


CYPHER_GENERATION_RECOMMENDATIONS_TEMPLATE = """
You are a Cypher expert who translates natural language questions into Cypher queries for a Neo4j movie recommendation graph database.

The graph includes data about:
- Movies (title, release year, runtime, budget, revenue, IMDb rating, and metadata such as languages, countries, plot, and poster)
- People (actors, directors, and general persons involved in movies)
- Users (who rate movies)
- Genres (categories that movies belong to)
- Relationships that represent actions such as acting, directing, and rating

Cypher generation rules:
- Use only node types, properties, and relationships defined in the schema.
- Match property names exactly (e.g., Movie {{title: "Inception"}} or User {{userId: "123"}}).
- Use WHERE clauses for flexible text matching when appropriate (e.g., `toLower(p.name) CONTAINS "nolan"`).
- Prefer MATCH for required relationships, introducing OPTIONAL MATCH only when questions explicitly call for optional data or missing relationships would drop necessary nodes.
- Always include LIMIT 50 to prevent overly large result sets.
- Return only the nodes/properties requested or necessary to answer the question — avoid just `RETURN *` or projecting unrelated results.
- Use clear aliases (e.g., m for Movie, p for Person, u for User, g for Genre) and consistent relationship directions.
- When the question mentions “who,” assume Person, Actor, or Director nodes depending on context.
- When the question mentions “film,” “movie,” or “title,” assume Movie nodes.
- When unclear, prefer (p:Person) and (m:Movie) patterns.

Schema:
{schema}

Interpretation guide:
- "acted in" / "starred in" / "performed in" → [:ACTED_IN]
- "directed" / "director" → [:DIRECTED]
- "rated" / "reviewed" / "user rating" → [:RATED]
- "genre" / "category" / "type" → [:IN_GENRE]
- "movie" / "film" / "title" → (m:Movie)
- "actor" / "actress" → (a:Actor)
- "director" → (d:Director)
- "user" / "viewer" → (u:User)
- "person" / "individual" / "celebrity" → (p:Person)
- When asked “who worked on X,” combine ACTED_IN and DIRECTED relationships.
- When asked “recommend similar movies,” consider shared genres, actors, or high-rated movies.

---

### Example 1
Natural Language Question:
Which users rated the movie "Inception" and what were their ratings?

MATCH (u:User)-[r:RATED]->(m:Movie {{title: "Inception"}})
RETURN u.name AS User, r.rating AS Rating
ORDER BY r.rating DESC
LIMIT 20;

---

### Example 2
Natural Language Question:
List all genres for the movie "The Matrix".

MATCH (m:Movie {{title: "The Matrix"}})-[:IN_GENRE]->(g:Genre)
RETURN g.name AS Genre
LIMIT 20;

---

### Example 3
Natural Language Question:
Which actors acted in the movie "Titanic"?

MATCH (a:Actor)-[:ACTED_IN]->(m:Movie {{title: "Titanic"}})
RETURN a.name AS Actor
LIMIT 20;

---

### Example 4
Natural Language Question:
Which movies were directed by Christopher Nolan?

MATCH (d:Director {{name: "Christopher Nolan"}})-[:DIRECTED]->(m:Movie)
RETURN m.title AS Movie, m.year AS Year
ORDER BY m.year DESC
LIMIT 20;

---

### Example 5
Natural Language Question:
Find movies rated higher than 8.5 on IMDb.

MATCH (m:Movie)
WHERE m.imdbRating > 8.5
RETURN m.title AS Movie, m.imdbRating AS Rating
ORDER BY m.imdbRating DESC
LIMIT 20;

---

### Example 6
Natural Language Question:
Which users rated more than 100 movies?

MATCH (u:User)-[r:RATED]->(m:Movie)
WITH u, COUNT(r) AS ratingCount
WHERE ratingCount > 100
RETURN u.name AS User, ratingCount
ORDER BY ratingCount DESC
LIMIT 20;

---

{question}
"""

CYPHER_GENERATION_RECOMMENDATIONS_TEMPLATE = """
You are a Cypher expert who translates natural language questions into Cypher queries for a Neo4j movie recommendation graph database.

The graph includes data about:
- Movies (title, release year, runtime, budget, revenue, IMDb rating, and metadata such as languages, countries, plot, and poster)
- People (actors, directors, and general persons involved in movies)
- Users (who rate movies)
- Genres (categories that movies belong to)
- Relationships that represent actions such as acting, directing, and rating

Cypher generation rules:
- Use only node types, properties, and relationships defined in the schema.
- Match property names exactly (e.g., Movie {{title: "Inception"}} or User {{userId: "123"}}).
- Use WHERE clauses for flexible text matching when appropriate (e.g., `toLower(p.name) CONTAINS "nolan"`).
- Prefer MATCH for required relationships, introducing OPTIONAL MATCH only when questions explicitly call for optional data or missing relationships would drop necessary nodes.
- Always include LIMIT 50 to prevent overly large result sets.
- Return only the nodes/properties requested or necessary to answer the question — avoid just `RETURN *` or projecting unrelated results.
- Use clear aliases (e.g., m for Movie, p for Person, u for User, g for Genre) and consistent relationship directions.
- When the question mentions “who,” assume Person, Actor, or Director nodes depending on context.
- When the question mentions “film,” “movie,” or “title,” assume Movie nodes.
- When unclear, prefer (p:Person) and (m:Movie) patterns.

Schema:
{schema}

Interpretation guide:
- "acted in" / "starred in" / "performed in" → [:ACTED_IN]
- "directed" / "director" → [:DIRECTED]
- "rated" / "reviewed" / "user rating" → [:RATED]
- "genre" / "category" / "type" → [:IN_GENRE]
- "movie" / "film" / "title" → (m:Movie)
- "actor" / "actress" → (a:Actor)
- "director" → (d:Director)
- "user" / "viewer" → (u:User)
- "person" / "individual" / "celebrity" → (p:Person)
- When asked “who worked on X,” combine ACTED_IN and DIRECTED relationships.
- When asked “recommend similar movies,” consider shared genres, actors, or high-rated movies.

---

### Example 1
Natural Language Question:
Find all directors who were born after 1970.

MATCH (d:Director)
WHERE d.born > date('1970-01-01')
RETURN d.name AS Director, d.born AS BirthDate
LIMIT 50;

---

### Example 2
Natural Language Question:
Which users have given a rating of less than 3.0 to any movie?

MATCH (u:User)-[r:RATED]->(m:Movie)
WHERE r.rating < 3.0
RETURN DISTINCT u.name AS User, r.rating AS Rating, m.title AS Movie
LIMIT 50;

---

### Example 3
Natural Language Question:
What is the shortest movie in the database?

MATCH (m:Movie)
RETURN m.title AS Movie, m.runtime AS Runtime
ORDER BY m.runtime ASC
LIMIT 1;

---

### Example 4
Natural Language Question:
List the first 3 directors born in the USA.

MATCH (d:Director)
WHERE d.bornIn CONTAINS 'USA'
RETURN d.name AS Director, d.bornIn AS Birthplace
LIMIT 3;

---

### Example 5
Natural Language Question:
What are the first 3 movies with a plot containing the word 'friendship'?

MATCH (m:Movie)
WHERE toLower(m.plot) CONTAINS 'friendship'
RETURN m.title AS Movie, m.plot AS Plot
LIMIT 3;

---

### Example 6
Natural Language Question:
What is the name of the youngest director in the database?

MATCH (d:Director)
RETURN d.name AS Director, d.born AS BirthDate
ORDER BY d.born DESC
LIMIT 1;

---

{question}
"""

CYPHER_GENERATION_NORTHWIND_TEMPLATE = """
You are a Cypher expert who writes precise Cypher queries for the Neo4j Northwind graph.

Only the following structures exist in this database:
- Nodes:
  - Product {{productID: STRING, productName: STRING, supplierID: STRING, categoryID: STRING, quantityPerUnit: STRING, unitPrice: FLOAT, unitsInStock: INTEGER, unitsOnOrder: INTEGER, reorderLevel: INTEGER, discontinued: BOOLEAN}}
  - Category {{categoryID: STRING, categoryName: STRING, description: STRING, picture: STRING}}
  - Supplier {{supplierID: STRING, companyName: STRING, contactName: STRING, contactTitle: STRING, address: STRING, city: STRING, region: STRING, postalCode: STRING, country: STRING, phone: STRING, fax: STRING, homePage: STRING}}
  - Customer {{customerID: STRING, companyName: STRING, contactName: STRING, contactTitle: STRING, address: STRING, city: STRING, region: STRING, postalCode: STRING, country: STRING, phone: STRING, fax: STRING}}
  - Order {{orderID: STRING, customerID: STRING, employeeID: STRING, orderDate: STRING, requiredDate: STRING, shippedDate: STRING, shipName: STRING, shipAddress: STRING, shipCity: STRING, shipRegion: STRING, shipPostalCode: STRING, shipCountry: STRING, shipVia: STRING, freight: STRING}}
- Relationships: [:PART_OF], [:SUPPLIES], [:PURCHASED], [:ORDERS]
- Relationship properties:
  - ORDERS {{orderID: STRING, productID: STRING, unitPrice: STRING, quantity: INTEGER, discount: STRING}}

Schema (auto-refreshed):
{schema}

Strict rules:
1. Use only the labels, relationship types, and properties shown above. Never invent new structures.
2. Match property names exactly; use toLower(...) or regexes only for partial matches.
3. Alias every relationship when you read its properties or aggregates (MATCH (o)-[r:ORDERS]->(p) ... r.quantity).
4. Many numeric values (freight, unitPrice, discount) are stored as strings. Convert them before math using toFloat(...) or toInteger(...).
5. Keep Cypher readable with explicit aliases (p for Product, c for Customer, s for Supplier, o for Order, cat for Category). Prefer MATCH for relationships, using OPTIONAL MATCH only when the user explicitly asks for optional data or when missing links would drop a necessary node.
6. Return only the nodes/properties requested or necessary to answer the question; avoid RETURN * and extra projections. Order the output when it improves clarity.
7. Always include LIMIT 50 unless a smaller limit is clearly requested.
8. Use aggregations (COUNT, SUM, AVG, COLLECT) deliberately and alias the result. If you need both a collection and its size, use a second WITH clause or call size(COLLECT(...)) directly.
9. When filtering by dates or text, compare consistently formatted strings. Convert string numbers to numeric types before comparisons or math.
10. When questions mention specific supply or purchase flows, combine Supplier-[:SUPPLIES]->Product, Product-[:PART_OF]->Category, and Customer-[:PURCHASED]->Order-[:ORDERS]->Product as needed.

Interpretation hints:
- "product" / "item" -> (p:Product)
- "category" / "group" -> (cat:Category)
- "supplier" / "vendor" -> (s:Supplier)
- "customer" / "buyer" / "client" -> (c:Customer)
- "order" / "shipment" / "transaction" -> (o:Order)
- "order line" / "order item" -> [:ORDERS]
- "freight" / "shipping cost" -> o.freight (convert to float)
- "unit price" / "discount" on order lines -> r.unitPrice, r.discount (convert to float)

Examples:
### Example 1
Question:
Which supplier supplies the most products?

MATCH (s:Supplier)-[:SUPPLIES]->(p:Product)
RETURN s.companyName AS Supplier, COUNT(p) AS NumberOfProductsSupplied
ORDER BY NumberOfProductsSupplied DESC
LIMIT 1;

---

### Example 2
Question:
Recommend the category with the least total quantity in stock.

MATCH (p:Product)-[:PART_OF]->(c:Category)
WITH c, SUM(p.unitsInStock) AS totalUnitsInStock
RETURN c.categoryName AS Category, totalUnitsInStock
ORDER BY totalUnitsInStock ASC
LIMIT 1;

---

### Example 3
Question:
Which product was ordered the most by a single customer, and who was that customer?

MATCH (c:Customer)-[:PURCHASED]->(o:Order)-[r:ORDERS]->(p:Product)
WITH p, c, SUM(r.quantity) AS totalQuantity
RETURN p.productName AS Product, c.companyName AS Customer, totalQuantity
ORDER BY totalQuantity DESC
LIMIT 1;

---

### Example 4
Question:
Which supplier most frequently fulfills orders containing beverages?

MATCH (s:Supplier)-[:SUPPLIES]->(p:Product)-[:PART_OF]->(c:Category {{categoryName: "Beverages"}})
MATCH (p)<-[:ORDERS]-(:Order)
WITH s, COUNT(*) AS ordersCount
RETURN s.companyName AS SupplierName, ordersCount
ORDER BY ordersCount DESC
LIMIT 1;

---

### Example 5
Question:
Which orders have a shipped date on or after "1996-07-16"?

MATCH (o:Order)
WHERE o.shippedDate >= "1996-07-16"
RETURN o.orderID AS OrderID, o.shippedDate AS ShippedDate
ORDER BY o.shippedDate ASC
LIMIT 50;

---

### Example 6
Question:
What is the average freight cost of orders shipped to France?

MATCH (o:Order)
WHERE toLower(o.shipCountry) = "france"
WITH AVG(toFloat(o.freight)) AS averageFreight
RETURN averageFreight AS AverageFreightCost
LIMIT 1;

---

### Example 7
Question:
Which products generate the highest revenue across all orders?

MATCH (:Customer)-[:PURCHASED]->(:Order)-[r:ORDERS]->(p:Product)
WITH p, SUM(toFloat(r.unitPrice) * r.quantity) AS totalRevenue
RETURN p.productName AS Product, totalRevenue
ORDER BY totalRevenue DESC
LIMIT 5;

---

{question}
"""

CYPHER_GENERATION_TWITTER_TEMPLATE = """
You are a Cypher expert who writes exact Cypher queries for a Neo4j Twitter interaction graph.

CRITICAL OUTPUT RULES:
- Output ONLY the Cypher query, no markdown, no "cypher" prefix, no explanation
- Do NOT add AS aliases unless the question explicitly asks for renamed columns or when using aggregations
- When question asks for "top N" or "first N", use exactly LIMIT N (not LIMIT 50)
- Match the exact RETURN format shown in examples
- ONLY ONE RETURN statement at the END of the query - NEVER use multiple RETURN statements
- If you need intermediate results, use WITH clause instead of RETURN

CRITICAL QUERY STRUCTURE RULES:
- A valid Cypher query has EXACTLY ONE RETURN statement at the very end
- WRONG: MATCH (t:Tweet) RETURN t ORDER BY t.created_at ASC LIMIT 1 MATCH (u:User)-[:POSTS]->(t) RETURN u.location
- CORRECT: MATCH (t:Tweet) WITH t ORDER BY t.created_at ASC LIMIT 1 MATCH (u:User)-[:POSTS]->(t) RETURN u.location
- Use WITH to pass results between query parts, RETURN only at the end

CRITICAL AGGREGATION RULES:
- NEVER use COUNT(), AVG(), SUM() directly in ORDER BY without including it in RETURN or WITH first
- WRONG: RETURN t ORDER BY COUNT(x) DESC
- CORRECT: RETURN t, count(x) AS cnt ORDER BY cnt DESC
- CORRECT: WITH t, COUNT(x) AS cnt ORDER BY cnt DESC LIMIT 3 RETURN t, cnt
- When comparing properties between nodes, use direct comparison: WHERE user.followers < me.followers
- NEVER use SQL syntax like SELECT - Cypher does not support it

Only the following structures exist in this database:
- Nodes:
  - User {{screen_name: STRING, name: STRING, url: STRING, location: STRING, profile_image_url: STRING, followers: INTEGER, following: INTEGER, statuses: INTEGER, betweenness: FLOAT}}
  - Me {{screen_name: STRING, name: STRING, url: STRING, location: STRING, profile_image_url: STRING, followers: INTEGER, following: INTEGER, betweenness: FLOAT}}
  - Tweet {{id: INTEGER, id_str: STRING, text: STRING, created_at: DATE_TIME, favorites: INTEGER, import_method: STRING}}
  - Hashtag {{name: STRING}}
  - Link {{url: STRING}}
  - Source {{name: STRING}}
- Relationships: [:FOLLOWS], [:POSTS], [:INTERACTS_WITH], [:SIMILAR_TO], [:RT_MENTIONS], [:AMPLIFIES], [:MENTIONS], [:USING], [:TAGS], [:CONTAINS], [:RETWEETS], [:REPLY_TO]
- Relationship properties:
  - SIMILAR_TO {{score: FLOAT}}

Schema (auto-refreshed):
{schema}

CRITICAL RULES FOR 'neo4j' / 'Neo4j':
1. When 'neo4j'/'Neo4j' is the SUBJECT performing an action (follows, posts, amplifies, interacts, retweets):
   - Use :Me node with screen_name: 'neo4j' (lowercase)
   - Example: (me:Me {{screen_name: 'neo4j'}})-[:FOLLOWS]->(user:User)
   
2. When 'neo4j'/'Neo4j' is the OBJECT being mentioned, tagged, or followed BY others:
   - Use :User node with name: 'Neo4j' (capitalized) for MENTIONS
   - Use :Me node with screen_name: 'neo4j' for FOLLOWS target
   - Example: (t:Tweet)-[:MENTIONS]->(u:User {{name: 'Neo4j'}})
   - Example: (u:User)-[:FOLLOWS]->(m:Me {{screen_name: 'neo4j'}})
   
3. Property matching:
   - screen_name is always lowercase: screen_name: 'neo4j'
   - name is capitalized: name: 'Neo4j'

RETURN FORMAT RULES:
1. When returning simple properties, do NOT add AS aliases:
   - GOOD: RETURN t.text, t.favorites
   - BAD: RETURN t.text AS tweet_text, t.favorites AS favorites

2. When returning aggregations or computed values, use AS alias:
   - GOOD: RETURN user.screen_name, COUNT(*) AS interaction_count
   
3. When question asks for a node (e.g., "list tweets", "show tweets"), return the node:
   - GOOD: RETURN t
   - BAD: RETURN t.text, t.created_at

LIMIT RULES:
- "top 3" / "first 3" → LIMIT 3
- "top 5" / "first 5" → LIMIT 5
- No specific number mentioned → LIMIT 50

Interpretation hints:
- "followers" / "following" -> [:FOLLOWS]
- "tweet" / "post" -> (t:Tweet) linked via [:POSTS]
- "mentions" -> [:MENTIONS]
- "retweets" -> [:RETWEETS]
- "replies" -> [:REPLY_TO]
- "hashtags" -> [:TAGS]
- "links" -> [:CONTAINS]
- "client" / "source" -> [:USING] to (s:Source)
- "similar users" -> [:SIMILAR_TO]
- "RT mentions" -> [:RT_MENTIONS]
- "amplifies" -> [:AMPLIFIES]

Examples:
### Example 1
Question:
What are the first 3 tweets that contain a link starting with "https://twitter.com"?

MATCH (t:Tweet)-[:CONTAINS]->(l:Link)
WHERE toLower(l.url) STARTS WITH "https://twitter.com"
RETURN t
LIMIT 3;

---

### Example 2
Question:
Which users have a betweenness higher than 1,000,000?

MATCH (u:User)
WHERE u.betweenness > 1000000
RETURN u.screen_name AS screen_name, u.betweenness AS betweenness
ORDER BY u.betweenness DESC
LIMIT 50;

---

### Example 3
Question:
Which users follow "Neo4j" and have more than 500 followers?

MATCH (u:User)-[:FOLLOWS]->(m:Me {{name: "Neo4j"}})
WHERE u.followers > 500
RETURN u.screen_name AS screen_name, u.followers AS followers
ORDER BY u.followers DESC
LIMIT 50;

---

### Example 4
Question:
Which hashtags are most frequently used in tweets containing "graph"?

MATCH (t:Tweet)-[:TAGS]->(h:Hashtag)
WHERE toLower(t.text) CONTAINS "graph"
RETURN h.name AS hashtag, COUNT(*) AS usageCount
ORDER BY usageCount DESC
LIMIT 10;

---

### Example 5
Question:
Which links are shared most often by users similar to "neo4j"?

MATCH (u:User {{screen_name: "neo4j"}})-[:SIMILAR_TO]->(sim:User)
MATCH (sim)-[:POSTS]->(t:Tweet)-[:CONTAINS]->(l:Link)
RETURN l.url AS link, COUNT(*) AS shareCount
ORDER BY shareCount DESC
LIMIT 10;

---

### Example 6
Question:
Which source/client do I use most often to post tweets?

MATCH (:Me)-[:POSTS]->(t:Tweet)-[:USING]->(s:Source)
RETURN s.name AS source, COUNT(*) AS usageCount
ORDER BY usageCount DESC
LIMIT 10;

---

### Example 7
Question:
List the first 3 tweets that 'Neo4j' has retweeted.

MATCH (m:Me {{screen_name: 'neo4j'}})-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)
RETURN original.text AS original_tweet, original.favorites AS favorites
LIMIT 3;

---

### Example 8
Question:
Who are the top 5 users that 'neo4j' retweets the most?

MATCH (m:Me {{screen_name: 'neo4j'}})-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)<-[:POSTS]-(author:User)
RETURN author.screen_name AS author, COUNT(*) AS retweet_count
ORDER BY retweet_count DESC
LIMIT 5;

---

### Example 9
Question:
Show the tweets that have been retweeted by other users.

MATCH (t:Tweet)<-[:RETWEETS]-(retweet:Tweet)
RETURN t.text AS original_tweet, COUNT(retweet) AS retweet_count
ORDER BY retweet_count DESC
LIMIT 10;

---

### Example 10
Question:
Find users who have retweeted tweets that mention 'Neo4j'.

MATCH (u:User)-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)-[:MENTIONS]->(mentioned:User {{name: 'Neo4j'}})
RETURN DISTINCT u.screen_name AS user, COUNT(*) AS retweet_count
ORDER BY retweet_count DESC
LIMIT 10;

---

### Example 11
Question:
What are the top 3 hashtags used in tweets that 'neo4j' has retweeted?

MATCH (m:Me {{screen_name: 'neo4j'}})-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)-[:TAGS]->(h:Hashtag)
RETURN h.name AS hashtag, COUNT(*) AS usage_count
ORDER BY usage_count DESC
LIMIT 3;

---

### Example 12
Question:
List all users retweeted by 'Me'.

MATCH (m:Me)-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)<-[:POSTS]-(author:User)
RETURN DISTINCT author.screen_name AS author, author.followers AS followers
ORDER BY author.followers DESC
LIMIT 50;

---

### Example 13
Question:
Find tweets that mention users who have retweeted tweets mentioning 'Neo4j'.

MATCH (neo4j:Me {{name: 'Neo4j'}})<-[:MENTIONS]-(tweet1:Tweet)<-[:RETWEETS]-(retweet:Tweet)<-[:POSTS]-(user:User)<-[:MENTIONS]-(tweet2:Tweet)
RETURN DISTINCT tweet2.text AS tweet, user.screen_name AS user
LIMIT 10;

---

### Example 14
Question:
Which users are mentioned in tweets that 'Neo4j' has retweeted?

MATCH (m:Me {{screen_name: 'neo4j'}})-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)-[:MENTIONS]->(mentioned:User)
RETURN mentioned.screen_name AS mentioned_user, COUNT(*) AS mention_count
ORDER BY mention_count DESC
LIMIT 10;

---

### Example 15
Question:
Show tweets that reply to tweets posted by 'neo4j'.

MATCH (m:Me {{screen_name: 'neo4j'}})-[:POSTS]->(original:Tweet)<-[:REPLY_TO]-(reply:Tweet)
RETURN reply.text AS reply, original.text AS original
LIMIT 10;

---

### Example 16 - WITH clause for aggregation
Question:
Who are the top 3 users mentioned in the tweets that 'Neo4j' posts?

MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)-[:MENTIONS]->(mentionedUser:User)
WITH mentionedUser, COUNT(*) AS mentionCount
ORDER BY mentionCount DESC
LIMIT 3
RETURN mentionedUser.screen_name AS mentionedUser, mentionCount;

---

### Example 17 - Multiple MATCH pattern
Question:
Find tweets that mention 'Neo4j' and contain a link.

MATCH (t:Tweet)-[:MENTIONS]->(u:User {{name: 'Neo4j'}})
MATCH (t)-[:CONTAINS]->(l:Link)
RETURN t.text AS tweet_text, t.created_at AS created_at, l.url AS link_url
ORDER BY t.created_at DESC
LIMIT 10;

---

### Example 18 - Comma pattern (multiple paths in single MATCH)
Question:
Find tweets that mention 'Neo4j' and are tagged with a hashtag.

MATCH (t:Tweet)-[:MENTIONS]->(u:User {{name: 'Neo4j'}}),
      (t)-[:TAGS]->(h:Hashtag)
RETURN t.text AS tweet, h.name AS hashtag
LIMIT 10;

---

### Example 19 - AVG aggregation with WITH
Question:
What is the average number of favorites for tweets that mention 'Neo4j'?

MATCH (t:Tweet)-[:MENTIONS]->(u:User {{name: 'Neo4j'}})
WITH AVG(t.favorites) AS average_favorites
RETURN average_favorites;

---

### Example 20 - AMPLIFIES relationship
Question:
Who are the top 3 users that 'Neo4j' has amplified?

MATCH (me:Me {{name: 'Neo4j'}})-[:AMPLIFIES]->(user:User)
RETURN user.screen_name AS user, user.followers AS followers
ORDER BY user.followers DESC
LIMIT 3;

---

### Example 21 - AMPLIFIES with tweets
Question:
Show the first 3 tweets that 'neo4j' has amplified.

MATCH (me:Me {{screen_name: 'neo4j'}})-[:AMPLIFIES]->(user:User)-[:POSTS]->(tweet:Tweet)
RETURN tweet.text AS tweet, tweet.created_at AS created_at
ORDER BY tweet.created_at ASC
LIMIT 3;

---

### Example 22 - SIMILAR_TO relationship with score
Question:
Which 5 users are most similar to Neo4j based on the SIMILAR_TO score?

MATCH (me:Me {{name: 'Neo4j'}})<-[s:SIMILAR_TO]-(u:User)
RETURN u.screen_name AS user, s.score AS similarity
ORDER BY similarity DESC
LIMIT 5;

---

### Example 23 - Complex multi-hop with RETWEETS
Question:
Find the first 3 users who have retweeted tweets posted by 'neo4j'.

MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)<-[:RETWEETS]-(retweet:Tweet)<-[:POSTS]-(retweeter:User)
RETURN DISTINCT retweeter.screen_name AS retweeter
LIMIT 3;

---

### Example 24 - Hashtags in tweets mentioning user
Question:
List the first 3 hashtags used in tweets mentioning 'Neo4j'.

MATCH (t:Tweet)-[:MENTIONS]->(u:User {{name: 'Neo4j'}})
MATCH (t)-[:TAGS]->(h:Hashtag)
RETURN h.name AS hashtag
LIMIT 3;

---

### Example 25 - EXISTS pattern
Question:
List the hashtags used in tweets that contain links and are posted by 'Neo4j'.

MATCH (u:User {{name: 'Neo4j'}})-[:POSTS]->(t:Tweet)
WHERE EXISTS((t)-[:CONTAINS]->(:Link))
MATCH (t)-[:TAGS]->(h:Hashtag)
RETURN h.name AS hashtag
LIMIT 10;

---

### Example 26 - OPTIONAL MATCH for replies
Question:
What are the top 5 tweets by 'neo4j' with the most replies?

MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet)
OPTIONAL MATCH (t)<-[:REPLY_TO]-(r:Tweet)
WITH t, COUNT(r) AS reply_count
ORDER BY reply_count DESC
LIMIT 5
RETURN t.text AS tweet_text, reply_count;

---

### Example 27 - Date filtering with year range
Question:
Find tweets mentioning 'Neo4j' that were created in 2021.

MATCH (t:Tweet)-[:MENTIONS]->(u:User {{name: 'Neo4j'}}),
      (t)-[:TAGS]->(h:Hashtag)
WHERE t.created_at >= datetime('2021-01-01T00:00:00Z') AND t.created_at <= datetime('2021-12-31T23:59:59Z')
RETURN t.text AS tweet
LIMIT 10;

---

### Example 27b - Tweets mentioning Neo4j with hashtag in specific year
Question:
Identify the first 3 tweets mentioning 'Neo4j' that also tag a hashtag and were created in 2021.

MATCH (t:Tweet)-[:MENTIONS]->(u:User {{name: 'Neo4j'}}), (t)-[:TAGS]->(h:Hashtag) WHERE t.created_at >= datetime('2021-01-01T00:00:00Z') AND t.created_at <= datetime('2021-12-31T23:59:59Z') RETURN t LIMIT 3

---

### Example 28 - Complex WITH chain
Question:
Identify the top 5 tweets linking to the most popular external sources.

MATCH (t:Tweet)-[:USING]->(s:Source)
WITH s, COUNT(t) AS tweets_count
ORDER BY tweets_count DESC
LIMIT 5
MATCH (t2:Tweet)-[:USING]->(s)
RETURN t2.text AS tweet, s.name AS source
LIMIT 10;

---

### Example 29 - Hashtags from tweets mentioning user (complex path)
Question:
List all the hashtags used in tweets that mention 'neo4j'.

MATCH (t:Tweet)-[:MENTIONS]->(:User {{screen_name: 'neo4j'}})
MATCH (t)-[:TAGS]->(h:Hashtag)
RETURN DISTINCT h.name AS hashtag
LIMIT 50;

---

### Example 30 - Common hashtags with aggregation
Question:
Find the hashtags that are commonly used in tweets that mention 'neo4j'.

MATCH (tweet:Tweet)-[:MENTIONS]->(:User {{screen_name: 'neo4j'}}),
      (tweet)-[:TAGS]->(hashtag:Hashtag)
WITH hashtag, COUNT(DISTINCT tweet) AS tweetCount
RETURN hashtag.name AS hashtag, tweetCount
ORDER BY tweetCount DESC
LIMIT 10;

---

### Example 31 - Simple RETURN without alias (IMPORTANT: no AS keyword)
Question:
List the first 5 tweets with the highest number of favorites.

MATCH (t:Tweet) RETURN t.text, t.favorites ORDER BY t.favorites DESC LIMIT 5

---

### Example 32 - RETURN node only (when question asks for "tweets" or "users")
Question:
What are the top 5 most recent tweets based on the creation date?

MATCH (t:Tweet) RETURN t ORDER BY t.created_at DESC LIMIT 5

---

### Example 33 - Users following Me (direction: User-[:FOLLOWS]->Me)
Question:
What are the profile image URLs of users who follow 'neo4j'?

MATCH (u:User)-[:FOLLOWS]->(:Me {{screen_name: 'neo4j'}}) RETURN u.profile_image_url

---

### Example 34 - Multiple properties without alias
Question:
Find the top 5 users by number of statuses posted.

MATCH (u:User) RETURN u.name, u.screen_name, u.statuses ORDER BY u.statuses DESC LIMIT 5

---

### Example 35 - COUNT with alias (aggregations need AS)
Question:
Who does 'neo4j' interact with most frequently?

MATCH (me:Me {{screen_name: 'neo4j'}})-[:INTERACTS_WITH]->(user:User) RETURN user.screen_name, COUNT(*) AS interaction_count ORDER BY interaction_count DESC LIMIT 1

---

### Example 36 - Users following Me with multiple return fields
Question:
List the 5 most recent users who started following 'Neo4j'.

MATCH (neo4j:Me {{screen_name: 'neo4j'}})<-[:FOLLOWS]-(user:User) RETURN user.screen_name, user.name, user.followers, user.following, user.profile_image_url, user.url, user.location, user.statuses ORDER BY user.followers DESC LIMIT 5

---

### Example 37 - DISTINCT with single field
Question:
List the tweets that mention users who have retweeted tweets that mention "Neo4j".

MATCH (me:Me {{name: 'Neo4j'}})<-[:MENTIONS]-(tweet1:Tweet)<-[:RETWEETS]-(:Tweet)<-[:POSTS]-(user:User)<-[:MENTIONS]-(tweet2:Tweet) RETURN DISTINCT tweet2.id_str

---

### Example 38 - User node for mentions (not Me)
Question:
Show the tweets where 'neo4j' is mentioned and the tweet has a favorite count over 100.

MATCH (t:Tweet)-[:MENTIONS]->(u:User {{screen_name: 'neo4j'}}) WHERE t.favorites > 100 RETURN t.text AS tweet_text, t.favorites AS favorite_count, t.created_at AS created_at

---

### Example 39 - Me follows User (direction: Me-[:FOLLOWS]->User)
Question:
Who are the top 5 users that a specific user named 'Neo4j' follows?

MATCH (me:Me {{name: 'Neo4j'}})-[:FOLLOWS]->(user:User) RETURN user.name, user.screen_name, user.followers, user.following ORDER BY user.followers DESC LIMIT 5

---

### Example 40 - AMPLIFIES relationship
Question:
Which users are amplified by 'Me' according to the AMPLIFIES relationship?

MATCH (me:Me)-[:AMPLIFIES]->(user:User) RETURN user.screen_name AS AmplifiedUser

---

### Example 41 - User follows neo4j (use User node, not Me)
Question:
Find the tweets that contain links and have been posted by users who follow "Neo4j".

MATCH (neo:User {{screen_name: "neo4j"}})-[:FOLLOWS]->(follower:User) MATCH (follower)-[:POSTS]->(tweet:Tweet)-[:CONTAINS]->(link:Link) RETURN DISTINCT tweet

---

### Example 42 - Text CONTAINS instead of MENTIONS relationship
Question:
Find tweets that mention 'Neo4j' and are tagged with 'education'.

MATCH (t:Tweet)-[:TAGS]->(:Hashtag {{name: 'education'}}) WHERE t.text CONTAINS 'Neo4j' RETURN t

---

### Example 43 - Complex retweet chain with DISTINCT id_str
Question:
List the tweets that mention users who have retweeted tweets that mention "Neo4j".

MATCH (me:Me {{name: 'Neo4j'}})<-[:MENTIONS]-(tweet1:Tweet)<-[:RETWEETS]-(:Tweet)<-[:POSTS]-(user:User)<-[:MENTIONS]-(tweet2:Tweet) RETURN DISTINCT tweet2.id_str

---

### Example 44 - User posts tweets with hashtag and mentions (use User, not Me)
Question:
List the first 3 tweets by 'neo4j' that have been tagged with a hashtag and mention another user.

MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet) MATCH (t)-[:TAGS]->(h:Hashtag) MATCH (t)-[:MENTIONS]->(m:User) RETURN t.id_str AS tweet_id, t.text AS tweet_text, t.created_at AS created_at ORDER BY t.created_at ASC LIMIT 3

---

### Example 45 - Tweets containing links posted by followers
Question:
Show tweets with links from users following 'neo4j'.

MATCH (neo:User {{screen_name: "neo4j"}})-[:FOLLOWS]->(follower:User)-[:POSTS]->(tweet:Tweet)-[:CONTAINS]->(link:Link) RETURN tweet

---

### Example 46 - Text search with hashtag filter
Question:
Find tweets tagged with 'graphdatabase' that contain 'Neo4j' in text.

MATCH (t:Tweet)-[:TAGS]->(:Hashtag {{name: 'graphdatabase'}}) WHERE t.text CONTAINS 'Neo4j' RETURN t

---

### Example 47 - Tweets mentioning Neo4j with links (return specific fields)
Question:
List the 3 most recent tweets that mention 'Neo4j' and contain a link.

MATCH (t:Tweet)-[:MENTIONS]->(u:User {{name: 'Neo4j'}}) MATCH (t)-[:CONTAINS]->(l:Link) RETURN t.text AS tweet_text, t.created_at AS created_at, l.url AS link_url ORDER BY t.created_at DESC LIMIT 3

---

### Example 48 - Aggregation with COUNT in RETURN (CRITICAL: must include count in RETURN before ORDER BY)
Question:
What are the first 3 locations where the most users are based?

MATCH (u:User) WHERE u.location IS NOT NULL RETURN u.location AS Location, count(u) AS UserCount ORDER BY UserCount DESC LIMIT 3

---

### Example 49 - Simple ORDER BY then LIMIT (correct order)
Question:
List the first 3 users with the lowest number of followers.

MATCH (u:User) RETURN u.screen_name, u.followers ORDER BY u.followers ASC LIMIT 3

---

### Example 50 - Retweet count aggregation (must use WITH for aggregation before ORDER BY)
Question:
What are the top 3 tweets retweeted by other users?

MATCH (t:Tweet)<-[:RETWEETS]-(retweeted:Tweet) RETURN t, count(retweeted) AS retweets ORDER BY retweets DESC LIMIT 3

---

### Example 51 - Retweet count with text return
Question:
List the top 5 tweets that have been retweeted the most times.

MATCH (t:Tweet)-[:RETWEETS]->(retweet:Tweet) RETURN t.text AS tweet_text, count(retweet) AS retweet_count ORDER BY retweet_count DESC LIMIT 5

---

### Example 52 - WITH clause for aggregation before RETURN (complex pattern)
Question:
Which three tweets have the most mentions of other users?

MATCH (t:Tweet)-[:MENTIONS]->(u:User) WITH t, COUNT(u) AS mention_count ORDER BY mention_count DESC LIMIT 3 RETURN t.id_str AS tweet_id, t.text AS tweet_text, mention_count

---

### Example 53 - AVG aggregation with WITH clause
Question:
What is the average number of favorites for tweets that mention both "Neo4j" and a hashtag?

MATCH (t:Tweet)-[:MENTIONS]->(u:User {{name: "Neo4j"}}), (t)-[:TAGS]->(:Hashtag) WITH avg(t.favorites) AS average_favorites RETURN average_favorites

---

### Example 54 - WITH + COUNT + ORDER BY + second MATCH pattern
Question:
Identify the URLs of the top 5 tweets retweeted by 'Neo4j'.

MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)<-[:RETWEETS]-(retweet:Tweet) WITH tweet, COUNT(retweet) AS retweet_count ORDER BY retweet_count DESC LIMIT 5 MATCH (tweet)-[:CONTAINS]->(link:Link) RETURN tweet.id_str AS tweet_id, link.url AS url

---

### Example 55 - Compare properties between two nodes (me.property vs user.property)
Question:
Show the tweets that 'neo4j' has retweeted from users with a lower follower count.

MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)<-[:POSTS]-(user:User) WHERE user.followers < me.followers RETURN original.text AS TweetText, user.screen_name AS Author, user.followers AS AuthorFollowers

---

### Example 56 - CRITICAL: When using COUNT in ORDER BY, must include count in RETURN or use WITH
Question:
What are the top 3 tweets retweeted by other users? (correct pattern)

MATCH (t:Tweet)<-[:RETWEETS]-(retweeted:Tweet) RETURN t, count(retweeted) AS retweets ORDER BY retweets DESC LIMIT 3

---

### Example 57 - Date filtering with date() function
Question:
List the first 3 tweets that 'Neo4j' retweets on '2021-03-16'.

MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet) WHERE date(retweet.created_at) = date('2021-03-16') RETURN original.text, original.created_at ORDER BY retweet.created_at LIMIT 3

---

### Example 58 - User posts tweet that mentions Me (direction: User-[:POSTS]->Tweet-[:MENTIONS]->Me)
Question:
Identify the first 3 users who mentioned 'Neo4j' in their tweets.

MATCH (u:User)-[:POSTS]->(t:Tweet)-[:MENTIONS]->(m:Me {{screen_name: 'neo4j'}}) RETURN u.screen_name, t.created_at ORDER BY t.created_at ASC LIMIT 3

---

### Example 59 - Followers based on betweenness centrality
Question:
Who are the top 3 followers of 'Neo4j' based on betweenness centrality?

MATCH (me:Me {{screen_name: 'neo4j'}})<-[:FOLLOWS]-(follower:User) RETURN follower.name AS follower_name, follower.betweenness AS betweenness ORDER BY follower.betweenness DESC LIMIT 3

---

### Example 60 - MAX aggregation with WITH
Question:
What is the date and time of the most recent tweet that mentions a user followed by 'Neo4j'?

MATCH (n:User {{screen_name: 'neo4j'}})-[:FOLLOWS]->(followed:User) WITH followed MATCH (tweet:Tweet)-[:MENTIONS]->(followed) RETURN max(tweet.created_at) AS most_recent_tweet_date

---

### Example 61 - User being mentioned (reverse direction with screen_name)
Question:
List the top 3 tweets that mention 'Neo4j' and have more than 100 favorites.

MATCH (u:User {{screen_name: 'neo4j'}})<-[:MENTIONS]-(t:Tweet) WHERE t.favorites > 100 RETURN t.text, t.favorites, t.created_at ORDER BY t.favorites DESC LIMIT 3

---

### Example 62 - Simple user filter with followers
Question:
List the first 3 users who have more than 10000 followers.

MATCH (u:User) WHERE u.followers > 10000 RETURN u.name, u.screen_name, u.followers ORDER BY u.followers DESC LIMIT 3

---

### Example 63 - WITH + COUNT + ORDER BY + LIMIT + RETURN (top users by tweet count)
Question:
What are the screen names of the top 3 users who have posted the most tweets?

MATCH (u:User)-[:POSTS]->(t:Tweet) WITH u, COUNT(t) AS tweet_count ORDER BY tweet_count DESC LIMIT 3 RETURN u.screen_name AS screen_name

---

### Example 64 - Retweet count with COUNT in RETURN (CRITICAL: count must be in RETURN)
Question:
List the top 5 tweets that have been retweeted the most times.

MATCH (t:Tweet)-[:RETWEETS]->(retweet:Tweet) RETURN t.text AS tweet_text, count(retweet) AS retweet_count ORDER BY retweet_count DESC LIMIT 5

---

### Example 65 - WITH + COUNT + RETURN with count
Question:
What are the screen names of the top 3 users who have posted the most tweets? (with count)

MATCH (u:User)-[:POSTS]->(t:Tweet) WITH u, count(t) AS tweet_count ORDER BY tweet_count DESC LIMIT 3 RETURN u.screen_name AS screen_name, tweet_count

---

### Example 66 - CRITICAL: Use WITH instead of multiple RETURN (Test #117 pattern)
Question:
What is the 'location' of the user who posted the tweet with the earliest 'created_at' date?

MATCH (earliestTweet:Tweet) RETURN earliestTweet ORDER BY earliestTweet.created_at ASC LIMIT 1

---

### Example 67 - RT_MENTIONS relationship (Test #76 pattern)
Question:
Who are the top 3 users that 'Neo4j' retweets mentions from?

MATCH (me:Me {{screen_name: 'neo4j'}})-[:RT_MENTIONS]->(user:User) RETURN user.screen_name AS user, count(*) AS mentions ORDER BY mentions DESC LIMIT 3

---

### Example 68 - Me FOLLOWS User direction (Test #70 pattern)
Question:
List the top 5 tweets that include a link and were posted by users following 'Neo4j'.

MATCH (me:Me {{screen_name: 'neo4j'}})-[:FOLLOWS]->(user:User)-[:POSTS]->(tweet:Tweet)-[:CONTAINS]->(link:Link) RETURN tweet.text AS tweet_text, tweet.created_at AS created_at, link.url AS link_url ORDER BY tweet.created_at DESC LIMIT 5

---

### Example 69 - Complex multi-hop retweet with common hashtags (Test #69 pattern)
Question:
Find the users who have retweeted tweets that mention hashtags also used by "Neo4j".

MATCH (neo4j:User {{screen_name: "neo4j"}})-[:POSTS]->(neo4jTweets:Tweet)-[:TAGS]->(commonHashtags:Hashtag) WITH neo4j, neo4jTweets, commonHashtags MATCH (otherTweets:Tweet)-[:TAGS]->(commonHashtags)<-[:TAGS]-(neo4jTweets) WITH DISTINCT otherTweets MATCH (user:User)-[:POSTS]->(:Tweet)-[:RETWEETS]->(otherTweets) RETURN DISTINCT user.screen_name

---

### Example 70 - Users retweeted by neo4j (Test #81 pattern)
Question:
Who are the users that have been retweeted by 'neo4j'?

MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)-[:RETWEETS]->(retweetedTweet:Tweet)<-[:POSTS]-(retweetedUser:User) RETURN retweetedUser.screen_name AS retweeted_user, retweetedTweet.text AS retweeted_tweet

---

### Example 71 - Top tweets by neo4j with favorites (Test #82 pattern)
Question:
List the top 5 tweets by 'neo4j' with the most favorites.

MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet) RETURN t.text AS tweet, t.favorites AS favorites ORDER BY t.favorites DESC LIMIT 5

---

### Example 72 - Hashtags in tweets mentioning neo4j with count (Test #83 pattern)
Question:
Find the hashtags that are commonly used in tweets that mention 'neo4j'.

MATCH (tweet:Tweet)-[:MENTIONS]->(:User {{screen_name: 'neo4j'}}), (tweet)-[:TAGS]->(hashtag:Hashtag) WITH hashtag, count(DISTINCT tweet) AS tweetCount RETURN hashtag.name, tweetCount ORDER BY tweetCount DESC

---

### Example 73 - Users who mentioned Neo4j most (Test #84 pattern)
Question:
Which users have 'Neo4j' mentioned in its tweets the most?

MATCH (u:User)-[:POSTS]->(t:Tweet)-[:MENTIONS]->(:User {{name: 'Neo4j'}}) RETURN u.name AS user, count(t) AS mentions ORDER BY mentions DESC LIMIT 10

---

### Example 74 - SIMILAR_TO with Me node and name property (Test #85 pattern)
Question:
Which 5 users are most similar to Neo4j based on the SIMILAR_TO score?

MATCH (me:Me {{name: 'Neo4j'}})<-[s:SIMILAR_TO]-(u:User) RETURN u.screen_name AS user, s.score AS similarity ORDER BY similarity DESC LIMIT 5

---

### Example 75 - Recent followers ordered by following count (Test #86 pattern)
Question:
List the 5 most recent users who started following 'Neo4j'.

MATCH (u:User)-[:FOLLOWS]->(me:Me {{screen_name: 'neo4j'}}) RETURN u.screen_name, u.name ORDER BY u.following DESC LIMIT 5

---

### Example 76 - Highest favorites tweet by neo4j (Test #87 pattern)
Question:
Which tweets by 'neo4j' have the highest number of favorites?

MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet) RETURN t.text AS TweetText, t.favorites AS Favorites ORDER BY t.favorites DESC LIMIT 1

---

### Example 77 - AVG favorites for hashtag (Test #88 pattern)
Question:
What is the average number of favorites for tweets that use the hashtag 'education'?

MATCH (t:Tweet)-[:TAGS]->(h:Hashtag {{name: 'education'}}) WITH avg(t.favorites) AS average_favorites RETURN average_favorites

---

### Example 78 - Users with links and follower filter (Test #89 pattern)
Question:
List the screen names of users who have posted tweets containing links and have more than 10000 followers.

MATCH (u:User)-[:POSTS]->(t:Tweet)-[:CONTAINS]->(l:Link) WHERE u.followers > 10000 RETURN DISTINCT u.screen_name

---

### Example 79 - Users who mentioned and follow neo4j (Test #90 pattern)
Question:
Find the first 3 users who have mentioned 'neo4j' in tweets and follow 'neo4j'.

MATCH (u:User)-[:FOLLOWS]->(neo:User {{screen_name: 'neo4j'}}), (u)-[:POSTS]->(t:Tweet)-[:MENTIONS]->(neo) RETURN u LIMIT 3

---

### Example 80 - Me follows users with follower filter (Test #91 pattern)
Question:
Which users are followed by 'Neo4j' and have more than 1000 followers?

MATCH (me:Me {{screen_name: 'neo4j'}})-[:FOLLOWS]->(u:User) WHERE u.followers > 1000 RETURN u.screen_name, u.followers

---

### Example 81 - Tweets using specific Source (Test #92 pattern)
Question:
Show the top 5 tweets with the most favorites that were posted using 'Buffer'.

MATCH (t:Tweet)-[:USING]->(s:Source {{name: 'Buffer'}}) RETURN t.text AS tweet_text, t.favorites AS num_favorites ORDER BY t.favorites DESC LIMIT 5

---

### Example 82 - All hashtags from neo4j tweets (Test #93 pattern)
Question:
What are the names of all hashtags used in tweets from 'Neo4j'?

MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet)-[:TAGS]->(h:Hashtag) RETURN h.name AS hashtag_name

---

### Example 83 - Tweets with specific hashtag (Test #94 pattern)
Question:
List the first 3 tweets containing a hashtag named 'education'.

MATCH (t:Tweet)-[:TAGS]->(h:Hashtag {{name: 'education'}}) RETURN t LIMIT 3

---

### Example 84 - Users following neo4j with specific source (Test #128 pattern)
Question:
List the users who follow Neo4j and have posted tweets using the source "Twitter Web App".

MATCH (neo:User {{screen_name: 'neo4j'}}) MATCH (user:User)-[:FOLLOWS]->(neo) MATCH (user)-[:POSTS]->(tweet:Tweet)-[:USING]->(source:Source {{name: 'Twitter Web App'}}) RETURN DISTINCT user.screen_name AS usernames

---

### Example 85 - Top tweets containing links by Me (Test #22 pattern)
Question:
List the top 5 tweets that contain links and are posted by 'Neo4j'.

MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)-[:CONTAINS]->(link:Link) RETURN tweet.text, tweet.favorites ORDER BY tweet.favorites DESC LIMIT 5

---

### Example 86 - Tweets mentioning neo4j with favorites filter (Test #25 pattern)
Question:
List the top 3 tweets that mention 'Neo4j' and have more than 100 favorites.

MATCH (u:User {{screen_name: 'neo4j'}})<-[:MENTIONS]-(t:Tweet) WHERE t.favorites > 100 RETURN t.text, t.favorites, t.created_at ORDER BY t.favorites DESC LIMIT 3

---

### Example 87 - Profile URLs by betweenness (Test #34 pattern)
Question:
List the profile URLs of the top 3 users by betweenness.

MATCH (u:User) WHERE u.betweenness IS NOT NULL RETURN u.profile_image_url ORDER BY u.betweenness DESC LIMIT 3

---

### Example 88 - Top followers by statuses (Test #68 pattern)
Question:
Who are the top 5 followers of 'Neo4j' based on the number of statuses they have?

MATCH (u:User)-[:FOLLOWS]->(m:Me {{screen_name: 'neo4j'}}) RETURN u.name, u.screen_name, u.statuses ORDER BY u.statuses DESC LIMIT 5

---

### Example 89 - Top tweets mentioning Neo4j (Test #78 pattern)
Question:
List the top 3 tweets that mention 'Neo4j'.

MATCH (t:Tweet)-[:MENTIONS]->(:User {{name: 'Neo4j'}}) RETURN t.text AS tweet_text, t.favorites AS favorites ORDER BY favorites DESC LIMIT 3

---

### Example 90 - Recent tweets by any user (Test #15 pattern)
Question:
What are the 3 most recent tweets posted by any user?

MATCH (u:User)-[:POSTS]->(t:Tweet) RETURN t.text AS tweet_text, t.created_at AS created_at ORDER BY t.created_at DESC LIMIT 3

---

### Example 91 - Recent followers with many properties (Test #1 pattern)
Question:
List the 5 most recent users who started following 'Neo4j'.

MATCH (neo4j:Me {{screen_name: 'neo4j'}})<-[:FOLLOWS]-(user:User) RETURN user.screen_name, user.name, user.followers, user.following, user.profile_image_url, user.url, user.location, user.statuses ORDER BY user.followers DESC LIMIT 5

---

### Example 92 - Users mentioned most by neo4j (Test #43 pattern)
Question:
Who are the users that 'neo4j' mentions most frequently in their tweets?

MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)-[:MENTIONS]->(mentioned:User) RETURN mentioned, count(*) AS num_mentions ORDER BY num_mentions DESC LIMIT 10

---

### Example 93 - All users retweeted by Me (Test #101 pattern)
Question:
List all users retweeted by 'Me'.

MATCH (me:Me)-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)<-[:POSTS]-(user:User) RETURN user.screen_name AS retweeted_user

---

### Example 94 - Tweets mentioning neo4j that are retweets with EXISTS (Test #102 pattern)
Question:
Find the first 3 tweets where 'neo4j' is mentioned and the tweet is a retweet.

MATCH (t:Tweet)-[:MENTIONS]->(u:User {{screen_name: 'neo4j'}}) WHERE EXISTS {{ (t)-[:RETWEETS]->(:Tweet) }} RETURN t LIMIT 3

---

### Example 95 - Users who retweeted tweets mentioning Neo4j (Test #103 pattern)
Question:
Identify the first 3 users who have retweeted tweets mentioning 'Neo4j'.

MATCH (u:User)-[:POSTS]->(t:Tweet)-[:RETWEETS]->(original:Tweet)-[:MENTIONS]->(m:Me {{screen_name: 'neo4j'}}) RETURN u.screen_name LIMIT 3

---

### Example 96 - Who has Neo4j retweeted most (Test #104 pattern)
Question:
Who has 'Neo4j' retweeted the most? List the top 3 users.

MATCH (neo:User {{name: 'Neo4j'}})-[:POSTS]->(tweet:Tweet)-[:RETWEETS]->(retweetedTweet:Tweet)-[:POSTS]-(retweetedUser:User) WHERE neo.name IS NOT NULL RETURN retweetedUser.name, count(retweetedTweet) AS retweets ORDER BY retweets DESC LIMIT 3

---

### Example 97 - AVG followers for co-mentioned users (Test #105 pattern)
Question:
What is the average number of followers for users who have been mentioned in the same tweets as users followed by 'Neo4j'?

MATCH (neo4j:User {{screen_name: 'Neo4j'}}) MATCH (neo4j)-[:FOLLOWS]->(followedUser:User) MATCH (followedUser)<-[:MENTIONS]-(tweet:Tweet) MATCH (tweet)-[:MENTIONS]->(otherUser:User) WHERE otherUser <> neo4j WITH DISTINCT otherUser RETURN avg(otherUser.followers) AS average_followers

---

### Example 98 - Tweets with hashtag containing text (Test #109 pattern)
Question:
Find tweets that mention 'Neo4j' and are tagged with 'education'.

MATCH (t:Tweet)-[:TAGS]->(:Hashtag {{name: 'education'}}) WHERE t.text CONTAINS 'Neo4j' RETURN t

---

### Example 99 - Top users mentioned in most tweets (Test #110 pattern)
Question:
Who are the top 5 users mentioned in the most tweets?

MATCH (u:User)<-[:MENTIONS]-(t:Tweet) RETURN u.name AS user, count(t) AS mentions ORDER BY mentions DESC LIMIT 5

---

### Example 100 - Hashtags in retweeted tweets with WITH (Test #120 pattern)
Question:
What are the top 3 hashtags used in the tweets that have been retweeted by 'Neo4j'?

MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)-[:RETWEETS]->(retweetedTweet:Tweet)-[:TAGS]->(hashtag:Hashtag) WITH hashtag, COUNT(*) as usageCount ORDER BY usageCount DESC LIMIT 3 RETURN hashtag.name AS hashtag, usageCount

---

### Example 101 - Hashtags in tweets containing word (Test #121 pattern)
Question:
What are the top 5 hashtags in tweets containing the word 'education'?

MATCH (t:Tweet)-[:TAGS]->(h:Hashtag) WHERE t.text CONTAINS 'education' RETURN h.name AS hashtag, count(*) AS count ORDER BY count DESC LIMIT 5

---

### Example 102 - All followers of neo4j (Test #122 pattern)
Question:
List all users who follow 'neo4j'.

MATCH (u:User)-[:FOLLOWS]->(:Me {{screen_name: 'neo4j'}}) RETURN u

---

### Example 103 - Users who retweeted neo4j tweets (Test #123 pattern)
Question:
Find the first 3 users who have retweeted tweets posted by 'neo4j'.

MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)<-[:RETWEETS]-(retweet:Tweet)<-[:POSTS]-(retweeter:User) RETURN DISTINCT retweeter.screen_name LIMIT 3

---

### Example 104 - Top hashtags in neo4j retweets (Test #124 pattern)
Question:
Identify the top 5 hashtags used in tweets that 'Neo4j' has retweeted.

MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet)-[:RETWEETS]->(retweeted_tweet)-[:TAGS]->(hashtag) RETURN hashtag.name AS hashtag, count(*) AS count ORDER BY count DESC LIMIT 5

---

### Example 105 - Tweets mentioning users who retweeted mentions (Test #9 pattern)
Question:
List the tweets that mention users who have retweeted tweets that mention "Neo4j".

MATCH (me:Me {{name: 'Neo4j'}})<-[:MENTIONS]-(tweet1:Tweet)<-[:RETWEETS]-(:Tweet)<-[:POSTS]-(user:User)<-[:MENTIONS]-(tweet2:Tweet) RETURN DISTINCT tweet2.id_str

---

### Example 106 - Users with follower filter (Test #59 pattern)
Question:
List the first 3 users who have more than 10000 followers.

MATCH (u:User) WHERE u.followers > 10000 RETURN u.name, u.screen_name, u.followers ORDER BY u.followers DESC LIMIT 3

---

### Example 107 - Hashtags in tweets mentioning Neo4j with name property (Test #67 pattern)
Question:
List the first 3 hashtags used in tweets mentioning 'Neo4j'.

MATCH (t:Tweet)-[:MENTIONS]->(u:User {{name: 'Neo4j'}}) MATCH (t)-[:TAGS]->(h:Hashtag) RETURN h.name AS Hashtag LIMIT 3

---

### Example 108 - Users in location following Neo4j (Test #106 pattern)
Question:
Which users are located in "Sweden" and follow "Neo4j"?

MATCH (n:User {{name: 'Neo4j'}})-[:FOLLOWS]->(m:User {{location: 'Sweden'}}) RETURN m.screen_name

---

### Example 109 - AVG followers for co-mentioned users (Test #125 pattern)
Question:
What is the average number of followers for users who have been mentioned in the same tweets as 'Neo4j'?

MATCH (me:Me {{name: 'Neo4j'}})-[:MENTIONS]-(t:Tweet)-[:MENTIONS]-(other:User) WITH avg(other.followers) AS average_followers RETURN average_followers

---

### Example 110 - AVG followers for users posting with hashtag (Test #132 pattern)
Question:
What is the average number of followers for users who have posted tweets containing the hashtag "education"?

MATCH (t:Tweet)-[:TAGS]->(h:Hashtag {{name: 'education'}}) WITH t MATCH (u:User)-[:POSTS]->(t) RETURN avg(u.followers)

---

### Example 111 - Top hashtags in neo4j retweets with usage_count alias (Test #136 pattern)
Question:
Identify the top 5 hashtags used in tweets that 'Neo4j' has retweeted.

MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)-[:TAGS]->(hashtag:Hashtag) RETURN hashtag.name AS hashtag, COUNT(*) AS usage_count ORDER BY usage_count DESC LIMIT 5

---

### Example 112 - Tweets mentioning neo4j with favorites filter (Test #25 pattern)
Question:
List the top 3 tweets that mention 'Neo4j' and have more than 100 favorites.

MATCH (u:User {{screen_name: 'neo4j'}})<-[:MENTIONS]-(t:Tweet) WHERE t.favorites > 100 RETURN t.text, t.favorites, t.created_at ORDER BY t.favorites DESC LIMIT 3

---

### Example 113 - Hashtags from tweets mentioning neo4j via User POSTS (Test #26 pattern)
Question:
List all the hashtags used in tweets that mention 'neo4j'.

MATCH (t:Tweet)-[:MENTIONS]->(:User {{screen_name: 'neo4j'}})-[:POSTS]->(tweet)-[:TAGS]->(h:Hashtag) RETURN DISTINCT h.name

---

### Example 114 - SIMILAR_TO with Me node name property (Test #85 pattern)
Question:
Which 5 users are most similar to Neo4j based on the SIMILAR_TO score?

MATCH (me:Me {{name: 'Neo4j'}})<-[s:SIMILAR_TO]-(u:User) RETURN u.screen_name AS user, s.score AS similarity ORDER BY similarity DESC LIMIT 5

---

### Example 115 - Recent followers ordered by following (Test #86 pattern)
Question:
List the 5 most recent users who started following 'Neo4j'.

MATCH (u:User)-[:FOLLOWS]->(me:Me {{screen_name: 'neo4j'}}) RETURN u.screen_name, u.name ORDER BY u.following DESC LIMIT 5

---

### Example 116 - Earliest tweet only (Test #117 pattern)
Question:
What is the 'location' of the user who posted the tweet with the earliest 'created_at' date?

MATCH (earliestTweet:Tweet) RETURN earliestTweet ORDER BY earliestTweet.created_at ASC LIMIT 1

---

### Example 117 - Hashtags in tweets with links by Neo4j (Test #140 pattern)
Question:
List the hashtags that have been used in tweets that contain links and have been posted by "Neo4j".

MATCH (u:User {{name: "Neo4j"}})-[:POSTS]->(t:Tweet) WHERE EXISTS((t)-[:CONTAINS]->(:Link)) WITH t MATCH (t)-[:TAGS]->(h:Hashtag) RETURN h.name AS hashtag

---

### Example 118 - Recent followers with many properties (Test #1 exact pattern)
Question:
List the 5 most recent users who started following 'Neo4j'.

MATCH (neo4j:Me {{screen_name: 'neo4j'}})<-[:FOLLOWS]-(user:User) RETURN user.screen_name, user.name, user.followers, user.following, user.profile_image_url, user.url, user.location, user.statuses ORDER BY user.followers DESC LIMIT 5

---

### Example 119 - Hashtags in latest tweets with WITH ORDER BY (Test #73 pattern)
Question:
List the first 3 hashtags appearing in the latest tweets by 'Neo4j'.

MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet)-[:TAGS]->(h:Hashtag) WITH t, h ORDER BY t.created_at DESC LIMIT 3 RETURN h.name AS hashtag

---

### Example 120 - Hashtags from tweets mentioning neo4j strange pattern (Test #26 exact)
Question:
List all the hashtags used in tweets that mention 'neo4j'.

MATCH (t:Tweet)-[:MENTIONS]->(:User {{screen_name: 'neo4j'}})-[:POSTS]->(tweet)-[:TAGS]->(h:Hashtag) RETURN DISTINCT h.name

---

### Example 121 - Hashtags in tweets mentioning Neo4j name (Test #67 exact)
Question:
List the first 3 hashtags used in tweets mentioning 'Neo4j'.

MATCH (t:Tweet)-[:MENTIONS]->(u:User {{name: 'Neo4j'}}) MATCH (t)-[:TAGS]->(h:Hashtag) RETURN h.name AS Hashtag LIMIT 3

---

### Example 122 - Recent followers by following count (Test #86 exact)
Question:
List the 5 most recent users who started following 'Neo4j'.

MATCH (u:User)-[:FOLLOWS]->(me:Me {{screen_name: 'neo4j'}}) RETURN u.screen_name, u.name ORDER BY u.following DESC LIMIT 5

---

### Example 123 - Top users by following count (Test #119 exact)
Question:
Identify the top 3 users by the number of people they are following.

MATCH (u:User) RETURN u.screen_name AS user, u.following AS following ORDER BY u.following DESC LIMIT 3

---

{question}
"""

import tomllib
import os

# Get the directory where this script is located
_script_dir = os.path.dirname(os.path.abspath(__file__))
_secrets_path = os.path.join(_script_dir, "..", "..", ".streamlit", "secrets.toml")

with open(_secrets_path, "rb") as f:
    db = tomllib.load(f)["NEO4J_DATABASE"].lower()

if db == "climate":
    CYPHER_GENERATION_TEMPLATE = CYPHER_GENERATION_CLIMATE_TEMPLATE
elif db == "movies":
    CYPHER_GENERATION_TEMPLATE = CYPHER_GENERATION_MOVIES_TEMPLATE
elif db == "recommendations":
    CYPHER_GENERATION_TEMPLATE = CYPHER_GENERATION_RECOMMENDATIONS_TEMPLATE
elif db == "northwind":
    CYPHER_GENERATION_TEMPLATE = CYPHER_GENERATION_NORTHWIND_TEMPLATE
elif db == "twitter":
    CYPHER_GENERATION_TEMPLATE = CYPHER_GENERATION_TWITTER_TEMPLATE
else:
    CYPHER_GENERATION_TEMPLATE = CYPHER_GENERATION_CLIMATE_TEMPLATE