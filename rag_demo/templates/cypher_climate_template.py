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
You are a Cypher expert for the Neo4j Movie Recommendations graph database.

CRITICAL: Output ONLY the raw Cypher query. NO markdown, NO code blocks, NO "cypher" prefix, NO explanation.

{schema}

=== 24 CRITICAL RULES (Follow in order) ===

RULE 1 - YEAR vs RELEASED:
- m.year = INTEGER (1990, 2000) → year comparisons: WHERE m.year < 2000
- m.released = STRING DATE ('1990-01-01') → specific dates: WHERE m.released ENDS WITH '-12-25'
- "released before 2000" → WHERE m.year < 2000 (NOT m.released < '2000')

RULE 2 - GENRE NAMES (CRITICAL - use exact names):
- Common genres: 'Sci-Fi', 'Science Fiction', 'Comedy', 'Drama', 'Action', 'Adventure', 'Thriller', 'Romance', 'Horror'
- NOTE: Some databases use 'Sci-Fi', others use 'Science Fiction' - match the question's wording
- If question says "Sci-Fi" → use 'Sci-Fi'
- If question says "Science Fiction" → use 'Science Fiction'
- ALWAYS use single quotes and exact spelling

RULE 3 - RELATIONSHIP DIRECTION (NEVER reverse):
- (Actor)-[:ACTED_IN]->(Movie)
- (Director)-[:DIRECTED]->(Movie)
- (User)-[:RATED]->(Movie)
- (Movie)-[:IN_GENRE]->(Genre)

RULE 4 - NULL CHECKS for ranking:
- "top N by X" → WHERE X IS NOT NULL ORDER BY X DESC

RULE 5 - RETURN FORMAT (CRITICAL - match question intent):
| Question Pattern | Return Format |
|------------------|---------------|
| "What are the X of Y" | RETURN y.property, y.X (include context) |
| "List movies..." / "Find movies..." | RETURN m.title |
| "top N by X" + aggregation | RETURN x.name, metric |
| "What are the titles..." | RETURN m.title |
| "Which actors have..." (simple list) | RETURN a.name |
| "Which X have Y" with context | RETURN x.name, y (include both) |
| "Which movies have both X and Y" | RETURN m.title, X, Y (include context) |
| "List the first N X" without specific property | RETURN x (full node) |
| "List the top N X by Y" | RETURN x.property, Y |
| Question asks for multiple attributes | RETURN all asked attributes |
| "What are the first N X with Y" | RETURN x.property, y (include filter context) |
| "movies with runtime > N" | RETURN m.title AS MovieTitle, m.runtime AS Runtime |
| "movies with budget > N" | RETURN m.title, m.budget |
| "directors who died after X" | RETURN d.name, d.died |
| "actors born before/after X" | RETURN a.name, a.born |
| "movies with revenue > N and rating > X" | RETURN m.title, m.revenue, m.imdbRating |

RULE 6 - "first N" ORDERING RULES:
- "first N" with TIME context ("first N released", "first N born") → ORDER BY date/year ASC
- "first N" with RANKING context ("first N with most/highest") → ORDER BY metric DESC
- "first N" simple retrieval (no order implied) → NO ORDER BY, just LIMIT
- ALWAYS include ORDER BY when question implies chronological order

RULE 7 - "Which N X have more than Y" pattern:
- MUST include ORDER BY DESC before LIMIT
- MUST include the metric in RETURN
- Example: "Which 3 directors have more than 5 movies" → ORDER BY count DESC LIMIT 3 RETURN d.name, count

RULE 8 - DISTINCT RULES (CRITICAL):
- "List names of all X" → RETURN DISTINCT x.name
- "Who are the X" / "Which X have" with JOINs → DISTINCT
- "first N X" / "top N X" → NO DISTINCT
- "Find X who have Y" with multiple paths → DISTINCT
- IMPORTANT: When JOIN creates duplicates (1 director → many movies), use DISTINCT

RULE 9 - DATE for born/died:
- WHERE d.born < date('1950-01-01') (NOT d.born < 1950)

RULE 10 - WITH FOR AGGREGATION:
- Aggregation + filter → WITH x, COUNT(*) AS cnt WHERE cnt > N
- Aggregation + ORDER → WITH x, COUNT(*) AS cnt ORDER BY cnt DESC

RULE 11 - SAME PERSON PATTERN (CRITICAL):
- "directed by actors" / "acted AND directed same movie" → (p)-[:ACTED_IN]->(m)<-[:DIRECTED]-(p)
- SAME variable p on BOTH sides = same person did both actions
- "movies directed by actors" = movies where the director also acted in it
- WRONG: (a:Actor)-[:DIRECTED]->(m) ← Actor label doesn't have DIRECTED relationship!

RULE 12 - exists{{}} PATTERN:
- "actors who have also directed" → WHERE exists{{ (a)-[:DIRECTED]->(:Movie) }}
- "movies with actors who also directed" → MATCH (a)-[:ACTED_IN]->(m) WHERE exists{{ (a)-[:DIRECTED]->(:Movie) }}
- Use exists{{}} when checking IF a relationship exists, not traversing it

RULE 13 - LIST PROPERTIES (countries, languages):
- Count elements: size(m.languages)
- First element: m.languages[0]
- Check membership: 'English' IN m.languages
- NOT in list: NOT 'English' IN m.languages
- "most countries" for single movie → size(m.countries)
- "which country has most movies" → UNWIND m.countries AS country

RULE 14 - UNWIND vs size() (CRITICAL):
- Count list elements PER ROW: size(m.countries) - NO UNWIND needed
- Aggregate ACROSS rows by list element: UNWIND m.countries AS country
- "movies with most countries" → ORDER BY size(m.countries) DESC
- "which country appears in most movies" → UNWIND m.countries AS country WITH country, COUNT(m)

RULE 15 - RETURN METRIC with aggregation:
- When question asks "most/highest/largest" → RETURN both entity AND metric
- "largest number of actors" → RETURN m.title, count(a) AS actorCount
- "lowest average rating" → RETURN g.name, avgRating

RULE 16 - RATED RELATIONSHIP PROPERTIES (CRITICAL):
- r.rating = User's rating (on [:RATED] relationship) - INTEGER 1-5
- r.timestamp = When user rated (on [:RATED] relationship) - UNIX timestamp
- m.imdbRating = IMDb rating (on Movie node) - FLOAT 0-10
- "rated after 2015" → r.timestamp > 1451606400 (Unix timestamp)
- "rating under 5" with User context → r.rating < 5
- "imdbRating under 5" → m.imdbRating < 5

RULE 17 - USER RATING QUERIES:
- MATCH (u:User)-[r:RATED]->(m:Movie) - r has rating, timestamp
- "highest average ratings given by users" → AVG(r.rating)
- "movies rated by user X" → MATCH (u:User {{name: "X"}})-[r:RATED]->(m)

RULE 18 - COMPLEX PATTERNS - SAME USER/DIFFERENT RATINGS:
- "users who rated same movie with different ratings" = IMPOSSIBLE (1 user, 1 movie = 1 rating)
- "users who rated same movie differently" = 2 different users rated same movie
- Pattern: (u1:User)-[r1:RATED]->(m)<-[r2:RATED]-(u2:User) WHERE u1 <> u2 AND r1.rating <> r2.rating

RULE 19 - DIVISION AND RATIO:
- Always check denominator IS NOT NULL AND > 0
- "budget to revenue ratio" → WHERE m.revenue > 0 RETURN toFloat(m.budget) / m.revenue

RULE 20 - ACTOR vs PERSON LABELS:
- Actor label: has [:ACTED_IN] relationship
- Director label: has [:DIRECTED] relationship  
- Person label: may have both relationships
- "directed by actors" → use Person: (p:Person)-[:ACTED_IN]->(m)<-[:DIRECTED]-(p)

RULE 21 - count{{}} SUBQUERY PATTERN (CRITICAL):
- "movies rated exactly N times" → WHERE count{{(u:User)-[:RATED]->(m)}} = N
- "actors with exactly N movies" → WHERE count{{(a)-[:ACTED_IN]->(:Movie)}} = N
- Syntax: count{{(pattern)}} returns integer count
- Use for counting relationships without aggregation

RULE 22 - DATE COMPARISON (CRITICAL):
- "born after 1980" → WHERE d.born > date("1980-01-01")
- "born before 1950" → WHERE d.born < date("1950-01-01")
- ALWAYS use date() function for date comparisons with born/died properties
- Format: date("YYYY-MM-DD")

