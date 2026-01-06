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
- If a question requests "models", return only the model node(s); if it requests a property, return only that property.
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
Provide the cf standard name of variables produced climate models which are used in the experiment "historical".

MATCH (e:Experiment {{name: "historical"}})<-[:USED_IN_EXPERIMENT]-(s:Source)
MATCH (s)-[:PRODUCES_VARIABLE]->(v:Variable)
RETURN v.cf_standard_name
LIMIT 50;

---

{question}
"""

CYPHER_GENERATION_MOVIES_TEMPLATE = """
You are a Cypher expert for the Neo4j Movies graph database.

CRITICAL: Output ONLY the raw Cypher query. NO markdown, NO code blocks, NO explanation.

=== SCHEMA ===
Nodes:
- Person {{name: STRING, born: INTEGER}}
- Movie {{title: STRING, released: INTEGER, votes: INTEGER, tagline: STRING}}

Relationships:
- [:ACTED_IN] - Person acted in Movie
  Properties: {{roles: LIST<STRING>}}  ← CRITICAL: roles is on the RELATIONSHIP, not Person!
  
- [:DIRECTED] - Person directed Movie
- [:PRODUCED] - Person produced Movie  
- [:WROTE] - Person wrote Movie
- [:FOLLOWS] - Person follows Person
- [:REVIEWED] - Person reviewed Movie
  Properties: {{summary: STRING, rating: INTEGER}}  ← CRITICAL: rating/summary on RELATIONSHIP!

{schema}

=== 10 CRITICAL RULES (MUST FOLLOW EXACTLY) ===

RULE 1 - RELATIONSHIP PROPERTIES (Most common error - 30% of failures):
- rating and summary are on [:REVIEWED] relationship, NOT on Movie node
- roles is on [:ACTED_IN] relationship, NOT on Person node
- CORRECT: MATCH (p:Person)-[r:REVIEWED]->(m:Movie) WHERE r.rating > 90
- WRONG: MATCH (p:Person)-[:REVIEWED]->(m:Movie) WHERE m.rating > 90
- CORRECT: MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) RETURN r.roles
- WRONG: MATCH (p:Person)-[:ACTED_IN]->(m:Movie) RETURN p.roles

RULE 2 - SAME PERSON PATTERN (Critical for "same person did X and Y"):
- When finding person who did BOTH actions, use SAME variable
- CORRECT: (p)-[:DIRECTED]->(m)<-[:WROTE]-(p)  ← Same p on both sides
- WRONG: (p)-[:DIRECTED]->(m)<-[:WROTE]-(p2)  ← Different variables = different people!
- Pattern: "wrote AND directed same movie" → (p)-[:WROTE]->(m)<-[:DIRECTED]-(p)

RULE 3 - RETURN FORMAT (25% of errors):
- Return SPECIFIC PROPERTIES, not entire nodes
- CORRECT: RETURN m.title, p.name
- WRONG: RETURN m, p
- Exception: Only return full node if question explicitly asks for "all details"

RULE 4 - DISTINCT usage:
- Use DISTINCT when question implies unique results
- "Which people..." / "Who has..." → likely needs DISTINCT
- CORRECT: RETURN DISTINCT p.name
- Especially important with multiple MATCH patterns

RULE 5 - Counting roles vs actors:
- size(r.roles) = number of roles ONE actor plays in ONE movie
- COUNT(p) = number of different actors
- "movies with exactly 3 roles" → size(r.roles) = 3
- "movies with exactly 3 actors" → COUNT(p) = 3

RULE 6 - Property location:
- Person.name (not Movie.name)
- Movie.title (not Person.title)
- "Nancy Meyers" is Person.name, not Movie.title
- CORRECT: (p:Person {{name: 'Nancy Meyers'}})
- WRONG: (m:Movie {{title: 'Nancy Meyers'}})

RULE 7 - Aggregation with WITH:
- Use WITH for intermediate aggregations before filtering
- CORRECT: WITH p, COUNT(m) AS cnt WHERE cnt > 1 RETURN p.name
- Use ORDER BY after WITH or in final RETURN

RULE 8 - EXISTS pattern for "has relationship":
- "people who have produced AND directed" → use exists{{}}
- CORRECT: WHERE exists{{ (p)-[:PRODUCED]->(:Movie) }} AND exists{{ (p)-[:DIRECTED]->(:Movie) }}