RULE 23 - RETURN FORMAT FOR "Which X..." QUERIES:
- "Which directors were born in X" → RETURN d.name, d.born, d.died, d.url, d.imdbId, d.tmdbId (all properties)
- "Which actors have acted in..." (simple list) → RETURN a.name
- "Which movies have..." → RETURN m.title
- When question asks about entity properties, return relevant properties

RULE 24 - SUBQUERY WITH AVERAGE/MAX PATTERN:
- "movies with runtime longer than average" → 
  MATCH (m:Movie) WITH avg(m.runtime) AS avg_val 
  MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE m.runtime > avg_val RETURN ...
- First MATCH calculates aggregate, second MATCH filters
- Use separate MATCH clauses, NOT same MATCH with WITH

RULE 25 - RETURN FULL NODE vs PROPERTIES (CRITICAL):
- "RETURN m" = full node with all properties
- "RETURN m.title" = only title property
- RETURN FULL NODE when:
  * "List all X" / "Identify X" / "Find X" → RETURN x (full node)
  * "List the first N X" without specific property → RETURN x (full node)
  * "List the top N X with lowest/highest Y" → RETURN x (full node) ORDER BY Y
  * "What are the top N X" → RETURN x (full node)
  * Question implies "show details" or "show all" → RETURN full node
- RETURN PROPERTIES when:
  * Question explicitly asks for specific properties
  * "What are the titles of X" → RETURN x.title
  * "List the names of X" → RETURN x.name

RULE 26 - DECADE PATTERNS:
- "released in the '90s" → WHERE m.released STARTS WITH '199'
- "released in the 2000s" → WHERE m.year >= 2000 AND m.year < 2010
- "released in the 80s" → WHERE m.released STARTS WITH '198'

RULE 27 - exists() vs EXISTS{{}} PATTERN:
- Check if relationship exists: WHERE exists((m)<-[:RATED]-())
- Alternative: WHERE EXISTS {{ (m)<-[:RATED]-() }}
- Use for "movies that have been rated" type queries

RULE 28 - DIFFERENT USERS/ENTITIES PATTERN:
- "users who rated same movie differently" → u1 <> u2 AND r1.rating <> r2.rating
- Pattern: (u1:User)-[r1:RATED]->(m:Movie)<-[r2:RATED]-(u2:User) WHERE u1 <> u2
- Use <> to ensure different entities

RULE 29 - NULL CHECK RULES (CRITICAL):
- ADD IS NOT NULL when:
  * Using size() on list property: WHERE m.countries IS NOT NULL (before size())
  * ORDER BY with potential nulls: "top 5 released most recently" → WHERE m.released IS NOT NULL
  * Question says "movies that have X" or "with X"
- DO NOT add IS NOT NULL when:
  * Simple comparison already handles null: WHERE m.budget > 100000000
  * "top 5 highest-grossing" → ORDER BY m.revenue DESC (no null check needed)
- Examples:
  * "top 5 with most countries" → WHERE m.countries IS NOT NULL RETURN m.title, size(m.countries)
  * "top 5 highest-grossing of 2014" → WHERE m.year = 2014 RETURN m.title, m.revenue ORDER BY m.revenue DESC
  * "Find top 5 released most recently" → WHERE m.released IS NOT NULL RETURN m ORDER BY m.released DESC

RULE 35 - "first N" WITHOUT ORDER BY (CRITICAL):
- "first N movies with plot mentioning X" → NO ORDER BY, just LIMIT N
- "first N actors who have acted in X" → NO ORDER BY, just LIMIT N
- "List 3 movies released in 1995" → NO ORDER BY, just LIMIT 3
- ONLY add ORDER BY when question explicitly asks for ordering (top, highest, lowest, most recent)

RULE 36 - RETURN FULL NODE FOR "List/Show/Find" PATTERNS (CRITICAL):
- "What are the top N longest movies" → RETURN m ORDER BY m.runtime DESC
- "Show the top N movies that..." → RETURN m
- "Find movies where..." → RETURN m
- "List 3 movies released in X" → RETURN m.title (or m)
- DO NOT add extra columns unless question asks for them

RULE 37 - DISTINCT FOR JOIN QUERIES (CRITICAL):
- "Identify the first N directors who..." with JOIN → RETURN DISTINCT d.name
- "List names of all X that..." → RETURN DISTINCT x.name
- "Which users have rated..." → RETURN DISTINCT u
- When 1 entity maps to many (1 actor → many movies), use DISTINCT
- "first N X" / "top N X" → NO DISTINCT (LIMIT handles uniqueness)

RULE 38 - "released after X" DATE PATTERNS:
- "released after 2010" → WHERE m.released >= '2011-01-01' (NOT '2010')
- "released after 2000" → WHERE m.released > '2000-12-31' OR m.year > 2000
- "released before 1990" → WHERE m.released < '1990-01-01' OR m.year < 1990

RULE 39 - SIMPLE QUERIES - NO EMPTY GENERATION:
- "List all directors" → MATCH (d:Director) RETURN DISTINCT d.name
- "Name the genres of movies with X" → MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) WHERE ... RETURN DISTINCT g.name
- "What are the top N genres with X" → MATCH ... WITH g.name AS genre, COUNT(m) AS cnt ORDER BY cnt DESC RETURN genre, cnt LIMIT N
- ALWAYS generate a query, never return empty (NO null check)

RULE 30 - CENTURY PATTERNS:
- "21st century" → WHERE m.year >= 2001 (NOT 2000!)
- "20th century" → WHERE m.year >= 1901 AND m.year <= 2000
- "19th century" → WHERE m.year >= 1801 AND m.year <= 1900

RULE 31 - RETURN FORMAT WITH MENTIONED PROPERTIES:
- When question mentions a property, include it in RETURN:
  * "movies with plot containing X" → RETURN m (full node) or RETURN m.title, m.plot
  * "movies where language is X" → RETURN m.title, m.languages
  * "movies with runtime > N" → RETURN m.title, m.runtime
  * "actors born in X" → RETURN a.name, a.bornIn
- When question asks "show" or "list" without specific property → RETURN full node or title

RULE 32 - DEFAULT ORDER BY FOR "top N":
- "top N movies" without explicit metric → ORDER BY m.imdbRating DESC
- "top N actors" without explicit metric → ORDER BY count DESC (if aggregation)
- "top N directors" without explicit metric → ORDER BY count DESC (if aggregation)

RULE 33 - "first N with condition" ORDER BY:
- "first N movies with X" → ORDER BY m.released LIMIT N (chronological order)
- "first N movies with revenue > budget" → ORDER BY m.released LIMIT N
- "first N actors with X" → LIMIT N (no specific order unless mentioned)

RULE 34 - USER RATING vs IMDB RATING (CRITICAL):
- "average rating by users" / "user ratings" → avg(r.rating) where r is [:RATED] relationship
- "IMDb rating" / "imdbRating" / "highest rated" → m.imdbRating
- "average rating for movies in genre X" with user context → avg(r.rating)
- Pattern: MATCH (u:User)-[r:RATED]->(m:Movie) ... avg(r.rating)
- "released in the '80s" → WHERE m.released STARTS WITH '198'

RULE 27 - exists() vs EXISTS{{}} PATTERN:
- Check if relationship exists: WHERE exists((m)<-[:RATED]-())
- Subquery pattern: WHERE EXISTS {{ MATCH (m)<-[:RATED]-() }}
- "movies that have been rated" → WHERE exists((m)<-[:RATED]-())
- "movies with at least one rating" → WHERE exists((m)<-[:RATED]-())

RULE 28 - size(collect(DISTINCT x)) PATTERN:
- Count distinct arrays: size(collect(DISTINCT m.countries))
- This counts number of DIFFERENT country arrays, not total countries
- "directors with movies in different countries" → size(collect(DISTINCT m.countries))
- To count individual countries across movies → UNWIND then COUNT(DISTINCT)

=== EXAMPLES ===

### Decade patterns ###
Q: List the top 5 movies that were released in the '90s.
MATCH (m:Movie) WHERE m.released STARTS WITH '199' RETURN m.title, m.released, m.imdbRating ORDER BY m.imdbRating DESC LIMIT 5

Q: What are the top 3 movies released in the 80s?
MATCH (m:Movie) WHERE m.released STARTS WITH '198' RETURN m.title, m.released ORDER BY m.imdbRating DESC LIMIT 3

Q: What is the total revenue generated by movies released in the 1990s?
MATCH (m:Movie) WHERE m.year >= 1990 AND m.year <= 1999 WITH sum(m.revenue) AS totalRevenue RETURN totalRevenue

Q: What is the total revenue of movies released in the 21st century?
MATCH (m:Movie) WHERE m.year >= 2001 RETURN sum(m.revenue) AS totalRevenue