RULE 9 - LIMIT:
- "top N" / "first N" → LIMIT N
- "most" without number → LIMIT 1
- No specification → LIMIT 50

RULE 10 - ORDER BY:
- "top N" / "most" / "highest" → ORDER BY ... DESC
- "lowest" / "least" → ORDER BY ... ASC
- Always include ORDER BY when question implies ranking

=== FEW-SHOT EXAMPLES ===

### RELATIONSHIP PROPERTIES - rating/summary on REVIEWED ###

Q: Find all movies with a rating above 90.
MATCH (p:Person)-[r:REVIEWED]->(m:Movie) WHERE r.rating > 90 RETURN m.title, r.rating

Q: What are the top 3 highest rated reviews and which movies they are associated with?
MATCH (p:Person)-[r:REVIEWED]->(m:Movie) RETURN m.title AS movie, r.rating AS rating, r.summary AS review ORDER BY r.rating DESC LIMIT 3

Q: List all movies with a 'Pretty funny at times' review summary.
MATCH (m:Movie)<-[r:REVIEWED]-(p:Person) WHERE r.summary = 'Pretty funny at times' RETURN m.title

Q: Who reviewed movies with a rating of 100?
MATCH (p:Person)-[r:REVIEWED]->(m:Movie) WHERE r.rating = 100 RETURN p.name

Q: Which person reviewed the movie with the lowest rating?
MATCH (p:Person)-[r:REVIEWED]->(m:Movie) RETURN p.name AS reviewer, m.title AS movie, r.rating AS rating ORDER BY r.rating ASC LIMIT 1

Q: Who are the top 3 reviewers by average rating given?
MATCH (p:Person)-[r:REVIEWED]->(m:Movie) WITH p, avg(r.rating) AS avg_rating ORDER BY avg_rating DESC LIMIT 3 RETURN p.name AS reviewer, avg_rating

Q: What is the highest rating given to any movie?
MATCH (:Person)-[r:REVIEWED]->(:Movie) RETURN max(r.rating) AS highest_rating

Q: List all movies that have a 'Fun, but a little far fetched' review summary.
MATCH (m:Movie)<-[r:REVIEWED]-(p:Person) WHERE r.summary = 'Fun, but a little far fetched' RETURN m.title

Q: Which movies have the word "coolest" in their review summary?
MATCH (m:Movie)<-[r:REVIEWED]-(p:Person) WHERE r.summary CONTAINS 'coolest' RETURN DISTINCT m.title

Q: Which people have reviewed a movie with the words "Robin Williams" in the summary?
MATCH (p:Person)-[r:REVIEWED]->(m:Movie) WHERE r.summary CONTAINS 'Robin Williams' RETURN DISTINCT p.name

---

### RELATIONSHIP PROPERTIES - roles on ACTED_IN ###

Q: What are the roles of Keanu Reeves in 'The Matrix'?
MATCH (p:Person {{name: 'Keanu Reeves'}})-[r:ACTED_IN]->(m:Movie {{title: 'The Matrix'}}) RETURN r.roles AS roles

Q: List the movies with exactly 3 roles in the ACTED_IN relationship.
MATCH (m:Movie)<-[r:ACTED_IN]-(p:Person) WHERE size(r.roles) = 3 RETURN m.title

Q: What are the roles of actors in the movie titled 'Speed Racer'?
MATCH (p:Person)-[r:ACTED_IN]->(m:Movie {{title: 'Speed Racer'}}) RETURN p.name, r.roles

Q: Who has the most roles in a single movie?
MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) RETURN p.name AS person, m.title AS movie, size(r.roles) AS num_roles ORDER BY num_roles DESC LIMIT 1

Q: List the roles of any person in movies with a title containing 'Matrix'.
MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) WHERE m.title CONTAINS 'Matrix' RETURN p.name AS person, m.title AS movie, r.roles AS roles

Q: What are the common roles for Keanu Reeves across all his movies?
MATCH (p:Person {{name: 'Keanu Reeves'}})-[r:ACTED_IN]->(m:Movie) WITH p, collect(r.roles) AS rolesList UNWIND rolesList AS roles UNWIND roles AS role RETURN p.name, role, count(*) AS times_played ORDER BY times_played DESC

Q: Show the first 3 movies with the most complex role lists in the 'ACTED_IN' relationship.
MATCH (m:Movie)<-[r:ACTED_IN]-(:Person) WITH m, size(r.roles) AS role_count ORDER BY role_count DESC LIMIT 3 RETURN m.title AS movie_title, role_count