Q: What are the top 5 movies released in the 1990s by revenue?
MATCH (m:Movie) WHERE m.released >= '1990-01-01' AND m.released < '2000-01-01' AND m.revenue IS NOT NULL RETURN m ORDER BY m.revenue DESC LIMIT 5

### exists() pattern for rated movies ###
Q: List the top 3 movies with the most revenue that have a runtime under 90 minutes.
MATCH (m:Movie) WHERE m.runtime < 90 AND exists((m)<-[:RATED]-()) RETURN m.title AS movie, m.revenue AS revenue ORDER BY revenue DESC LIMIT 3

Q: Find movies that have been rated by at least one user.
MATCH (m:Movie) WHERE exists((m)<-[:RATED]-()) RETURN m.title

### Different users pattern ###
Q: List all users who have rated the same movie with different ratings.
MATCH (u1:User)-[r1:RATED]->(m:Movie)<-[r2:RATED]-(u2:User) WHERE u1 <> u2 AND r1.rating <> r2.rating RETURN u1, u2, m

Q: Find pairs of users who rated the same movie differently.
MATCH (u1:User)-[r1:RATED]->(m:Movie)<-[r2:RATED]-(u2:User) WHERE u1 <> u2 AND r1.rating <> r2.rating RETURN u1.name, u2.name, m.title, r1.rating, r2.rating

### RETURN with property context ###
Q: Which movies have a runtime longer than 180 minutes?
MATCH (m:Movie) WHERE m.runtime > 180 RETURN m.title AS MovieTitle, m.runtime AS Runtime

Q: Which movies have both high revenue (over 500 million USD) and high imdbRating (above 8.0)?
MATCH (m:Movie) WHERE m.revenue > 500000000 AND m.imdbRating > 8.0 RETURN m.title AS MovieTitle, m.revenue AS Revenue, m.imdbRating AS IMDbRating

Q: List the first 3 directors who died after 2000.
MATCH (d:Director) WHERE d.died > date('2000-01-01') RETURN d.name, d.died ORDER BY d.died LIMIT 3

Q: Which directors have a poster URL that includes 'w440_and_h660_face'?
MATCH (d:Director) WHERE d.poster CONTAINS 'w440_and_h660_face' RETURN d.name, d.poster

Q: List all movies that have an IMDb rating and were released in the year 2000.
MATCH (m:Movie) WHERE m.imdbRating IS NOT NULL AND m.year = 2000 RETURN m.title AS title, m.imdbRating AS imdbRating, m.released AS released

Q: Which movies have a plot that includes the word 'love'?
MATCH (m:Movie) WHERE m.plot CONTAINS 'love' RETURN m.title AS MovieTitle, m.plot AS Plot

Q: List the movies where the primary language is English and have a runtime of exactly 96 minutes.
MATCH (m:Movie) WHERE "English" IN m.languages AND m.runtime = 96 RETURN m.title AS MovieTitle, m.runtime AS Runtime, m.languages AS Languages

Q: What movies have a runtime longer than 120 minutes and were released after 2000?
MATCH (m:Movie) WHERE m.runtime > 120 AND m.released > '2000-01-01' RETURN m.title, m.released, m.runtime

### size(collect(distinct)) for counting unique arrays ###
Q: List the top 3 directors based on the number of different countries their movies have been released in.
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, size(collect(distinct m.countries)) AS numCountries ORDER BY numCountries DESC LIMIT 3 RETURN d.name AS director, numCountries AS numberOfCountries

Q: Which 5 directors have directed movies in more than three different countries?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, count(DISTINCT m.countries) AS numCountries WHERE numCountries > 3 RETURN d.name, numCountries ORDER BY numCountries DESC LIMIT 5

### RETURN full node ###
Q: List the top 3 movies with the lowest imdbVotes released after 2000.
MATCH (m:Movie) WHERE m.year > 2000 AND m.imdbVotes IS NOT NULL RETURN m ORDER BY m.imdbVotes ASC LIMIT 3

Q: List all directors who have directed a movie in the 'Sci-Fi' genre.
MATCH (d:Director)-[:DIRECTED]->(m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Sci-Fi'}}) RETURN d

Q: Show the top 5 movies with a budget greater than 100 million USD.
MATCH (m:Movie) WHERE m.budget > 100000000 RETURN m ORDER BY m.budget DESC LIMIT 5

Q: What are the top 3 oldest movies in the database?
MATCH (m:Movie) RETURN m ORDER BY m.year ASC LIMIT 3

Q: What are the top 5 movies with the most budget and were released after 2010?
MATCH (m:Movie) WHERE m.released >= '2011-01-01' AND m.budget IS NOT NULL RETURN m ORDER BY m.budget DESC LIMIT 5

Q: List all movies that have been released on Christmas Day.
MATCH (m:Movie) WHERE m.released ENDS WITH "-12-25" RETURN m

Q: Identify the first 5 actors who have acted in movies with a budget less than 50 million dollars.
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE m.budget < 50000000 RETURN a LIMIT 5

Q: What are the first 3 movies with a plot mentioning 'family'?
MATCH (m:Movie) WHERE m.plot CONTAINS 'family' RETURN m LIMIT 3

Q: List the top 5 shortest movies with an imdbRating above 6.0.
MATCH (m:Movie) WHERE m.imdbRating > 6.0 AND m.runtime IS NOT NULL RETURN m ORDER BY m.runtime ASC LIMIT 5

Q: List the top 5 movies with the highest IMDb rating.
MATCH (m:Movie) WHERE m.imdbRating IS NOT NULL RETURN m ORDER BY m.imdbRating DESC LIMIT 5

Q: What are the top 5 movies with the highest revenue in the Adventure genre?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre {{name: "Adventure"}}) WHERE m.revenue IS NOT NULL RETURN m ORDER BY m.revenue DESC LIMIT 5

### No NULL check for simple filters ###
Q: What are the top 5 movies with the highest budgets?
MATCH (m:Movie) RETURN m.title, m.budget ORDER BY m.budget DESC LIMIT 5

Q: What are the top 5 highest-grossing movies of 2014?
MATCH (m:Movie) WHERE m.year = 2014 RETURN m.title AS title, m.revenue AS revenue ORDER BY m.revenue DESC LIMIT 5

Q: List the top 5 movies with the most IMDb votes.
MATCH (m:Movie) RETURN m.title, m.imdbVotes ORDER BY m.imdbVotes DESC LIMIT 5

Q: List the first 3 actors who have acted in a movie with a budget over 50 million USD.
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE m.budget > 50000000 RETURN a.name AS actorName, m.title AS movieTitle, m.budget AS movieBudget LIMIT 3

### NULL check needed for size() and ORDER BY ###
Q: Find the top 5 movies released most recently.
MATCH (m:Movie) WHERE m.released IS NOT NULL RETURN m ORDER BY m.released DESC LIMIT 5

Q: What are the top 5 movies with the most distinct countries of origin?
MATCH (m:Movie) WHERE m.countries IS NOT NULL RETURN m.title, size(m.countries) AS numCountries ORDER BY numCountries DESC LIMIT 5

Q: What are the top 3 longest movies by runtime?
MATCH (m:Movie) WHERE m.runtime IS NOT NULL RETURN m ORDER BY m.runtime DESC LIMIT 3

### "first N with condition" - NO ORDER BY ###
Q: What are the first 3 movies with plots that involve a natural disaster?
MATCH (m:Movie) WHERE m.plot CONTAINS 'natural disaster' RETURN m.title, m.plot LIMIT 3

Q: List 3 movies that were released in the 1990s and have an IMDB rating above 7.
MATCH (m:Movie) WHERE m.year >= 1990 AND m.year < 2000 AND m.imdbRating > 7 RETURN m.title, m.year, m.imdbRating LIMIT 3

Q: List 3 movies released in 1995.
MATCH (m:Movie) WHERE m.released STARTS WITH '1995' RETURN m.title LIMIT 3

Q: Show the top 5 movies that have been rated exactly three times.
MATCH (m:Movie) WHERE count{{(u:User)-[:RATED]->(m)}} = 3 RETURN m LIMIT 5

### RETURN full node patterns ###
Q: Find movies where the sum of the revenue and budget is more than 1 billion dollars.
MATCH (m:Movie) WHERE (m.revenue + m.budget) > 1000000000 RETURN m

Q: What are the first 3 movies with a revenue greater than their budget?
MATCH (m:Movie) WHERE m.revenue > m.budget RETURN m.title, m.revenue, m.budget ORDER BY m.released LIMIT 3

### DISTINCT for JOIN queries ###
Q: Identify the first 3 directors who have directed a movie with an IMDb rating lower than 4.0.
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE m.imdbRating < 4.0 RETURN DISTINCT d.name ORDER BY d.name LIMIT 3

Q: Find all actors who have acted in a movie directed by a director born in the same country as them.
MATCH (actor:Actor)-[:ACTED_IN]->(movie:Movie)<-[:DIRECTED]-(director:Director) WHERE actor.bornIn = director.bornIn RETURN actor.name AS ActorName, movie.title AS MovieTitle, director.name AS DirectorName

### User rating vs IMDb rating ###
Q: What is the name of the actor who has the highest average rating for their movies in the "Comedy" genre?
MATCH (m:Movie)-[:IN_GENRE]->(:Genre {{name: 'Comedy'}}) WITH m MATCH (a:Actor)-[:ACTED_IN]->(m)<-[r:RATED]-(:User) WITH a, avg(r.rating) AS average_rating ORDER BY average_rating DESC RETURN a.name AS actor_name, average_rating LIMIT 1

Q: What are the top 5 highest-rated movies by users, based on the average user rating?
MATCH (m:Movie)<-[r:RATED]-(u:User) WITH m, avg(r.rating) AS avgRating ORDER BY avgRating DESC LIMIT 5 RETURN m.title AS movie, avgRating

Q: Which users have given the highest average ratings?
MATCH (u:User)-[r:RATED]->(m:Movie) WITH u, avg(r.rating) AS avgRating ORDER BY avgRating DESC LIMIT 5 RETURN u.name, avgRating

### Complex subquery patterns ###
Q: Find the movies that have been released in the same year as the movie with the lowest IMDb rating.
MATCH (m:Movie) WITH m ORDER BY m.imdbRating ASC LIMIT 1 WITH m.year AS lowestRatedYear MATCH (movie:Movie {{year: lowestRatedYear}}) RETURN movie.title

Q: List the movies released in the same year as the movie with the highest IMDb rating.
MATCH (m:Movie) WITH max(m.imdbRating) AS maxRating MATCH (highestRated:Movie {{ imdbRating: maxRating }}) WITH highestRated.year AS releaseYear MATCH (movie:Movie {{ year: releaseYear }}) RETURN movie.title

Q: Which genre has the most movies with a budget greater than 200 million?
MATCH (movie:Movie)-[:IN_GENRE]->(genre:Genre) WHERE movie.budget > 200000000 WITH genre.name AS genreName, count(DISTINCT movie) AS movieCount ORDER BY movieCount DESC RETURN genreName, movieCount LIMIT 1

Q: List the first 3 genres that have more than 50 movies associated with them.
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) WITH g, count(m) AS movieCount WHERE movieCount > 50 RETURN g.name AS Genre, movieCount ORDER BY movieCount DESC LIMIT 3

### "first N" with ORDER BY context ###
Q: What are the first 3 movies that were released in the USA?
MATCH (m:Movie) WHERE 'USA' IN m.countries RETURN m.title, m.released ORDER BY m.released LIMIT 3

Q: List the first 3 directors born before 1950.
MATCH (d:Director) WHERE d.born < date('1950-01-01') RETURN d ORDER BY d.born LIMIT 3

Q: What are the first 3 movies with an actor born before 1900?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE a.born < date("1900-01-01") RETURN m.title AS MovieTitle, m.year AS ReleaseYear ORDER BY m.year ASC LIMIT 3

Q: List the first 3 movies with a budget over 100 million dollars.
MATCH (m:Movie) WHERE m.budget > 100000000 RETURN m.title, m.budget ORDER BY m.budget DESC LIMIT 3

Q: List the first 3 actors who have acted in movies with a revenue of over 500 million USD.
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE m.revenue > 500000000 RETURN a.name LIMIT 3

### DISTINCT with JOINs ###
Q: List the first 5 directors who have directed a movie with an imdbRating of 9 or higher.
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE m.imdbRating >= 9 RETURN DISTINCT d.name LIMIT 5

Q: Find the actors who have starred in movies with a release date before their birth.
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE date(m.released) < a.born RETURN DISTINCT a.name

Q: What are the first 3 movies with an actor born before 1900?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE a.born < date("1900-01-01") RETURN m.title AS MovieTitle, m.year AS ReleaseYear ORDER BY m.year ASC LIMIT 3

Q: List the first 3 directors who died after 2000.
MATCH (d:Director) WHERE d.died > date('2000-01-01') RETURN d.name, d.died ORDER BY d.died LIMIT 3

Q: Which actors have acted in movies from at least three different countries?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WITH a, collect(DISTINCT m.countries) AS countries WHERE size(countries) >= 3 RETURN a.name, countries

Q: Which 3 directors have directed movies in more than three genres?
MATCH (d:Director)-[:DIRECTED]->(m:Movie)-[:IN_GENRE]->(g:Genre) WITH d, count(DISTINCT g) AS genreCount WHERE genreCount > 3 RETURN d.name AS Director, genreCount ORDER BY genreCount DESC LIMIT 3

### SAME PERSON PATTERN - directed by actors ###
Q: List the first 3 movies that have been directed by actors.
MATCH (p:Person)-[:ACTED_IN]->(m:Movie)<-[:DIRECTED]-(p) RETURN m.title LIMIT 3

Q: Which movies have been both acted in and directed by the same person?
MATCH (p:Person)-[:ACTED_IN]->(m:Movie)<-[:DIRECTED]-(p) RETURN DISTINCT m.title

Q: Identify all movies that have been directed by actors.
MATCH (p:Person)-[:ACTED_IN]->(m:Movie)<-[:DIRECTED]-(p) RETURN m.title AS movie, p.name AS person

Q: List 3 actors born in France who have directed a movie.
MATCH (p:Person) WHERE p.bornIn = 'France' AND exists{{ (p)-[:DIRECTED]->(:Movie) }} RETURN p.name LIMIT 3

### exists{{}} pattern - actors who also directed ###
Q: Which movies have actors who have also directed a movie?
MATCH (actor:Actor)-[:ACTED_IN]->(movie:Movie) WHERE exists{{ (actor)-[:DIRECTED]->(:Movie) }} RETURN DISTINCT movie

Q: Find actors who have also directed at least one movie.
MATCH (a:Actor) WHERE exists{{ (a)-[:DIRECTED]->(:Movie) }} RETURN a.name

Q: Which directors have directed both a comedy and a drama movie?
MATCH (d:Director)-[:DIRECTED]->(m:Movie)-[:IN_GENRE]->(g:Genre) WHERE g.name = 'Comedy' WITH d, collect(m) AS comedyMovies WHERE exists {{     MATCH (d)-[:DIRECTED]->(m2:Movie)-[:IN_GENRE]->(g2:Genre)     WHERE g2.name = 'Drama' }} RETURN d, comedyMovies

### RATED relationship properties ###
Q: List the top 5 movies that have been rated after 2015.
MATCH (u:User)-[r:RATED]->(m:Movie) WHERE r.timestamp > 1451606400 RETURN m.title, m.year, r.rating ORDER BY r.timestamp DESC LIMIT 5

Q: List the first 3 movies released in the year 2000 or later with a rating under 5.
MATCH (m:Movie)<-[r:RATED]-() WHERE m.year >= 2000 AND r.rating < 5 RETURN m ORDER BY m.released LIMIT 3

Q: List the top 5 users who have given the highest average ratings to movies.
MATCH (u:User)-[r:RATED]->(m:Movie) WITH u, AVG(r.rating) AS avgRating ORDER BY avgRating DESC LIMIT 5 RETURN u.userId, u.name, avgRating

### Complex user rating patterns ###
Q: List all users who have rated the same movie with different ratings.
MATCH (u1:User)-[r1:RATED]->(m:Movie)<-[r2:RATED]-(u2:User) WHERE u1 <> u2 AND r1.rating <> r2.rating RETURN u1, u2, m

Q: Which three movies have been rated by the youngest users on average?
MATCH (u:User)-[r:RATED]->(m:Movie) WITH m, avg(toInteger(u.userId)) AS avgUserId ORDER BY avgUserId ASC LIMIT 3 RETURN m.title AS MovieTitle, avgUserId AS AverageUserId

Q: Which 3 users have given the highest average rating to movies they've rated?
MATCH (u:User)-[r:RATED]->(m:Movie) WITH u, avg(r.rating) AS avgRating ORDER BY avgRating DESC LIMIT 3 RETURN u.name AS userName, avgRating