---

### SAME PERSON PATTERN - wrote AND directed, etc. ###

Q: List all people who have written and directed the same movie.
MATCH (p:Person)-[:WROTE]->(m:Movie)<-[:DIRECTED]-(p) RETURN DISTINCT p.name

Q: Which movies have been both written and directed by the same person?
MATCH (p:Person)-[:DIRECTED]->(m:Movie)<-[:WROTE]-(p) RETURN m.title AS movie_title

Q: Which persons have acted in and directed the same movie?
MATCH (p:Person)-[:ACTED_IN]->(m:Movie)<-[:DIRECTED]-(p) RETURN p.name AS personName, m.title AS movieTitle

Q: Who has produced and written the same movie?
MATCH (p:Person)-[:PRODUCED]->(m:Movie)<-[:WROTE]-(p) RETURN p.name AS person, m.title AS movie

Q: List the first 3 movies that have been produced and directed by the same person.
MATCH (p:Person)-[:DIRECTED]->(m:Movie)<-[:PRODUCED]-(p) RETURN m.title AS MovieTitle LIMIT 3

Q: Show top 3 persons who directed, produced, and acted in the same movie.
MATCH (p:Person)-[:DIRECTED]->(m:Movie)<-[:PRODUCED]-(p)-[:ACTED_IN]->(m) RETURN p.name, collect(m.title) AS movies ORDER BY size(movies) DESC LIMIT 3

Q: What are the names of the first 3 people who have produced, directed, and written the same movie?
MATCH (p:Person)-[:PRODUCED]->(m:Movie) WHERE exists {{ (p)-[:DIRECTED]->(m) }} AND exists {{ (p)-[:WROTE]->(m) }} RETURN p.name LIMIT 3

---

### FILTERING BY PERSON NAME ###

Q: List the names of people who acted in movies directed by Nancy Meyers.
MATCH (d:Person {{name: 'Nancy Meyers'}})-[:DIRECTED]->(m:Movie)<-[:ACTED_IN]-(a:Person) RETURN DISTINCT a.name

Q: Which person produced movies directed by Lana Wachowski?
MATCH (d:Person {{name: 'Lana Wachowski'}})-[:DIRECTED]->(m:Movie)<-[:PRODUCED]-(producer:Person) RETURN DISTINCT producer.name

Q: Who are the first 3 actors in the movie titled 'Speed Racer'?
MATCH (p:Person)-[r:ACTED_IN]->(m:Movie {{title: 'Speed Racer'}}) RETURN p.name, r.roles LIMIT 3

---

### DISTINCT and AGGREGATION ###

Q: Who has produced movies but never acted in any?
MATCH (p:Person) WHERE exists{{ (p)-[:PRODUCED]->(:Movie) }} AND NOT exists{{ (p)-[:ACTED_IN]->(:Movie) }} RETURN p.name

Q: Find all people who have both produced and directed movies.
MATCH (p:Person)-[:DIRECTED]->(:Movie) WITH p MATCH (p)-[:PRODUCED]->(:Movie) RETURN DISTINCT p.name

Q: Which top 5 people have directed movies with more than 200 votes?
MATCH (p:Person)-[:DIRECTED]->(m:Movie) WHERE m.votes > 200 WITH p, count(m) AS num_movies ORDER BY num_movies DESC LIMIT 5 RETURN p.name AS director, num_movies

Q: Which year saw the release of the most movies?
MATCH (m:Movie) WITH m.released AS releaseYear, count(m) AS movieCount ORDER BY movieCount DESC RETURN releaseYear, movieCount LIMIT 1

---

### BASIC QUERIES ###

Q: Find the top 5 movies with the most votes.
MATCH (m:Movie) WHERE m.votes IS NOT NULL RETURN m.title, m.votes ORDER BY m.votes DESC LIMIT 5

Q: List the movies with more than 100 votes.
MATCH (m:Movie) WHERE m.votes > 100 RETURN m.title

Q: What are the first 3 movies with a released year of 2008?
MATCH (m:Movie) WHERE m.released = 2008 RETURN m.title, m.released ORDER BY m.title LIMIT 3

Q: What are the 3 most common taglines found in the movies?
MATCH (m:Movie) WHERE m.tagline IS NOT NULL RETURN m.tagline AS Tagline, count(m) AS Frequency ORDER BY Frequency DESC LIMIT 3