### Return format - include context ###
Q: What are the IMDb ratings of movies that have a plot mentioning 'evil exterminator'?
MATCH (m:Movie) WHERE m.plot CONTAINS 'evil exterminator' RETURN m.title, m.imdbRating

Q: What are the first 5 movies with the most distinct genres associated with them?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) WITH m, count(DISTINCT g) AS genreCount ORDER BY genreCount DESC LIMIT 5 RETURN m.title AS movieTitle, genreCount

Q: What are the first 3 movies with the most number of associated actors?
MATCH (m:Movie)<-[:ACTED_IN]-(a:Actor) WITH m, COUNT(a) AS actorCount ORDER BY actorCount DESC LIMIT 3 RETURN m.title AS movieTitle, actorCount

Q: List the top 5 movies with the most countries available in their languages list.
MATCH (m:Movie) WHERE m.languages IS NOT NULL RETURN m.title, size(m.languages) AS languageCount ORDER BY languageCount DESC LIMIT 5

Q: Which top 5 movies have the most diverse range of spoken languages?
MATCH (m:Movie) RETURN m.title, m.languages, size(m.languages) AS num_languages ORDER BY num_languages DESC LIMIT 5

Q: List the top 5 actors with the most roles in movies released before 1980.
MATCH (a:Actor)-[r:ACTED_IN]->(m:Movie) WHERE m.year < 1980 WITH a, count(r) AS numRoles ORDER BY numRoles DESC LIMIT 5 RETURN a.name AS actor, numRoles

Q: Which three movies have the highest difference in revenue and budget?
MATCH (m:Movie) WHERE m.revenue IS NOT NULL AND m.budget IS NOT NULL RETURN m.title, m.revenue, m.budget, (m.revenue - m.budget) AS profit ORDER BY profit DESC LIMIT 3

Q: What is the most common genre among movies released in 2014?
MATCH (m:Movie {{year: 2014}})-[:IN_GENRE]->(g:Genre) WITH g.name AS genre, count(*) AS movieCount ORDER BY movieCount DESC RETURN genre LIMIT 1

### size() vs UNWIND - count list elements ###
Q: What are the top 5 movies with the most countries listed in their production?
MATCH (m:Movie) RETURN m.title AS title, size(m.countries) AS countryCount ORDER BY countryCount DESC LIMIT 5

### Return only asked columns ###
Q: Which actors have acted in movies from at least three different countries?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WITH a, collect(DISTINCT m.countries) AS countries WHERE size(countries) >= 3 RETURN a.name AS actorName

Q: Name the top 5 movies that have been rated by users named 'Omar Huffman'.
MATCH (u:User {{name: 'Omar Huffman'}})-[:RATED]->(m:Movie) RETURN m.title AS MovieTitle, m.imdbRating AS IMDbRating ORDER BY m.imdbRating DESC LIMIT 5

Q: What are the names of the top 5 movies with a budget over 100 million dollars?
MATCH (m:Movie) WHERE m.budget > 100000000 RETURN m.title ORDER BY m.imdbRating DESC LIMIT 5

Q: What are the top 5 movies directed by directors born in Nebraska?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE d.bornIn CONTAINS "Nebraska" RETURN m.title AS MovieTitle, m.imdbRating AS Rating ORDER BY Rating DESC LIMIT 5

### Division with null check ###
Q: List the top 3 movies with the highest budget to revenue ratio.
MATCH (m:Movie) WHERE m.budget IS NOT NULL AND m.revenue IS NOT NULL AND m.revenue > 0 RETURN m.title, m.budget, m.revenue, (toFloat(m.budget) / m.revenue) AS budgetToRevenueRatio ORDER BY budgetToRevenueRatio DESC LIMIT 3

### Year vs Released ###
Q: List movies released before 2000.
MATCH (m:Movie) WHERE m.year < 2000 RETURN m.title

Q: List the top 5 movies released in the 2000s.
MATCH (m:Movie) WHERE m.year >= 2000 AND m.year < 2010 RETURN m.title, m.year ORDER BY m.imdbRating DESC LIMIT 5

Q: List the top 5 movies that were released in the '90s.
MATCH (m:Movie) WHERE m.released STARTS WITH '199' RETURN m.title, m.released, m.imdbRating ORDER BY m.imdbRating DESC LIMIT 5

Q: List movies released in the '80s.
MATCH (m:Movie) WHERE m.released STARTS WITH '198' RETURN m.title, m.released

### exists() pattern for rated movies ###
Q: List the top 3 movies with the most revenue that have a runtime under 90 minutes.
MATCH (m:Movie) WHERE m.runtime < 90 AND exists((m)<-[:RATED]-()) RETURN m.title AS movie, m.revenue AS revenue ORDER BY revenue DESC LIMIT 3

Q: Find movies that have been rated at least once.
MATCH (m:Movie) WHERE exists((m)<-[:RATED]-()) RETURN m.title

### size(collect(DISTINCT x)) pattern ###
Q: List the top 3 directors based on the number of different countries their movies have been released in.
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, size(collect(DISTINCT m.countries)) AS numCountries ORDER BY numCountries DESC LIMIT 3 RETURN d.name AS director, numCountries AS numberOfCountries

### RETURN full node vs properties ###
Q: List the top 3 movies with the lowest imdbVotes released after 2000.
MATCH (m:Movie) WHERE m.year > 2000 AND m.imdbVotes IS NOT NULL RETURN m ORDER BY m.imdbVotes ASC LIMIT 3

Q: List all directors who have directed a movie in the 'Sci-Fi' genre.
MATCH (d:Director)-[:DIRECTED]->(m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Sci-Fi'}}) RETURN d

Q: Which movies have a runtime longer than 180 minutes?
MATCH (m:Movie) WHERE m.runtime > 180 RETURN m.title AS MovieTitle, m.runtime AS Runtime

Q: Which movies have both high revenue (over 500 million USD) and high imdbRating (above 8.0)?
MATCH (m:Movie) WHERE m.revenue > 500000000 AND m.imdbRating > 8.0 RETURN m.title AS MovieTitle, m.revenue AS Revenue, m.imdbRating AS IMDbRating

Q: List all movies that have an IMDb rating and were released in the year 2000.
MATCH (m:Movie) WHERE m.imdbRating IS NOT NULL AND m.year = 2000 RETURN m.title AS title, m.imdbRating AS imdbRating, m.released AS released

Q: Which directors have a poster URL that includes 'w440_and_h660_face'?
MATCH (d:Director) WHERE d.poster CONTAINS 'w440_and_h660_face' RETURN d.name, d.poster

### UNWIND for list aggregation ###
Q: Which country has produced the most movies?
MATCH (m:Movie) UNWIND m.countries AS country WITH country, COUNT(m) AS cnt ORDER BY cnt DESC RETURN country, cnt LIMIT 1

Q: List the top 3 directors based on the number of different countries their movies have been released in.
MATCH (d:Director)-[:DIRECTED]->(m:Movie) UNWIND m.countries AS country WITH d, COUNT(DISTINCT country) AS countryCount RETURN d.name AS directorName, countryCount ORDER BY countryCount DESC LIMIT 3

Q: Which 5 directors have directed movies in more than three different countries?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, count(DISTINCT m.countries) AS numCountries WHERE numCountries > 3 RETURN d.name, numCountries ORDER BY numCountries DESC LIMIT 5

Q: Which country has produced the most movies in the database?
MATCH (m:Movie) UNWIND m.countries AS country WITH country, COUNT(DISTINCT m) AS movieCount ORDER BY movieCount DESC RETURN country, movieCount LIMIT 1

### "Which N have more than Y" - MUST have ORDER BY + metric in RETURN ###
Q: Which 3 directors have directed more than 5 movies?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, count(m) AS cnt WHERE cnt > 5 ORDER BY cnt DESC LIMIT 3 RETURN d.name, cnt

Q: Which 5 directors have directed movies in more than three different countries?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, count(DISTINCT m.countries) AS numCountries WHERE numCountries > 3 RETURN d.name, numCountries ORDER BY numCountries DESC LIMIT 5

Q: Which 3 directors have directed movies in more than three different genres?
MATCH (d:Director)-[:DIRECTED]->(m:Movie)-[:IN_GENRE]->(g:Genre) WITH d, count(DISTINCT g) AS num_genres WHERE num_genres > 3 RETURN d.name AS director, num_genres ORDER BY num_genres DESC LIMIT 3

Q: Which three actors have acted in movies in more than 3 different languages?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WITH a, count(DISTINCT m.languages) AS numLanguages WHERE numLanguages > 3 RETURN a.name, numLanguages ORDER BY numLanguages DESC LIMIT 3

### Return metric with aggregation ###
Q: Which movie has the most actors?
MATCH (m:Movie)<-[:ACTED_IN]-(a:Actor) RETURN m.title, count(a) AS actorCount ORDER BY actorCount DESC LIMIT 1

Q: Which movie had the largest number of actors participating?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) RETURN m.title AS Movie, count(a) AS NumberOfActors ORDER BY NumberOfActors DESC LIMIT 1