Q: What is the longest movie title in the database?
MATCH (m:Movie) RETURN m.title, size(m.title) AS length ORDER BY length DESC LIMIT 1

Q: Which movies have more than 3 actors listed?
MATCH (m:Movie)<-[:ACTED_IN]-(p:Person) WITH m, count(p) AS actorCount WHERE actorCount > 3 RETURN m.title AS MovieTitle, actorCount

Q: List the top 5 movies that have not been reviewed.
MATCH (m:Movie) WHERE NOT EXISTS {{ (m)<-[:REVIEWED]-() }} RETURN m.title LIMIT 5

---

### COMPLEX QUERIES ###

Q: Who are the top 3 producers by the number of movies with different taglines?
MATCH (p:Person)-[:PRODUCED]->(m:Movie) WHERE m.tagline IS NOT NULL WITH p, count(DISTINCT m.tagline) AS distinctTaglines ORDER BY distinctTaglines DESC LIMIT 3 RETURN p.name, distinctTaglines

Q: List the movies released between 1990 and 2000 with a rating higher than 80.
MATCH (m:Movie)<-[r:REVIEWED]-(p:Person) WHERE m.released >= 1990 AND m.released <= 2000 AND r.rating > 80 RETURN DISTINCT m.title

Q: What is the average number of words in the review summaries of movies with a rating above 95?
MATCH (:Person)-[r:REVIEWED]->(m:Movie) WHERE r.rating > 95 WITH size(split(r.summary, " ")) AS words RETURN avg(words) AS average_word_count

Q: Which person has the highest average rating for movies they wrote?
MATCH (p:Person)-[:WROTE]->(m:Movie)<-[r:REVIEWED]-() WITH p, avg(r.rating) AS average_rating RETURN p.name, average_rating ORDER BY average_rating DESC LIMIT 1

Q: What is the average number of votes for movies released in the same year "Speed Racer" was released?
MATCH (m:Movie {{title: 'Speed Racer'}}) WITH m.released AS releaseYear MATCH (m2:Movie) WHERE m2.released = releaseYear RETURN avg(m2.votes) AS averageVotes

Q: List the actors who have acted in movies directed by both Lilly Wachowski and Lana Wachowski.
MATCH (lilly:Person {{name: 'Lilly Wachowski'}})-[:DIRECTED]->(lillyMovies:Movie)<-[:ACTED_IN]-(actor:Person) MATCH (lana:Person {{name: 'Lana Wachowski'}})-[:DIRECTED]->(lanaMovies:Movie)<-[:ACTED_IN]-(actor) RETURN DISTINCT actor.name AS actorName

---

### ADVANCED QUERIES - SUBQUERIES AND COMPLEX PATTERNS ###

Q: Find all movies that have been produced by persons born after 1960 limited to top 5.
MATCH (p:Person)-[:PRODUCED]->(m:Movie) WHERE p.born > 1960 RETURN m.title ORDER BY m.released DESC LIMIT 5

Q: List the roles of actors in the 3 movies with the highest number of actors involved.
MATCH (m:Movie)<-[:ACTED_IN]-(p:Person) WITH m, count(p) AS actorCount ORDER BY actorCount DESC LIMIT 3 MATCH (m)<-[r:ACTED_IN]-(actor:Person) RETURN m.title AS movieTitle, actor.name AS actorName, r.roles AS roles

Q: Which movie has the most roles in the 'ACTED_IN' relationship and what are those roles?
MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) RETURN m.title AS Movie, r.roles AS Roles ORDER BY size(r.roles) DESC LIMIT 1

Q: Who reviewed the movie with the highest rating and what was the summary?
MATCH (m:Movie)<-[r:REVIEWED]-(p:Person) WITH m, r, p ORDER BY r.rating DESC LIMIT 1 RETURN p.name AS reviewer, r.summary AS review_summary, r.rating AS rating

Q: List the actors born after 1980 who have acted in more than one movie.
MATCH (p:Person)-[:ACTED_IN]->(m:Movie) WHERE p.born > 1980 WITH p, count(m) AS numMovies WHERE numMovies > 1 RETURN p.name AS actor, numMovies

Q: Show the top 5 people who have the most followers.
MATCH (p:Person)<-[:FOLLOWS]-(follower:Person) WITH p, COUNT(follower) AS followerCount ORDER BY followerCount DESC LIMIT 5 RETURN p.name AS personName, followerCount

Q: List the roles played by actors in the first 3 movies directed by Nancy Meyers.
MATCH (director:Person {{name: 'Nancy Meyers'}})-[:DIRECTED]->(movie:Movie) WITH movie ORDER BY movie.released LIMIT 3 MATCH (actor:Person)-[r:ACTED_IN]->(movie) RETURN movie.title AS MovieTitle, actor.name AS ActorName, r.roles AS Roles

Q: Find the movie with the highest number of votes that does not have the word "a" in its title.
MATCH (m:Movie) WHERE NOT m.title CONTAINS 'a' AND m.votes IS NOT NULL RETURN m.title, m.votes ORDER BY m.votes DESC LIMIT 1

Q: Which person has directed movies with a rating higher than 95?
MATCH (p:Person)-[:DIRECTED]->(m:Movie)<-[r:REVIEWED]-() WHERE r.rating > 95 RETURN DISTINCT p.name

Q: Who are the top 3 actors by number of followers?
MATCH (actor:Person)-[:ACTED_IN]->(:Movie) OPTIONAL MATCH (follower:Person)-[:FOLLOWS]->(actor) WITH actor, COUNT(follower) AS followerCount RETURN actor.name AS actorName, followerCount ORDER BY followerCount DESC LIMIT 3

---

### GRAPH METADATA QUERIES ###

Q: What are the 3 newest relationships formed in the graph (any type)?
MATCH ()-[r]-() RETURN r, type(r) ORDER BY id(r) DESC LIMIT 3

Q: List all relationship types in the database.
CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType

---

### NEGATIVE FILTERS ###

Q: Find movies that have NOT been reviewed.
MATCH (m:Movie) WHERE NOT EXISTS {{ (m)<-[:REVIEWED]-() }} RETURN m.title

Q: List people who have never directed any movie.
MATCH (p:Person) WHERE NOT EXISTS {{ (p)-[:DIRECTED]->(:Movie) }} RETURN p.name LIMIT 10

Q: Find movies with no tagline.
MATCH (m:Movie) WHERE m.tagline IS NULL RETURN m.title

---

{question}
"""

CYPHER_GENERATION_RECOMMENDATIONS_TEMPLATE = """
You are a Cypher expert who translates natural language questions into Cypher queries for a Neo4j movie recommendation graph database.

Schema:
{schema}

Examples:
### Example 1
Natural Language Question:
Which users rated the movie "Inception" and what were their ratings?

MATCH (u:User)-[r:RATED]->(m:Movie {{title: "Inception"}})
RETURN u.name AS User, r.rating AS Rating
ORDER BY r.rating DESC
LIMIT 20;

---

{question}
"""

CYPHER_GENERATION_NORTHWIND_TEMPLATE = """
You are a Cypher expert who writes precise Cypher queries for the Neo4j Northwind graph.

Schema (auto-refreshed):
{schema}

Examples:
### Example 1
Question:
Which supplier supplies the most products?

MATCH (s:Supplier)-[:SUPPLIES]->(p:Product)
RETURN s.companyName AS Supplier, COUNT(p) AS NumberOfProductsSupplied
ORDER BY NumberOfProductsSupplied DESC
LIMIT 1;

---

{question}
"""


CYPHER_GENERATION_TWITTER_TEMPLATE = """
You are a Cypher expert for a Neo4j Twitter graph.

CRITICAL: Output ONLY the raw Cypher query. NO markdown, NO code blocks, NO "cypher" prefix, NO explanation.

SCHEMA:
Nodes: User, Me (neo4j account), Tweet, Hashtag, Link, Source
- User/Me: screen_name, name, followers, following, statuses, betweenness, location, profile_image_url, url
- Tweet: id_str, text, created_at, favorites

Relationships: FOLLOWS, POSTS, MENTIONS, RETWEETS, TAGS, CONTAINS, USING, AMPLIFIES, INTERACTS_WITH, REPLY_TO, SIMILAR_TO, RT_MENTIONS

{schema}

=== 10 CRITICAL RULES (MUST FOLLOW EXACTLY) ===

RULE 1 - Property matching:
- Use screen_name: 'neo4j' (lowercase) when question says "neo4j" or "'neo4j'"
- Use name: 'Neo4j' (capitalized) when question says "Neo4j" or "'Neo4j'" or "user named Neo4j"
- MATCH THE EXACT CASING from the question