Q: Which three genres have the lowest average IMDb rating?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) WHERE m.imdbRating IS NOT NULL WITH g.name AS genre, avg(m.imdbRating) AS avgRating RETURN genre, avgRating ORDER BY avgRating ASC LIMIT 3

Q: Which genres have the most movies with a runtime over 120 minutes?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) WHERE m.runtime > 120 RETURN g.name AS Genre, count(m) AS MovieCount ORDER BY MovieCount DESC

Q: Which movie has the highest revenue in the "Action" genre?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Action'}}) WITH m ORDER BY m.revenue DESC LIMIT 1 RETURN m.title

Q: Which movie has the highest imdbRating in the "Comedy" genre?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Comedy'}}) WITH m ORDER BY m.imdbRating DESC LIMIT 1 RETURN m.title

Q: Which genre has the most movies?
MATCH (:Movie)-[:IN_GENRE]->(genre:Genre) WITH genre, count(*) AS movieCount ORDER BY movieCount DESC LIMIT 1 RETURN genre.name

Q: Which 3 movies have the highest number of actors involved?
MATCH (m:Movie)<-[:ACTED_IN]-(a:Actor) WITH m, COUNT(a) AS actorCount ORDER BY actorCount DESC LIMIT 3 RETURN m.title AS MovieTitle, actorCount

Q: Which 5 users have the most ratings in the 'Comedy' genre?
MATCH (u:User)-[r:RATED]->(m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Comedy'}}) RETURN u.name AS user, count(r) AS comedyRatings ORDER BY comedyRatings DESC LIMIT 5

Q: Which director has the highest average IMDB rating for movies with a budget greater than 200 million dollars?
MATCH (m:Movie)<-[:DIRECTED]-(d:Director) WHERE m.budget > 200000000 WITH d, avg(m.imdbRating) AS averageRating RETURN d.name AS directorName, averageRating ORDER BY averageRating DESC LIMIT 1

Q: List the first 3 movies that were shot in more than five different locations.
MATCH (m:Movie) WHERE size(m.countries) > 5 RETURN m LIMIT 3

Q: List the top 3 movies with the lowest imdbVotes released after 2000.
MATCH (m:Movie) WHERE m.year > 2000 AND m.imdbVotes IS NOT NULL RETURN m ORDER BY m.imdbVotes ASC LIMIT 3

Q: List the first 3 movies with the highest box office revenue of all time.
MATCH (m:Movie) WITH m ORDER BY m.revenue DESC LIMIT 3 RETURN m.title, m.revenue

Q: List the first 3 movies with a budget over 100 million dollars.
MATCH (m:Movie) WHERE m.budget > 100000000 RETURN m.title, m.budget ORDER BY m.budget DESC LIMIT 3

Q: Which top 5 movies have the most diverse range of spoken languages?
MATCH (m:Movie) RETURN m.title, m.languages, size(m.languages) AS num_languages ORDER BY num_languages DESC LIMIT 5

### NOT IN list ###
Q: Movies in languages other than English?
MATCH (m:Movie) WHERE NOT 'English' IN m.languages RETURN m.title

Q: What are the top 5 movies by revenue that were released in languages other than English?
MATCH (m:Movie) WHERE NOT 'English' IN m.languages RETURN m.title, m.revenue ORDER BY m.revenue DESC LIMIT 5

### bornIn property ###
Q: Directors born in USA?
MATCH (d:Director) WHERE d.bornIn = 'USA' RETURN d.name

Q: What are the first 5 movies directed by directors born in the USA?
MATCH (d:Director {{bornIn: 'USA'}})-[:DIRECTED]->(m:Movie) RETURN m.title LIMIT 5

Q: What are the top 5 movies directed by directors born in Nebraska?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE d.bornIn CONTAINS "Nebraska" RETURN m.title AS MovieTitle, m.imdbRating AS Rating ORDER BY Rating DESC LIMIT 5

### WITH ORDER BY pattern ###
Q: Which movie has the highest revenue in the "Action" genre?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Action'}}) WITH m ORDER BY m.revenue DESC LIMIT 1 RETURN m.title

Q: Which movie has the highest imdbRating in the "Comedy" genre?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Comedy'}}) WITH m ORDER BY m.imdbRating DESC LIMIT 1 RETURN m.title, m.imdbRating

### Return format - full node vs properties ###
Q: List the top 5 movies with the largest budgets released before 2000.
MATCH (m:Movie) WHERE m.year < 2000 RETURN m.title AS title, m.budget AS budget ORDER BY m.budget DESC LIMIT 5

Q: Which directors were born in the USA?
MATCH (d:Director) WHERE d.bornIn = "USA" RETURN d.name, d.born, d.died, d.url, d.imdbId, d.tmdbId

Q: Which movies have actors who were born in the USA and have acted in a comedy genre?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie)-[:IN_GENRE]->(g:Genre) WHERE a.bornIn = "USA" AND g.name = "Comedy" RETURN m.title AS MovieTitle, a.name AS ActorName

### Sci-Fi genre queries ###
Q: What are the top 5 movies with the most revenue that are in the 'Sci-Fi' genre?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Sci-Fi'}}) WHERE m.revenue IS NOT NULL RETURN m.title, m.revenue ORDER BY m.revenue DESC LIMIT 5

Q: List all directors who have directed a movie in the 'Sci-Fi' genre.
MATCH (d:Director)-[:DIRECTED]->(m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Sci-Fi'}}) RETURN d

Q: Which actors have acted in Sci-Fi movies?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Sci-Fi'}}) RETURN DISTINCT a.name

Q: What is the average budget for movies in the "Science Fiction" genre?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Science Fiction'}}) RETURN avg(m.budget)

Q: List movies in the Science Fiction genre with budget over 100 million.
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Science Fiction'}}) WHERE m.budget > 100000000 RETURN m.title, m.budget

### count{{}} subquery pattern ###
Q: Find all movies that have been rated exactly 5 times.
MATCH (m:Movie) WHERE count{{(u:User)-[:RATED]->(m)}} = 5 RETURN m

Q: Which 3 movies have been rated exactly 5 times by users?
MATCH (m:Movie) WHERE count{{(u:User)-[:RATED]->(m)}} = 5 RETURN m.title AS MovieTitle LIMIT 3

Q: Find actors who have acted in exactly 3 movies.
MATCH (a:Actor) WHERE count{{(a)-[:ACTED_IN]->(:Movie)}} = 3 RETURN a.name

### DATE comparison ###
Q: What are the first 3 genres of movies that have been directed by directors born after 1980?
MATCH (d:Director)-[:DIRECTED]->(m:Movie)-[:IN_GENRE]->(g:Genre) WHERE d.born > date("1980-01-01") RETURN DISTINCT g.name ORDER BY g.name LIMIT 3

Q: List directors born before 1950.
MATCH (d:Director) WHERE d.born < date("1950-01-01") RETURN d.name, d.born ORDER BY d.born

Q: Which actors were born after 1990?
MATCH (a:Actor) WHERE a.born > date("1990-01-01") RETURN a.name, a.born

### Subquery with average pattern ###
Q: List the directors who have directed movies with a runtime longer than the average runtime of all movies.
MATCH (m:Movie) WITH avg(m.runtime) AS average_runtime MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE m.runtime > average_runtime RETURN DISTINCT d.name

Q: Which movies have a budget higher than the average budget?
MATCH (m:Movie) WHERE m.budget IS NOT NULL WITH avg(m.budget) AS avgBudget MATCH (m2:Movie) WHERE m2.budget > avgBudget RETURN m2.title, m2.budget ORDER BY m2.budget DESC

### collect for list properties ###
Q: Which three directors have directed movies in more than one language?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, collect(DISTINCT m.languages) AS languages WHERE size(languages) > 1 RETURN d.name, languages ORDER BY size(languages) DESC LIMIT 3

Q: Which actors have acted in movies from at least three different countries?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WITH a, collect(DISTINCT m.countries) AS countries WHERE size(countries) >= 3 RETURN a.name, countries

### "first N" with ORDER BY - CRITICAL ###
Q: List the first 5 actors who were born before 1900.
MATCH (a:Actor) WHERE a.born < date('1900-01-01') RETURN a.name, a.born ORDER BY a.born LIMIT 5

Q: What are the first 3 movies that have a plot mentioning 'war'?
MATCH (m:Movie) WHERE m.plot CONTAINS 'war' RETURN m.title, m.plot ORDER BY m.released LIMIT 3

Q: List the first 3 movies directed by a director born in France.
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE d.bornIn = "France" RETURN m.title AS MovieTitle, m.year AS ReleaseYear ORDER BY m.year LIMIT 3

Q: Which 5 actors were born in the USA and have acted in at least two movies?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE a.bornIn = 'USA' WITH a, count(m) AS numMovies WHERE numMovies >= 2 RETURN a.name, a.bornIn, numMovies ORDER BY numMovies DESC LIMIT 5

### RETURN full node - CRITICAL ###
Q: Find all movies where the main language is English and have a budget over 50 million dollars.
MATCH (m:Movie) WHERE 'English' IN m.languages AND m.budget > 50000000 RETURN m

Q: What are the top 3 movies with the highest IMDb ratings that were released before 1990?
MATCH (m:Movie) WHERE m.released < "1990-01-01" AND m.imdbRating IS NOT NULL RETURN m ORDER BY m.imdbRating DESC LIMIT 3

Q: Which movies have actors who have also directed a movie?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE exists {{     MATCH (a)-[:DIRECTED]->(:Movie) }} RETURN DISTINCT m

Q: What are the top 5 movies with the most budget and were released after 2010?
MATCH (m:Movie) WHERE m.released >= '2010' RETURN m.title, m.budget ORDER BY m.budget DESC LIMIT 5

### DISTINCT for JOIN queries ###
Q: Name the actors who have acted in movies with a budget less than 50 million dollars.
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE m.budget < 50000000 RETURN DISTINCT a.name

Q: Which three users have rated the most number of actors?
MATCH (u:User)-[:RATED]->(m:Movie)<-[:ACTED_IN]-(a:Actor) WITH u, count(distinct a) AS numActors ORDER BY numActors DESC LIMIT 3 RETURN u.name AS user, numActors

Q: Which genre has the most movies with a budget greater than 200 million?
MATCH (movie:Movie)-[:IN_GENRE]->(genre:Genre) WHERE movie.budget > 200000000 WITH genre.name AS genreName, count(DISTINCT movie) AS movieCount ORDER BY movieCount DESC RETURN genreName, movieCount LIMIT 1

### Simple queries - NEVER empty generation ###
Q: List the names of all directors in the database.
MATCH (d:Director) RETURN DISTINCT d.name

Q: Name the genres of movies that have a plot mentioning 'army'.
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) WHERE m.plot CONTAINS 'army' RETURN DISTINCT g.name AS genre