RULE 2 - Node selection (:Me vs :User) - CRITICAL (25% of errors):
- :Me ONLY for these relationships: AMPLIFIES, INTERACTS_WITH, RT_MENTIONS, SIMILAR_TO
- :Me for FOLLOWS (both directions when neo4j is involved)
- :User for ALL OTHER CASES including:
  * POSTS (even with favorites filter)
  * TAGS
  * MENTIONS (when neo4j is target)
  * General queries about 'neo4j' user

RULE 3 - RETURN format - CRITICAL (20% of errors):
- "RETURN t" / "RETURN u" / "RETURN tweet" → Return FULL NODE, not properties
- Questions asking "list tweets" / "show tweets" / "find tweets" → RETURN t (full node)
- Questions asking "top N by X" with specific metric → RETURN t.text, t.favorites (properties)
- Questions asking "which users" → RETURN u.screen_name, u.name (properties)
- MATCH THE EXACT RETURN FORMAT from similar examples below

RULE 4 - ORDER BY:
- "top N" / "most" / "highest" → ORDER BY ... DESC
- "first N" / "most recent" → ORDER BY created_at DESC (most recent first)
- "earliest" / "oldest" → ORDER BY created_at ASC
- "most frequently" → ORDER BY count_alias DESC
- ALWAYS include ORDER BY when question implies ranking

RULE 5 - LIMIT:
- "top N" / "first N" → LIMIT N
- "most" without number → LIMIT 1
- No specification → LIMIT 50

RULE 6 - RETWEETS pattern - CRITICAL (6% of errors):
- CORRECT: (user)-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)
- WRONG: (user)-[:RETWEETS]->(tweet) ← This relationship does NOT exist!
- To find who posted original: ...-[:RETWEETS]->(orig)<-[:POSTS]-(author:User)

RULE 7 - Aggregation with WITH:
- "most frequently" / "most mentions" → WITH entity, COUNT(*) AS count ORDER BY count DESC

RULE 8 - count{{}} syntax:
- "number of people following" → count{{(u)-[:FOLLOWS]->(:User)}} AS followingCount

RULE 9 - Relationship direction - CRITICAL:
- (User)-[:FOLLOWS]->(Me) means User follows Me
- (Me)<-[:FOLLOWS]-(User) is SAME as above
- (Tweet)-[:MENTIONS]->(User) means Tweet mentions User
- (Tweet)-[:RETWEETS]->(Tweet) means first tweet is retweet of second

RULE 10 - Hashtag matching:
- Use {{name: 'education'}} NOT {{name: '#education'}}
- Hashtag names are stored WITHOUT the # symbol

=== EXAMPLES (MATCH EXACTLY) ===

### AMPLIFIES - always :Me ###
Q: Which users does 'neo4j' amplify the most and list the top 5?
MATCH (me:Me {{screen_name: 'neo4j'}})-[:AMPLIFIES]->(user:User) RETURN user.screen_name, COUNT(*) AS amplification_count ORDER BY amplification_count DESC LIMIT 5

Q: Who are the top 3 users amplified by 'Me'?
MATCH (me:Me)-[:AMPLIFIES]->(u:User) RETURN u.name, u.screen_name ORDER BY u.followers DESC LIMIT 3

Q: Which users are amplified by 'Me' according to the AMPLIFIES relationship?
MATCH (me:Me)-[:AMPLIFIES]->(user:User) RETURN user.screen_name AS AmplifiedUser

### POSTS - use :User (NOT :Me) ###
Q: What are the top 5 tweets by 'Neo4j' based on favorites count?
MATCH (u:User {{name: 'Neo4j'}})-[:POSTS]->(t:Tweet) RETURN t ORDER BY t.favorites DESC LIMIT 5

Q: List all tweets by 'neo4j' that have more than 200 favorites and show the first 5.
MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet) WHERE t.favorites > 200 RETURN t LIMIT 5

Q: Find the first 3 tweets by 'Neo4j' that were favorited more than 300 times.
MATCH (u:User {{name: 'Neo4j'}})-[:POSTS]->(t:Tweet) WHERE t.favorites > 300 RETURN t ORDER BY t.created_at ASC LIMIT 3

Q: Find the top 5 tweets by 'Neo4j' using a specific source.
MATCH (u:User {{name: 'Neo4j'}})-[:POSTS]->(t:Tweet)-[:USING]->(s:Source) RETURN t ORDER BY t.favorites DESC LIMIT 5

### POSTS with TAGS - use :User ###
Q: Find all tweets posted by 'Neo4j' containing a hashtag.
MATCH (u:User {{name: 'Neo4j'}})-[:POSTS]->(t:Tweet)-[:TAGS]->(h:Hashtag) RETURN t, h

Q: Which tweets by 'neo4j' contain the hashtag 'education'?
MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet)-[:TAGS]->(h:Hashtag {{name: 'education'}}) RETURN t

Q: Display the first 3 tweets from 'neo4j' that contain a hashtag.
MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet)-[:TAGS]->(h:Hashtag) RETURN t ORDER BY t.created_at ASC LIMIT 3

### POSTS with CONTAINS - use :Me ###
Q: List the top 5 tweets that contain links and are posted by 'Neo4j'.
MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)-[:CONTAINS]->(link:Link) RETURN tweet.text, tweet.favorites ORDER BY tweet.favorites DESC LIMIT 5

Q: Find the tweets posted by "Neo4j" that contain links.
MATCH (u:User {{name: "Neo4j"}})-[:POSTS]->(t:Tweet)-[:CONTAINS]->(l:Link) RETURN t

### FOLLOWS - use :Me ###
Q: Who are the top 5 users that a specific user named 'Neo4j' follows?
MATCH (me:Me {{name: 'Neo4j'}})-[:FOLLOWS]->(user:User) RETURN user.name, user.screen_name, user.followers, user.following ORDER BY user.followers DESC LIMIT 5

Q: List the 5 most recent users who started following 'Neo4j'.
MATCH (neo4j:Me {{screen_name: 'neo4j'}})<-[:FOLLOWS]-(user:User) RETURN user.screen_name, user.name, user.followers, user.following, user.profile_image_url, user.url, user.location, user.statuses ORDER BY user.followers DESC LIMIT 5

Q: Which users are followed by 'neo4j' and have more than 10000 followers?
MATCH (me:Me {{screen_name: 'neo4j'}})-[:FOLLOWS]->(user:User) WHERE user.followers > 10000 RETURN user.screen_name, user.name, user.followers

### MENTIONS - neo4j as target, use :User ###
Q: Show the tweets where 'neo4j' is mentioned and the tweet has a favorite count over 100.
MATCH (t:Tweet)-[:MENTIONS]->(u:User {{screen_name: 'neo4j'}}) WHERE t.favorites > 100 RETURN t.text AS tweet_text, t.favorites AS favorite_count, t.created_at AS created_at

Q: List the top 3 tweets that mention 'Neo4j' and have more than 100 favorites.
MATCH (u:User {{screen_name: 'neo4j'}})<-[:MENTIONS]-(t:Tweet) WHERE t.favorites > 100 RETURN t.text, t.favorites, t.created_at ORDER BY t.favorites DESC LIMIT 3

Q: List the top 5 tweets that mention the user with the screen name 'neo4j'.
MATCH (t:Tweet)-[:MENTIONS]->(u:User {{screen_name: 'neo4j'}}) RETURN t ORDER BY t.favorites DESC LIMIT 5

### neo4j MENTIONS others - use :User with POSTS ###
Q: Who are the users that 'neo4j' mentions most frequently in their tweets?
MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet)-[:MENTIONS]->(mentioned:User) RETURN mentioned.screen_name, count(t) AS mentions_count ORDER BY mentions_count DESC

Q: Who are the top 3 users mentioned in the tweets that 'Neo4j' mentions?
MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)-[:MENTIONS]->(mentionedUser:User) WITH mentionedUser, COUNT(*) AS mentionCount ORDER BY mentionCount DESC LIMIT 3 RETURN mentionedUser.screen_name, mentionCount

### RETWEETS - MUST use POSTS->RETWEETS pattern ###
Q: Who are the top 5 users that 'neo4j' retweets the most?
MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)-[:RETWEETS]->(retweetedTweet:Tweet)<-[:POSTS]-(retweetedUser:User) RETURN retweetedUser.screen_name AS retweeted_user, count(*) AS retweet_count ORDER BY retweet_count DESC LIMIT 5

Q: Show the first 3 tweets that 'Me' has retweeted.
MATCH (me:Me)-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet) RETURN original ORDER BY original.created_at ASC LIMIT 3

Q: List the first 3 tweets that 'Neo4j' has retweeted.
MATCH (u:User {{name: 'Neo4j'}})-[:POSTS]->(t:Tweet)-[:RETWEETS]->(rt:Tweet) RETURN rt LIMIT 3