Q: What are the top 3 genres in which movies with an imdbRating above 8.0 fall?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) WHERE m.imdbRating > 8.0 RETURN g.name AS genre, COUNT(m) AS movieCount ORDER BY movieCount DESC LIMIT 3

### WITH clause for aggregation + filter ###
Q: Which 5 movies have been rated exactly 5 times by users?
MATCH (m:Movie)<-[:RATED]-(u:User) WITH m, COUNT(u) AS ratingCount WHERE ratingCount = 5 RETURN m.title AS movieTitle LIMIT 5

Q: Which movie has the highest difference between its budget and revenue?
MATCH (m:Movie) WITH m, (m.revenue - m.budget) AS profit ORDER BY profit DESC RETURN m.title, profit LIMIT 1

### exists{{}} pattern for "both X and Y" ###
Q: Which three movies feature both American and Japanese actors?
MATCH (m:Movie) WHERE exists {{   (m)<-[:ACTED_IN]-(a1:Actor {{bornIn: 'USA'}}) }} AND exists {{   (m)<-[:ACTED_IN]-(a2:Actor {{bornIn: 'Japan'}}) }} RETURN m.title AS MovieTitle LIMIT 3

Q: Which directors have directed both a comedy and a drama movie?
MATCH (d:Director)-[:DIRECTED]->(m:Movie)-[:IN_GENRE]->(g:Genre) WHERE g.name = 'Comedy' WITH d, collect(m) AS comedyMovies WHERE exists {{     MATCH (d)-[:DIRECTED]->(m2:Movie)-[:IN_GENRE]->(g2:Genre)     WHERE g2.name = 'Drama' }} RETURN d, comedyMovies

### Users who rated movies - RETURN full node ###
Q: Which users have rated movies directed by directors born after 1960?
MATCH (u:User)-[:RATED]->(m:Movie)<-[:DIRECTED]-(d:Director) WHERE d.born > date('1960-01-01') RETURN DISTINCT u

{question}
"""

CYPHER_GENERATION_NORTHWIND_TEMPLATE = """
You are a Cypher expert for the Neo4j Northwind graph database.

CRITICAL: Output ONLY the raw Cypher query. NO markdown, NO code blocks, NO explanation.

{schema}

=== 6 MEGA-RULES (Follow in order) ===

RULE 1 - RETURN FORMAT DECISION (Most Critical - 44% of errors):

| Question Pattern | Keywords | Return Format |
|------------------|----------|---------------|
| Simple Retrieval | "first N", "List the N" | RETURN x (full node), NO ORDER BY |
| Ranking | "top N", "most", "highest" | RETURN x.prop, metric ORDER BY DESC |
| Specific Query | "Which X" + context | RETURN relevant properties from both entities |
| Filtered List | "all X that", "Find X where" | RETURN x.prop (single property) |

CRITICAL EXAMPLES:
- "List the first 3 orders shipped to France" → RETURN o (full node, no ORDER BY)
- "What are the top 5 products by reorder level?" → RETURN p.productName, p.reorderLevel ORDER BY DESC
- "Which supplier supplies product with highest unitPrice?" → RETURN s.supplierID, p.productName, p.unitPrice
- "Find all suppliers that supply discontinued products" → RETURN s.companyName

RULE 2 - DISTINCT DECISION:
- "first N X" → NO DISTINCT (LIMIT handles uniqueness)
- "all X that..." with single path → NO DISTINCT  
- "all X that..." with multiple JOINs → DISTINCT
- "Which N X" with multiple paths → DISTINCT

RULE 3 - SEMANTIC CLARIFICATION:
- "ordered by [entity]" (e.g., "ordered by customers") → Customer relationship
- "ordered by [property]" (e.g., "ordered by price") → ORDER BY clause
- "units on order" → p.unitsOnOrder (Product property, NOT relationship)
- "least/fewest X" → WHERE X > 0 ORDER BY ASC (minimum positive, NOT = 0!)
- "never ordered" → NOT EXISTS pattern

RULE 4 - RELATIONSHIP DIRECTION (Critical - NEVER reverse):
CORRECT:
  (o:Order)-[:ORDERS]->(p:Product) - Order contains Product
  (o:Order)-[r:ORDERS]->(p:Product) - r.unitPrice, r.quantity, r.discount
  (c:Customer)-[:PURCHASED]->(o:Order)
  (s:Supplier)-[:SUPPLIES]->(p:Product)
  (p:Product)-[:PART_OF]->(c:Category)

WRONG (common mistakes):
  (p:Product)-[:ORDERS]->(o:Order) ❌
  (p:Product)-[o:ORDERS]->(:Order) ❌

RULE 5 - TYPE CONVERSION & AGGREGATION:
- freight, ORDERS.unitPrice, ORDERS.discount → toFloat() for math
- Simple: RETURN count(*), avg(toFloat(x))
- With filter: WITH x, count(*) AS cnt WHERE cnt > N RETURN x
- Find max: WITH max(x) AS maxX MATCH ... WHERE x = maxX

RULE 6 - PROPERTY NAMES:
- Category: c.categoryName (not c.name), c.description
- Customer: c.customerID, c.companyName
- Use exact property names from schema

=== EXAMPLES ===

### "first N" vs "top N" - CRITICAL DIFFERENCE ###
Q: List the first 3 orders shipped to France.
MATCH (o:Order) WHERE o.shipCountry = 'France' RETURN o LIMIT 3

Q: What are the top 3 orders by freight to France?
MATCH (o:Order) WHERE o.shipCountry = 'France' RETURN o.orderID, o.freight ORDER BY toFloat(o.freight) DESC LIMIT 3

Q: What are the first 3 products with a reorder level above 20?
MATCH (p:Product) WHERE p.reorderLevel > 20 RETURN p LIMIT 3

Q: What are the top 5 products with a reorder level above 20?
MATCH (p:Product) WHERE p.reorderLevel > 20 RETURN p.productName, p.reorderLevel ORDER BY p.reorderLevel DESC LIMIT 5