Q: List the first 3 tweets that 'Neo4j' retweets on '2021-03-16'.
MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet) WHERE date(retweet.created_at) = date('2021-03-16') RETURN original.text, original.created_at ORDER BY retweet.created_at ASC LIMIT 3

Q: Who has 'Neo4j' retweeted the most? List the top 3 users.
MATCH (neo:User {{name: 'Neo4j'}})-[:POSTS]->(tweet:Tweet)-[:RETWEETS]->(retweetedTweet:Tweet)-[:POSTS]->(retweetedUser:User) RETURN retweetedUser.screen_name AS user, count(*) AS retweet_count ORDER BY retweet_count DESC LIMIT 3

Q: Who are the users that have been retweeted by 'neo4j'?
MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)-[:RETWEETS]->(retweetedTweet:Tweet)<-[:POSTS]-(retweetedUser:User) RETURN DISTINCT retweetedUser.screen_name

Q: List all users retweeted by 'Me'.
MATCH (me:Me)-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)<-[:POSTS]-(user:User) RETURN user

### INTERACTS_WITH - always :Me ###
Q: Who does 'neo4j' interact with most frequently?
MATCH (me:Me {{screen_name: 'neo4j'}})-[:INTERACTS_WITH]->(user:User) RETURN user.screen_name, COUNT(*) AS interaction_count ORDER BY interaction_count DESC LIMIT 1

Q: Which users interact most frequently with 'neo4j' and list the top 5?
MATCH (me:Me {{screen_name: 'neo4j'}})-[:INTERACTS_WITH]->(user:User) RETURN user.screen_name, COUNT(*) AS interaction_count ORDER BY interaction_count DESC LIMIT 5

### RT_MENTIONS - always :Me ###
Q: Who are the top 3 users that 'Neo4j' retweets mentions from?
MATCH (me:Me {{screen_name: 'neo4j'}})-[:RT_MENTIONS]->(user:User) RETURN user.screen_name AS user, count(*) AS mentions ORDER BY mentions DESC LIMIT 3

### SIMILAR_TO - always :Me ###
Q: Which 5 users are most similar to Neo4j based on the SIMILAR_TO score?
MATCH (me:Me {{name: 'Neo4j'}})<-[s:SIMILAR_TO]-(u:User) RETURN u.screen_name AS user, s.score AS similarity ORDER BY similarity DESC LIMIT 5

### Counting relationships ###
Q: Identify the top 3 users by the number of people they are following.
MATCH (u:User) RETURN u.name, u.screen_name, count{{(u)-[:FOLLOWS]->(:User)}} AS followingCount ORDER BY followingCount DESC LIMIT 3

### General queries - return full node ###
Q: List the first 5 tweets with the highest number of favorites.
MATCH (t:Tweet) RETURN t.text, t.favorites ORDER BY t.favorites DESC LIMIT 5

Q: What are the top 5 most recent tweets based on the creation date?
MATCH (t:Tweet) RETURN t ORDER BY t.created_at DESC LIMIT 5

Q: Find the top 5 tweets with the highest number of favorites.
MATCH (t:Tweet) RETURN t ORDER BY t.favorites DESC LIMIT 5

Q: List the first 3 users who have more than 10000 followers.
MATCH (u:User) WHERE u.followers > 10000 RETURN u.name, u.screen_name, u.followers ORDER BY u.followers DESC LIMIT 3

Q: List all users who follow 'neo4j'.
MATCH (u:User)-[:FOLLOWS]->(:Me {{screen_name: 'neo4j'}}) RETURN u

Q: List the top 3 users with the lowest number of followers who follow 'Neo4j'.
MATCH (u:User)-[:FOLLOWS]->(m:Me {{screen_name: 'neo4j'}}) RETURN u ORDER BY u.followers ASC LIMIT 3

### Hashtag queries ###
Q: List the first 3 hashtags used in tweets mentioning 'Neo4j'.
MATCH (t:Tweet)-[:MENTIONS]->(u:User {{name: 'Neo4j'}}) MATCH (t)-[:TAGS]->(h:Hashtag) RETURN h.name AS hashtag LIMIT 3

Q: List the first 3 tweets containing a hashtag named 'education'.
MATCH (t:Tweet)-[:TAGS]->(h:Hashtag {{name: 'education'}}) RETURN t LIMIT 3

Q: What are the names of all hashtags used in tweets from 'Neo4j'?
MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet)-[:TAGS]->(h:Hashtag) RETURN h.name AS hashtag

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