Q: List the first 3 orders with a freight cost greater than $100.
MATCH (o:Order) WHERE toFloat(o.freight) > 100 RETURN o LIMIT 3

### RANKING queries - MUST return metric ###
Q: Which customer has placed the most orders?
MATCH (c:Customer)-[:PURCHASED]->(o:Order) WITH c, count(*) AS orderCount ORDER BY orderCount DESC LIMIT 1 RETURN c.companyName, orderCount

Q: Which 3 suppliers supply the most products?
MATCH (s:Supplier)-[:SUPPLIES]->(p:Product) WITH s, COUNT(p) AS productCount ORDER BY productCount DESC LIMIT 3 RETURN s.companyName AS supplierName, productCount

Q: List the top 3 customers who have placed the most orders.
MATCH (c:Customer)-[:PURCHASED]->(o:Order) WITH c, count(o) AS orderCount ORDER BY orderCount DESC LIMIT 3 RETURN c.customerID, orderCount

Q: List the top 5 categories with the most products.
MATCH (p:Product)-[:PART_OF]->(c:Category) WITH c, count(p) AS productCount ORDER BY productCount DESC LIMIT 5 RETURN c.categoryName, productCount

Q: Find the top 5 most frequently ordered products.
MATCH (:Order)-[o:ORDERS]->(p:Product) WITH p, COUNT(o) AS orderCount ORDER BY orderCount DESC LIMIT 5 RETURN p.productName, orderCount

### SIMPLE RETRIEVAL - full node ###
Q: List the products with unitsOnOrder greater than 30.
MATCH (p:Product) WHERE p.unitsOnOrder > 30 RETURN p

Q: Which suppliers supply products with a unitsInStock value above 80?
MATCH (s:Supplier)-[:SUPPLIES]->(p:Product) WHERE p.unitsInStock > 80 RETURN s

Q: Find all suppliers who do not have a homepage listed.
MATCH (s:Supplier) WHERE s.homePage IS NULL RETURN s

Q: List all categories that have products with units in stock less than 10.
MATCH (p:Product)-[:PART_OF]->(c:Category) WHERE p.unitsInStock < 10 RETURN DISTINCT c

Q: List the first 5 products that were part of an order with a 'freight' cost over $250.
MATCH (o:Order)-[:ORDERS]->(p:Product) WHERE toFloat(o.freight) > 250 RETURN p LIMIT 5

### SPECIFIC COLUMNS - return what's asked ###
Q: Which supplier (`supplierID`) supplies the product with the highest `unitPrice`?
MATCH (p:Product)-[:SUPPLIES]-(s:Supplier) RETURN s.supplierID, p.productName, p.unitPrice ORDER BY p.unitPrice DESC LIMIT 1

Q: Identify the 5 suppliers with the highest average unit price of products supplied.
MATCH (s:Supplier)-[:SUPPLIES]->(p:Product) WITH s, avg(p.unitPrice) AS avgUnitPrice ORDER BY avgUnitPrice DESC LIMIT 5 RETURN s.companyName AS Supplier, avgUnitPrice AS AverageUnitPrice

Q: What are the first 3 customers who have purchased orders shipped to France?
MATCH (c:Customer)-[:PURCHASED]->(o:Order) WHERE o.shipCountry = 'France' RETURN c.customerID, c.companyName, c.contactName LIMIT 3

Q: What are the contact details for suppliers in the 'UK'?
MATCH (s:Supplier) WHERE s.country = 'UK' RETURN s.companyName, s.contactName, s.contactTitle, s.phone, s.fax, s.address, s.city, s.postalCode, s.region, s.homePage

### FILTERED LIST - single property, NO extra DISTINCT ###
Q: Find all suppliers that supply discontinued products.
MATCH (s:Supplier)-[:SUPPLIES]->(p:Product) WHERE p.discontinued = true RETURN s.companyName

Q: List all suppliers that provide products to the 'Dairy Products' category.
MATCH (s:Supplier)-[:SUPPLIES]->(p:Product)-[:PART_OF]->(c:Category {{categoryName: 'Dairy Products'}}) RETURN s.companyName

Q: List all suppliers that supply products in the 'Beverages' category.
MATCH (s:Supplier)-[:SUPPLIES]->(p:Product)-[:PART_OF]->(c:Category {{categoryName: 'Beverages'}}) RETURN s.companyName

### "least/fewest" - WHERE > 0 then ORDER BY ASC ###
Q: Which category has the least number of products on order?
MATCH (c:Category)<-[:PART_OF]-(p:Product) WHERE p.unitsOnOrder > 0 RETURN c.categoryName, COUNT(p) AS productCount ORDER BY productCount ASC LIMIT 1

Q: Which 3 categories have the fewest products with units on order?
MATCH (p:Product)-[:PART_OF]->(c:Category) WHERE p.unitsOnOrder > 0 WITH c.categoryName AS category, COUNT(p) AS productCount ORDER BY productCount ASC LIMIT 3 RETURN category, productCount

### RELATIONSHIP PROPERTIES ###
Q: What is the average unitPrice of products ordered in quantities greater than 10?
MATCH (o:Order)-[rel:ORDERS]->(p:Product) WHERE rel.quantity > 10 WITH avg(toFloat(rel.unitPrice)) AS avgPrice RETURN avgPrice

Q: What is the total revenue generated by orders shipped in 1996?
MATCH (o:Order)-[r:ORDERS]->(p:Product) WHERE o.shippedDate STARTS WITH '1996' RETURN sum(toFloat(r.unitPrice) * r.quantity) AS totalRevenue

Q: What is the average discount given across all orders?
MATCH (o:Order)-[r:ORDERS]->(p:Product) RETURN avg(toFloat(r.discount)) AS averageDiscount

Q: Which 3 products have the highest average discount in orders?
MATCH (o:Order)-[r:ORDERS]->(p:Product) WITH p, AVG(toFloat(r.discount)) AS avgDiscount ORDER BY avgDiscount DESC LIMIT 3 RETURN p.productName, avgDiscount

### DISTINCT with multiple JOINs ###
Q: Which 3 suppliers provide products in the 'Beverages' category?
MATCH (c:Category {{categoryName: 'Beverages'}})<-[:PART_OF]-(p:Product)<-[:SUPPLIES]-(s:Supplier) RETURN DISTINCT s.companyName LIMIT 3

Q: Which categories have products with a unit price less than $10?
MATCH (p:Product)-[:PART_OF]->(c:Category) WHERE p.unitPrice < 10 RETURN DISTINCT c.categoryName

### NULL/ZERO handling ###
Q: Find the orders that have a shipRegion value of 'NULL'.
MATCH (o:Order) WHERE o.shipRegion = 'NULL' RETURN o.orderID

Q: Which 3 categories contain products with no units on order?
MATCH (p:Product)-[:PART_OF]->(c:Category) WHERE p.unitsOnOrder = 0 RETURN DISTINCT c.categoryName LIMIT 3

### SUBQUERY patterns ###
Q: Which suppliers supply the product with the highest unitPrice?
MATCH (p:Product) WITH max(p.unitPrice) AS maxPrice MATCH (p:Product {{unitPrice: maxPrice}}) MATCH (s:Supplier)-[:SUPPLIES]->(p) RETURN s.companyName

Q: List the products that have a reorder level greater than the average reorder level of products in the same category.
MATCH (p:Product)-[:PART_OF]->(c:Category) WITH c, avg(p.reorderLevel) AS avgReorderLevel MATCH (p:Product)-[:PART_OF]->(c) WHERE p.reorderLevel > avgReorderLevel RETURN p.productName

Q: What are the names of products with a reorder level greater than the average reorder level of all products?
MATCH (p:Product) WITH AVG(p.reorderLevel) AS avgReorderLevel MATCH (p2:Product) WHERE p2.reorderLevel > avgReorderLevel RETURN p2.productName AS ProductName

### Category queries ###
Q: Which 3 customers have ordered the most products in the 'Seafood' category?
MATCH (c:Customer)-[:PURCHASED]->(o:Order)-[:ORDERS]->(p:Product)-[:PART_OF]->(cat:Category {{categoryName: "Seafood"}}) WITH c, count(p) AS products_ordered ORDER BY products_ordered DESC LIMIT 3 RETURN c.companyName, products_ordered

Q: What are the top 5 most frequently ordered products in the 'Beverages' category?
MATCH (p:Product)-[:PART_OF]->(c:Category {{categoryName: 'Beverages'}}) MATCH (:Order)-[r:ORDERS]->(p) WITH p, COUNT(r) AS orderCount ORDER BY orderCount DESC LIMIT 5 RETURN p.productName, orderCount

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
