"""
Cypher generation templates for all supported databases.
Template selection is done via get_cypher_template(db_name).
"""
from __future__ import annotations

from config import get_settings

# =============================================================================
# CYPHER GENERATION TEMPLATES - OPTIMIZED FOR TEXT-TO-CYPHER EVALUATION
# =============================================================================
# Each template follows a consistent structure:
# 1. CRITICAL OUTPUT RULE (no markdown, no explanation)
# 2. SCHEMA section
# 3. NUMBERED RULES (max 15 rules, prioritized by error frequency)
# 4. FEW-SHOT EXAMPLES (grouped by pattern type)
# 5. {question} placeholder
# =============================================================================

CYPHER_GENERATION_CLIMATE_TEMPLATE = """
You are a Cypher expert for a Neo4j Climate Science graph database.

CRITICAL: Output ONLY the raw Cypher query. NO markdown, NO code blocks, NO explanation.

=== SCHEMA ===
{{schema}}

=== 12 CRITICAL RULES (Follow in order of priority) ===

RULE 1 - OUTPUT FORMAT (CRITICAL):
- Output ONLY valid Cypher query
- NO markdown code blocks (```)
- NO "cypher" prefix
- NO explanations before or after

RULE 2 - RETURN FORMAT (30% of errors):
| Question Pattern | Return Format |
|------------------|---------------|
| "Show models..." / "List models..." | RETURN s (full node) or s.name |
| "Which variables..." | RETURN v.name, v.cf_standard_name |
| "Show X and Y" | RETURN x, y (both entities) |
| "What is the X of Y" | RETURN specific property |

RULE 3 - NODE LABEL SELECTION:
- "regional climate models" / "RCMs" → (r:RCM)
- "global climate models" / "GCMs" → (s:Source)-[:IS_OF_TYPE]->(t:SourceType {{name: "AOGCM"}})
- "climate models" / "models" → (s:Source)
- "variables" → (v:Variable)
- "experiments" → (e:Experiment)

RULE 4 - RELATIONSHIP DIRECTION (NEVER reverse):
- (s:Source)-[:PRODUCES_VARIABLE]->(v:Variable)
- (s:Source)-[:USED_IN_EXPERIMENT]->(e:Experiment)
- (s:Source)-[:PRODUCED_BY_INSTITUTE]->(i:Institute)
- (s:Source)-[:HAS_SOURCE_COMPONENT]->(sc:SourceComponent)
- (s:Source)-[:APPLIES_TO_REALM]->(r:Realm)
- (r:RCM)-[:DRIVEN_BY_SOURCE]->(s:Source)
- (r:RCM)-[:COVERS_REGION]->(region)

RULE 5 - VARIABLE MAPPING:
- "temperature" → Variable {{name: "tas"}}
- "precipitation" / "rainfall" → Variable {{name: "pr"}}
- "pressure" → Variable {{name: "ps"}}
- Use exact name matching: {{name: "pr"}} NOT CONTAINS

RULE 6 - REGION HIERARCHY:
- Country: (c:Country {{name: "USA"}})
- State/Province: (cs:Country_Subdivision {{name: "Florida", code: "US.FL"}})
- Continent: (cont:Continent {{name: "North America"}})

RULE 7 - DISTINCT USAGE:
- "Which models..." with JOINs → RETURN DISTINCT
- "Show all X that..." → RETURN DISTINCT x.name
- "first N" / "top N" → NO DISTINCT (LIMIT handles uniqueness)

RULE 8 - LIMIT:
- Always include LIMIT 50 unless question specifies a number
- "top N" / "first N" → LIMIT N
- "all" / no limit mentioned → LIMIT 50

RULE 9 - CASE SENSITIVITY:
- Model names: exact match (e.g., "ACCESS-CM2")
- Institute names: use toLower() for safety
- Variable names: exact lowercase (e.g., "pr", "tas")

RULE 10 - OPTIONAL MATCH:
- Use ONLY when question explicitly mentions "if available" or "any"
- Default to MATCH for required relationships

RULE 11 - AGGREGATION:
- "how many" → COUNT(*)
- "which X has most Y" → WITH x, COUNT(y) AS cnt ORDER BY cnt DESC LIMIT 1

RULE 12 - SHARED COMPONENTS PATTERN:
- "models that share components" → 
  MATCH (s1:Source)-[:HAS_SOURCE_COMPONENT]->(sc)<-[:HAS_SOURCE_COMPONENT]-(s2:Source)
  WHERE s1 <> s2

=== FEW-SHOT EXAMPLES ===

### Basic Retrieval ###
Q: Show all climate models that include the variable 'pr'.
MATCH (s:Source)-[:PRODUCES_VARIABLE]->(v:Variable {{name: "pr"}})
RETURN s
LIMIT 50

Q: List all variables produced by the model ACCESS-CM2.
MATCH (s:Source {{name: "ACCESS-CM2"}})-[:PRODUCES_VARIABLE]->(v:Variable)
RETURN v.name, v.cf_standard_name
LIMIT 50

### Regional Models ###
Q: Show regional climate models that predict precipitation over Florida.
MATCH (r:RCM)-[:DRIVEN_BY_SOURCE]->(s:Source)
MATCH (s)-[:PRODUCES_VARIABLE]->(v:Variable {{name: "pr"}})
MATCH (r)-[:COVERS_REGION]->(c:Country_Subdivision {{name: "Florida"}})
RETURN r
LIMIT 50

Q: Which RCMs cover regions in the USA?
MATCH (r:RCM)-[:COVERS_REGION]->(cs:Country_Subdivision)-[:PART_OF]->(c:Country {{name: "USA"}})
RETURN DISTINCT r.name
LIMIT 50

### Experiments ###
Q: Which variables are associated with the experiment historical?
MATCH (e:Experiment {{name: "historical"}})<-[:USED_IN_EXPERIMENT]-(s:Source)
MATCH (s)-[:PRODUCES_VARIABLE]->(v:Variable)
RETURN DISTINCT v.name, v.cf_standard_name
LIMIT 50

Q: Show all models used in the experiment ssp585.
MATCH (s:Source)-[:USED_IN_EXPERIMENT]->(e:Experiment {{name: "ssp585"}})
RETURN s.name
LIMIT 50

### Shared Components ###
Q: Which component does ACCESS-CM2 share with ACCESS-ESM1-5?
MATCH (s1:Source {{name: "ACCESS-CM2"}})-[:HAS_SOURCE_COMPONENT]->(sc:SourceComponent)
MATCH (s2:Source {{name: "ACCESS-ESM1-5"}})-[:HAS_SOURCE_COMPONENT]->(sc)
WHERE s1 <> s2
RETURN sc
LIMIT 50

Q: Show all models produced by NASA-GISS and their shared components.
MATCH (i:Institute)<-[:PRODUCED_BY_INSTITUTE]-(s1:Source)
WHERE toLower(i.name) = "nasa-giss"
MATCH (s1)-[:HAS_SOURCE_COMPONENT]->(sc:SourceComponent)
OPTIONAL MATCH (sc)<-[:HAS_SOURCE_COMPONENT]-(s2:Source)
WHERE s1 <> s2
RETURN s1.name, sc.name, s2.name
LIMIT 50

### Model Types ###
Q: Which realms are targeted by AOGCM models?
MATCH (s:Source)-[:IS_OF_TYPE]->(type:SourceType {{name: "AOGCM"}})
MATCH (s)-[:APPLIES_TO_REALM]->(r:Realm)
RETURN DISTINCT r.name
LIMIT 50

Q: List all global climate models.
MATCH (s:Source)-[:IS_OF_TYPE]->(type:SourceType {{name: "AOGCM"}})
RETURN s.name
LIMIT 50

### Properties ###
Q: What is the cf_standard_name of variables produced by models in the historical experiment?
MATCH (e:Experiment {{name: "historical"}})<-[:USED_IN_EXPERIMENT]-(s:Source)
MATCH (s)-[:PRODUCES_VARIABLE]->(v:Variable)
RETURN DISTINCT v.cf_standard_name
LIMIT 50

Q: Show the frequency and resolution for model NorESM2-LM.
MATCH (s:Source {{name: "NorESM2-LM"}})
OPTIONAL MATCH (s)-[:HAS_FREQUENCY]->(f:Frequency)
OPTIONAL MATCH (s)-[:HAS_RESOLUTION]->(r:Resolution)
RETURN s.name, f.name AS frequency, r.name AS resolution
LIMIT 50

### Driving Models ###
Q: Which driving models are linked to RCMs that predict precipitation?
MATCH (r:RCM)-[:DRIVEN_BY_SOURCE]->(s:Source)
MATCH (s)-[:PRODUCES_VARIABLE]->(v:Variable {{name: "pr"}})
RETURN DISTINCT s.name AS driving_model
LIMIT 50

### Aggregation ###
Q: How many models are used in the historical experiment?
MATCH (s:Source)-[:USED_IN_EXPERIMENT]->(e:Experiment {{name: "historical"}})
RETURN COUNT(DISTINCT s) AS model_count

Q: Which institute has produced the most climate models?
MATCH (i:Institute)<-[:PRODUCED_BY_INSTITUTE]-(s:Source)
WITH i, COUNT(s) AS model_count
ORDER BY model_count DESC
LIMIT 1
RETURN i.name, model_count

{{question}}
"""

CYPHER_GENERATION_MOVIES_TEMPLATE = """
You are a Cypher expert for the Neo4j Movies graph database.

CRITICAL: Output ONLY the raw Cypher query. NO markdown, NO code blocks, NO explanation.

=== SCHEMA ===
Nodes (IMPORTANT - Movie does NOT have budget, revenue, or imdbRating):
- Person {{name: STRING, born: INTEGER}}
- Movie {{title: STRING, released: INTEGER, votes: INTEGER, tagline: STRING}}

Relationships:
- [:ACTED_IN] - Person acted in Movie
  Properties: {{roles: LIST<STRING>}}  ← CRITICAL: roles is on RELATIONSHIP!
- [:DIRECTED] - Person directed Movie
- [:PRODUCED] - Person produced Movie  
- [:WROTE] - Person wrote Movie
- [:FOLLOWS] - Person follows Person
- [:REVIEWED] - Person reviewed Movie
  Properties: {{summary: STRING, rating: INTEGER}}  ← CRITICAL: rating on RELATIONSHIP!

{{schema}}

=== 18 CRITICAL RULES (Follow in order of priority) ===

RULE 1 - RETURN FULL NODE vs PROPERTIES (CRITICAL - 40% of errors):
| Question Pattern | Return Format |
|------------------|---------------|
| "Find the top N movies..." | RETURN m (full node) |
| "Find all movies that..." | RETURN m (full node) |
| "Find all people born in X who..." | RETURN p (full node) |
| "List top N movies released before..." | RETURN m (full node) |
| "What are the top N X by Y" | RETURN x.prop, Y ORDER BY Y DESC |
| "List the first N actors in movie X" | RETURN p.name, r.roles |
| "List the top N youngest/oldest people..." | RETURN p.name, p.born |

RULE 2 - RELATIONSHIP PROPERTIES (35% of errors):
- rating, summary → on [:REVIEWED] relationship, NOT Movie node
- roles → on [:ACTED_IN] relationship, NOT Person node
- CORRECT: MATCH (p:Person)-[r:REVIEWED]->(m:Movie) WHERE r.rating > 90
- WRONG: WHERE m.rating > 90
- CORRECT: MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) RETURN r.roles
- WRONG: RETURN p.roles

RULE 3 - SAME PERSON PATTERN (20% of errors):
- "wrote AND directed same movie" → same variable on both sides
- CORRECT: (p)-[:WROTE]->(m)<-[:DIRECTED]-(p)
- WRONG: (p1)-[:WROTE]->(m)<-[:DIRECTED]-(p2)

RULE 4 - COUNTING ROLES - PER RELATIONSHIP vs AGGREGATED:
- "movies with most roles" (per single ACTED_IN) → size(r.roles) per relationship
- "movies with most total roles" (sum across all actors) → sum(size(r.roles))
- DEFAULT: Use per-relationship size(r.roles) unless "total" is specified
- CORRECT for "top 5 movies with most roles": RETURN m.title, size(r.roles) AS roleCount ORDER BY roleCount DESC

RULE 5 - OPTIONAL MATCH for counting with possible zeros:
- "top N actors by number of followers" → OPTIONAL MATCH for followers (might be 0)
- CORRECT: OPTIONAL MATCH (follower)-[:FOLLOWS]->(actor) WITH actor, COUNT(follower) AS cnt
- Use OPTIONAL MATCH when the relationship might not exist for all nodes

RULE 6 - DISTINCT USAGE:
- "Which people..." / "Who has..." with JOINs → DISTINCT
- "first N" / "top N" → NO DISTINCT
- Multiple MATCH patterns → likely needs DISTINCT

RULE 5 - COUNTING ROLES vs ACTORS:
- size(r.roles) = roles ONE actor plays in ONE movie
- COUNT(p) = number of different actors
- "movies with 3 roles" → size(r.roles) = 3
- "movies with 3 actors" → COUNT(p) = 3

RULE 7 - PROPERTY LOCATION:
- Person: name, born
- Movie: title, released, votes, tagline
- Movie does NOT have: budget, revenue, imdbRating
- "Nancy Meyers" → Person {{name: 'Nancy Meyers'}}

RULE 8 - AGGREGATION WITH WITH:
- WITH p, COUNT(m) AS cnt WHERE cnt > 1 RETURN p.name
- Use WITH for intermediate aggregations

RULE 9 - EXISTS PATTERN:
- "people who have produced AND directed" →
  WHERE exists{{(p)-[:PRODUCED]->(:Movie)}} AND exists{{(p)-[:DIRECTED]->(:Movie)}}

RULE 10 - LIMIT:
- "top N" / "first N" → LIMIT N
- "most" without number → LIMIT 1
- No specification → LIMIT 50

RULE 11 - ORDER BY:
- "top N" / "most" / "highest" → ORDER BY ... DESC
- "lowest" / "least" / "oldest" → ORDER BY ... ASC
- "youngest" → ORDER BY p.born DESC (higher birth year = younger)

RULE 12 - NULL HANDLING:
- "top N by votes" → WHERE m.votes IS NOT NULL ORDER BY m.votes DESC

RULE 13 - "first N" vs "top N":
- "first N" (simple retrieval) → NO ORDER BY, just LIMIT
- "first N" (chronological) → ORDER BY m.released ASC
- "top N" (ranking) → ORDER BY metric DESC

RULE 14 - NOT EXISTS:
- "movies NOT reviewed" → WHERE NOT EXISTS {{(m)<-[:REVIEWED]-()}}
- "people who never directed" → WHERE NOT EXISTS {{(p)-[:DIRECTED]->()}}

RULE 15 - YEAR FILTERING:
- "released in 2008" → WHERE m.released = 2008
- "released between 1990 and 2000" → WHERE m.released >= 1990 AND m.released <= 2000

RULE 16 - STRING MATCHING:
- Exact: WHERE r.summary = 'Pretty funny at times'
- Contains: WHERE r.summary CONTAINS 'coolest'

RULE 17 - RETURN RELATIONSHIP OBJECT:
- "newest relationships" → RETURN r, type(r) (include relationship object)
- "what type of relationship" → RETURN type(r)

RULE 18 - ALIAS MATCHING:
- Match the alias style from the question when possible
- "movie_title" in question → use AS movie_title
- Keep aliases consistent with question wording

=== FEW-SHOT EXAMPLES ===

### RETURN FULL NODE (CRITICAL) ###
Q: Find the top 5 movies with the most votes.
MATCH (m:Movie) WHERE m.votes IS NOT NULL RETURN m ORDER BY m.votes DESC LIMIT 5

Q: Find all people born in 1949 who have directed a movie.
MATCH (p:Person) WHERE p.born = 1949 AND exists{{(p)-[:DIRECTED]->(:Movie)}} RETURN p

Q: Find all movies that have been produced by persons born after 1960 limited to top 5.
MATCH (p:Person)-[:PRODUCED]->(m:Movie) WHERE p.born > 1960 RETURN m LIMIT 5

Q: List top 3 movies released before 1980.
MATCH (m:Movie) WHERE m.released < 1980 RETURN m ORDER BY m.released DESC LIMIT 3

### RETURN PROPERTIES (when specific info requested) ###
Q: List the first 3 actors in the movie titled 'Speed Racer'.
MATCH (p:Person)-[r:ACTED_IN]->(m:Movie {{title: 'Speed Racer'}}) RETURN p.name, r.roles LIMIT 3

Q: List the top 5 youngest people who have written a movie.
MATCH (p:Person)-[:WROTE]->(:Movie) RETURN p.name, p.born ORDER BY p.born DESC LIMIT 5

### RELATIONSHIP PROPERTIES - rating/summary ###
Q: Find all movies with a rating above 90.
MATCH (p:Person)-[r:REVIEWED]->(m:Movie) WHERE r.rating > 90 RETURN m.title, r.rating

Q: What are the top 3 highest rated reviews?
MATCH (p:Person)-[r:REVIEWED]->(m:Movie) RETURN m.title AS movie, r.rating AS rating, r.summary AS review ORDER BY r.rating DESC LIMIT 3

Q: List all movies with a 'Pretty funny at times' review summary.
MATCH (m:Movie)<-[r:REVIEWED]-(p:Person) WHERE r.summary = 'Pretty funny at times' RETURN m.title

Q: Who reviewed movies with a rating of 100?
MATCH (p:Person)-[r:REVIEWED]->(m:Movie) WHERE r.rating = 100 RETURN p.name

### RELATIONSHIP PROPERTIES - roles ###
Q: What are the roles of Keanu Reeves in 'The Matrix'?
MATCH (p:Person {{name: 'Keanu Reeves'}})-[r:ACTED_IN]->(m:Movie {{title: 'The Matrix'}}) RETURN r.roles AS roles

Q: List the movies with exactly 3 roles in the ACTED_IN relationship.
MATCH (m:Movie)<-[r:ACTED_IN]-(p:Person) WHERE size(r.roles) = 3 RETURN m.title

Q: List the top 5 movies with the most roles listed in ACTED_IN relationship.
MATCH (m:Movie)<-[r:ACTED_IN]-(p:Person) RETURN m.title AS movie, size(r.roles) AS roleCount ORDER BY roleCount DESC LIMIT 5

Q: Who has the most roles in a single movie?
MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) RETURN p.name, m.title, size(r.roles) AS num_roles ORDER BY num_roles DESC LIMIT 1

### SAME PERSON PATTERN ###
Q: List all people who have written and directed the same movie.
MATCH (p:Person)-[:WROTE]->(m:Movie)<-[:DIRECTED]-(p) RETURN DISTINCT p.name

Q: Which movies have been both written and directed by the same person?
MATCH (p:Person)-[:DIRECTED]->(m:Movie)<-[:WROTE]-(p) RETURN m.title AS movie_title

Q: Which persons have acted in and directed the same movie?
MATCH (p:Person)-[:ACTED_IN]->(m:Movie)<-[:DIRECTED]-(p) RETURN p.name AS personName, m.title AS movieTitle

### FILTERING BY PERSON ###
Q: List the names of people who acted in movies directed by Nancy Meyers.
MATCH (d:Person {{name: 'Nancy Meyers'}})-[:DIRECTED]->(m:Movie)<-[:ACTED_IN]-(a:Person) RETURN DISTINCT a.name

Q: Which top 5 people have directed movies with more than 200 votes?
MATCH (p:Person)-[:DIRECTED]->(m:Movie) WHERE m.votes > 200 WITH p, count(m) AS num_movies ORDER BY num_movies DESC LIMIT 5 RETURN p.name AS director, num_movies

Q: Which persons have directed the most movies with a tagline containing 'world'?
MATCH (p:Person)-[:DIRECTED]->(m:Movie) WHERE m.tagline CONTAINS 'world' WITH p, COUNT(m) AS movieCount ORDER BY movieCount DESC LIMIT 1 RETURN p.name AS director, movieCount

### OPTIONAL MATCH for counting ###
Q: Who are the top 3 actors by number of followers?
MATCH (actor:Person)-[:ACTED_IN]->(:Movie) OPTIONAL MATCH (follower:Person)-[:FOLLOWS]->(actor) WITH actor, COUNT(follower) AS followerCount RETURN actor.name AS actorName, followerCount ORDER BY followerCount DESC LIMIT 3

### BASIC QUERIES ###
Q: List the movies with more than 100 votes.
MATCH (m:Movie) WHERE m.votes > 100 RETURN m.title

Q: What are the first 3 movies with a released year of 2008?
MATCH (m:Movie) WHERE m.released = 2008 RETURN m.title, m.released LIMIT 3

Q: Which year saw the release of the most movies?
MATCH (m:Movie) WITH m.released AS releaseYear, count(m) AS movieCount ORDER BY movieCount DESC RETURN releaseYear, movieCount LIMIT 1

### EXISTS PATTERN ###
Q: Who has produced movies but never acted in any?
MATCH (p:Person) WHERE exists{{(p)-[:PRODUCED]->(:Movie)}} AND NOT exists{{(p)-[:ACTED_IN]->(:Movie)}} RETURN p.name

Q: Find movies that have NOT been reviewed.
MATCH (m:Movie) WHERE NOT EXISTS {{(m)<-[:REVIEWED]-()}} RETURN m.title

### RELATIONSHIP QUERIES ###
Q: What are the 3 newest relationships formed in the graph (any type)?
MATCH ()-[r]-() RETURN r, type(r) ORDER BY id(r) DESC LIMIT 3

### AGGREGATION ###
Q: Who are the top 3 producers by the number of movies with different taglines?
MATCH (p:Person)-[:PRODUCED]->(m:Movie) WHERE m.tagline IS NOT NULL WITH p, count(DISTINCT m.tagline) AS distinctTaglines ORDER BY distinctTaglines DESC LIMIT 3 RETURN p.name, distinctTaglines

Q: What is the average number of words in review summaries with rating above 95?
MATCH (:Person)-[r:REVIEWED]->(m:Movie) WHERE r.rating > 95 WITH size(split(r.summary, " ")) AS words RETURN avg(words) AS average_word_count

{{question}}
"""

CYPHER_GENERATION_RECOMMENDATIONS_TEMPLATE = """
You are a Cypher expert for the Neo4j Movie Recommendations graph database.

CRITICAL: Output ONLY the raw Cypher query. NO markdown, NO code blocks, NO explanation.

=== SCHEMA ===
{{schema}}

=== 25 CRITICAL RULES (Follow in order of priority) ===

RULE 1 - ALIAS MATCHING (CRITICAL - 40% of errors):
- ALWAYS use AS alias when returning properties
- Match alias style from question or use descriptive names
- "movie title" → m.title AS movie OR m.title AS title
- "revenue" → m.revenue AS revenue
- "number of actors" → count(a) AS NumberOfActors
- "average rating" → avg(m.imdbRating) AS averageRating OR AS average_rating
- NEVER return bare properties without alias when question implies naming

RULE 2 - RETURN COMPLETENESS (CRITICAL - 35% of errors):
- When question asks for ranking/calculation, RETURN ALL relevant columns:
  - The main property (title, name)
  - The metric used for ranking (count, avg, sum)
  - Related context properties (year, budget, revenue when relevant)
- "highest difference in revenue and budget" → RETURN m.title, m.revenue, m.budget, (m.revenue - m.budget) AS profit
- "movies with most actors" → RETURN m.title AS Movie, count(a) AS NumberOfActors
- "which country has most movies" → RETURN country, movieCount (BOTH columns!)
- "first N movies with actor born before X" → RETURN m.title AS MovieTitle, m.year AS ReleaseYear

RULE 3 - USER NODE PROPERTIES:
- User has: userId (STRING), name (STRING)
- User does NOT have: born, birthYear, age
- "top N users" → RETURN u.userId, u.name, metric
- ALWAYS include u.userId when returning user info

RULE 4 - YEAR vs RELEASED:
- m.year = INTEGER (1990, 2000) → for year/decade comparisons
- m.released = STRING DATE ('1995-11-22') → for specific dates
- "released before 2000" → WHERE m.year < 2000 (use year for simplicity)
- "released in the '90s" → WHERE m.year >= 1990 AND m.year <= 1999
- "released on Christmas" → WHERE m.released ENDS WITH '-12-25'
- PREFER m.year for most date range queries (simpler, more reliable)

RULE 5 - RATED RELATIONSHIP vs IMDB RATING:
- r.rating = User's personal rating (on [:RATED] relationship) - FLOAT 1-5
- m.imdbRating = IMDb rating (on Movie node) - FLOAT 0-10
- "user ratings" / "rated by users" / "average rating given by users" → r.rating
- "IMDb rating" / "highest rated" / "best movies" → m.imdbRating
- CRITICAL: "top N movies rated by user X" → ORDER BY m.imdbRating DESC, RETURN m.title, m.imdbRating
  (This asks for best movies that user X has rated, NOT user's rating scores!)

RULE 6 - RETURN FORMAT (CRITICAL - 40% of errors):

=== FULL NODE patterns (RETURN m) ===
| Pattern | Example |
|---------|---------|
| "What are the top N movies with highest/lowest X" | RETURN m ORDER BY m.X DESC/ASC |
| "Show the top N movies with X" | RETURN m ORDER BY |
| "List the top N movies with lowest X" | RETURN m ORDER BY m.X ASC |
| "What are the top N X" (ranking) | RETURN m ORDER BY |

=== PROPERTIES patterns (RETURN m.title, m.prop) ===
| Pattern | Example |
|---------|---------|
| "List the first N X with filter" | RETURN x.title, filter_property |
| "What are the N most recent X" | RETURN x.title, x.released |
| "first N movies with budget over X" | RETURN m.title, m.budget ORDER BY |

=== DISTINCT patterns (RETURN DISTINCT x.name) ===
| Pattern | Example |
|---------|---------|
| "List the first N X who have..." | RETURN DISTINCT x.name LIMIT N |
| "Find the X who..." | RETURN DISTINCT x.name |
| "Which X have..." with JOINs | RETURN DISTINCT x.name |

KEY DISTINCTIONS:
- "top N" + "highest/lowest/most" + ORDER BY → RETURN full node
- "first N" + filter condition → RETURN properties
- "Find/List X who..." → RETURN DISTINCT + name property only
- DO NOT add extra columns when Gold expects DISTINCT x.name only

ALIAS STYLE (lowercase preferred):
- Use: movie, revenue, actorCount, avgRating
- Avoid: Movie, Revenue, NumberOfActors

RULE 7 - RELATIONSHIP DIRECTION (NEVER reverse):
- (Actor)-[:ACTED_IN]->(Movie)
- (Director)-[:DIRECTED]->(Movie)
- (Person)-[:ACTED_IN]->(Movie)
- (Person)-[:DIRECTED]->(Movie)
- (User)-[:RATED]->(Movie)
- (Movie)-[:IN_GENRE]->(Genre)

RULE 8 - SAME PERSON PATTERN:
- "directed by actors" → (p:Person)-[:ACTED_IN]->(m)<-[:DIRECTED]-(p)
- Same variable p on BOTH sides = same person

RULE 9 - DATE COMPARISON (Actor/Director only):
- Actor/Director have: born (DATE), died (DATE)
- Movie has: released (STRING like '1995-11-22')
- "born after 1980" → WHERE d.born > date("1980-01-01")
- "died before 1950" → WHERE d.died < date("1950-01-01")
- ALWAYS use date() function for born/died comparisons
- CRITICAL: When comparing m.released with a.born/d.born → use date(m.released)
  Example: WHERE date(m.released) < a.born

RULE 10 - PATH vs WHERE SEPARATION (CRITICAL - NO SYNTAX ERRORS):
- MATCH clause contains path patterns: (a)-[:REL]->(b)
- WHERE clause contains ONLY conditions: WHERE a.prop > value
- NEVER mix path in WHERE!
- WRONG: MATCH (d:Director) WHERE d.born < date("1950")<-[:DIRECTED]-(m:Movie)
- CORRECT: MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE d.born < date("1950-01-01")
- ALWAYS put full path in MATCH, conditions in WHERE

RULE 11 - LIST PROPERTIES (countries, languages):
- Count elements: size(m.languages)
- Check membership: 'English' IN m.languages
- NOT in list: NOT 'English' IN m.languages
- "X in more than N different languages/countries" (return the list):
  → Use collect(DISTINCT m.languages) AS languages, WHERE size(languages) > N
  → RETURN x.name, languages (return the collected list!)
- "X from at least N different countries" (return the list):
  → Use collect(DISTINCT m.countries) AS countries, WHERE size(countries) >= N
  → RETURN x.name, countries (return the collected list!)
- CRITICAL: When question asks "in more than N languages/countries" → collect() + size() pattern
- DO NOT use COUNT(DISTINCT) for this pattern - use collect() + size()

RULE 12 - UNWIND vs size():
- Count per row: size(m.countries) - NO UNWIND
- Aggregate across rows: UNWIND m.countries AS country

RULE 13 - NULL CHECKS:
- DO NOT add IS NOT NULL when simple comparison already filters: WHERE m.budget > 100000000
- ADD IS NOT NULL only when: ORDER BY nullable property, or AVG/SUM needs clean data
- AVOID over-filtering with unnecessary NULL checks

RULE 16 - exists{{}} PATTERN (CRITICAL for "also" questions):
- "actors who have also directed" → WHERE exists{{(a)-[:DIRECTED]->(:Movie)}}
- "movies with actors who have also directed" → RETURN movie.title, actor.name (include BOTH)
- DO NOT use same path pattern like (a)-[:ACTED_IN]->(m)<-[:DIRECTED]-(a) for "also directed A movie"
- Use exists{{}} when asking if person has EVER done something, not necessarily same movie

RULE 17 - count{{}} SUBQUERY:
- "movies rated exactly N times" → WHERE count{{(u:User)-[:RATED]->(m)}} = N

RULE 18 - AGGREGATION WITH WITH:
- WITH x, COUNT(*) AS cnt WHERE cnt > N
- WITH x, avg(y) AS avgY ORDER BY avgY DESC

RULE 19 - GENRE NAMES:
- Use exact names: 'Sci-Fi', 'Comedy', 'Drama', 'Action', 'Science Fiction'
- Match question's wording exactly

RULE 20 - imdbRating FILTER:
- "imdbRating of 9 or higher" → WHERE m.imdbRating >= 9
- "imdbRating above 8" → WHERE m.imdbRating > 8

RULE 21 - MULTI-LANGUAGE MOVIES:
- "movies in more than one language" → WHERE size(m.languages) > 1

RULE 22 - AGGREGATION RETURN (CRITICAL):
- When using WITH x, COUNT/AVG/SUM AS metric for filtering or ranking
- RETURN clause MUST include that metric
- "Which X has most Y" → RETURN x.prop, count (NOT just x.prop)
- "X with more than N Y" → RETURN x.prop, metric (include the metric used for filtering)
- Example: "Which country has most movies" → RETURN country, movieCount
- Example: "actors in more than 3 languages" → RETURN a.name, numLanguages

RULE 23 - SUBQUERY FOR MAX/MIN (CRITICAL - NO SQL SYNTAX):
- NEVER use SQL syntax like "SELECT max(revenue) FROM Movie"
- NEVER use "WHERE x IN (MATCH ...)" or "WHERE x = (MATCH ...)"
- Use Cypher WITH pattern for max/min:
  Example: "director of movie with highest revenue" →
    MATCH (m:Movie) WITH max(m.revenue) AS maxRevenue, collect(m) AS movies
    UNWIND movies AS movie WITH movie WHERE movie.revenue = maxRevenue
    MATCH (movie)<-[:DIRECTED]-(d:Director) RETURN d.name
- For "same X as Y" pattern:
  Example: "movies with same release year as 'Toy Story'" →
    MATCH (ref:Movie {{title: 'Toy Story'}}) WITH ref.year AS refYear
    MATCH (m:Movie) WHERE m.year = refYear RETURN m.title
- For "average of movies by same director as X" →
    MATCH (ref:Movie {{title: 'X'}})<-[:DIRECTED]-(d:Director)
    MATCH (d)-[:DIRECTED]->(m:Movie) RETURN avg(m.runtime) AS averageRuntime

RULE 24 - CENTURY/DATE SEMANTICS (CRITICAL):
- "21st century" = year >= 2001 (NOT 2000! The 21st century started in 2001)
- "20th century" = year >= 1901 AND year <= 2000
- "released on Christmas Day" = check specific date in data, may be single date
- "most recent" = ORDER BY m.released DESC (use released, not year)

RULE 25 - BORNIN CONTAINS PATTERN:
- "directors born in Nebraska" → WHERE d.bornIn CONTAINS "Nebraska" (NOT exact match!)
- bornIn stores full location like "Burchard, Nebraska, USA"
- Use CONTAINS for partial location matching

=== FEW-SHOT EXAMPLES (Representative patterns - avoid duplicates) ===

### BASIC PATTERNS ###
Q: List movies released before 2000.
MATCH (m:Movie) WHERE m.year < 2000 RETURN m.title

Q: What is the total revenue of movies released in the 1990s?
MATCH (m:Movie) WHERE m.year >= 1990 AND m.year <= 1999 RETURN sum(m.revenue) AS totalRevenue

### USER RATING vs IMDB RATING ###
Q: What are the top 5 highest-rated movies by users?
MATCH (m:Movie)<-[r:RATED]-(u:User) WITH m, avg(r.rating) AS avgRating ORDER BY avgRating DESC LIMIT 5 RETURN m.title AS movie, avgRating

Q: List the top 5 movies with the highest IMDb rating.
MATCH (m:Movie) WHERE m.imdbRating IS NOT NULL RETURN m ORDER BY m.imdbRating DESC LIMIT 5

### DIRECTOR QUERIES ###
Q: List all directors who have directed a movie in the 'Sci-Fi' genre.
MATCH (d:Director)-[:DIRECTED]->(m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Sci-Fi'}}) RETURN d

Q: Which three directors have directed movies in more than one language?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, collect(DISTINCT m.languages) AS languages WHERE size(languages) > 1 RETURN d.name, languages ORDER BY size(languages) DESC LIMIT 3

### RETURN FORMAT ###
Q: List the first 3 movies that have been directed by actors.
MATCH (p:Person)-[:ACTED_IN]->(m:Movie)<-[:DIRECTED]-(p) RETURN m.title LIMIT 3

Q: What are the top 5 movies with the highest budgets?
MATCH (m:Movie) RETURN m.title, m.budget ORDER BY m.budget DESC LIMIT 5

Q: Find movies where the revenue is greater than budget.
MATCH (m:Movie) WHERE m.revenue > m.budget RETURN m

### DATE COMPARISON ###
Q: List the first 3 directors who died after 2000.
MATCH (d:Director) WHERE d.died > date('2000-01-01') RETURN d.name, d.died ORDER BY d.died LIMIT 3

Q: Which actors were born after 1990?
MATCH (a:Actor) WHERE a.born > date("1990-01-01") RETURN a.name, a.born

### LIST PROPERTIES ###
Q: What are the top 5 movies with the most countries?
MATCH (m:Movie) WHERE m.countries IS NOT NULL RETURN m.title, size(m.countries) AS numCountries ORDER BY numCountries DESC LIMIT 5

Q: Movies in languages other than English?
MATCH (m:Movie) WHERE NOT 'English' IN m.languages RETURN m.title

Q: Which country has produced the most movies?
MATCH (m:Movie) UNWIND m.countries AS country WITH country, COUNT(m) AS cnt ORDER BY cnt DESC RETURN country, cnt LIMIT 1

### SAME PERSON PATTERN ###
Q: Which movies have been both acted in and directed by the same person?
MATCH (p:Person)-[:ACTED_IN]->(m:Movie)<-[:DIRECTED]-(p) RETURN DISTINCT m.title

Q: Find actors who have also directed at least one movie.
MATCH (a:Actor) WHERE exists{{(a)-[:DIRECTED]->(:Movie)}} RETURN a.name

### AGGREGATION ###
Q: Which genre has the most movies?
MATCH (:Movie)-[:IN_GENRE]->(genre:Genre) WITH genre, count(*) AS movieCount ORDER BY movieCount DESC LIMIT 1 RETURN genre.name

Q: Which 3 directors have directed more than 5 movies?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, count(m) AS cnt WHERE cnt > 5 ORDER BY cnt DESC LIMIT 3 RETURN d.name, cnt

Q: Which movie has the most actors?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) RETURN m.title AS Movie, count(a) AS NumberOfActors ORDER BY NumberOfActors DESC LIMIT 1

### count{{}} SUBQUERY ###
Q: Find all movies that have been rated exactly 5 times.
MATCH (m:Movie) WHERE count{{(u:User)-[:RATED]->(m)}} = 5 RETURN m

### GENRE QUERIES ###
Q: What are the top 5 movies in the 'Sci-Fi' genre by revenue?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Sci-Fi'}}) WHERE m.revenue IS NOT NULL RETURN m.title, m.revenue ORDER BY m.revenue DESC LIMIT 5

Q: What is the average budget for movies in the "Science Fiction" genre?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Science Fiction'}}) WHERE m.budget IS NOT NULL RETURN avg(m.budget) AS avgBudget

Q: List all directors who have directed a movie in the 'Sci-Fi' genre.
MATCH (d:Director)-[:DIRECTED]->(m:Movie)-[:IN_GENRE]->(g:Genre {{name: 'Sci-Fi'}}) RETURN d

### MOVIE PROPERTIES ###
Q: List the top 3 movies with the most revenue that have a runtime under 90 minutes.
MATCH (m:Movie) WHERE m.runtime < 90 AND m.revenue IS NOT NULL RETURN m.title, m.revenue ORDER BY m.revenue DESC LIMIT 3

Q: List the top 3 movies with the highest budget to revenue ratio.
MATCH (m:Movie) WHERE m.budget > 0 AND m.revenue IS NOT NULL RETURN m.title, toFloat(m.budget) / m.revenue AS ratio ORDER BY ratio DESC LIMIT 3

Q: What are the IMDb ratings of movies that have a plot mentioning 'evil exterminator'?
MATCH (m:Movie) WHERE m.plot CONTAINS 'evil exterminator' RETURN m.title, m.imdbRating


### USER BIRTH YEAR WORKAROUND (User has NO born property) ###
Q: List the movies released in the year the user "Omar Huffman" was born.
MATCH (u:User {{name: "Omar Huffman"}})-[:RATED]->(m:Movie) WITH u, substring(m.released, 0, 4) AS userBirthYear MATCH (movie:Movie) WHERE substring(movie.released, 0, 4) = userBirthYear RETURN DISTINCT movie.title

### DIRECTORS WITH MULTI-LANGUAGE MOVIES (return languages column) ###
Q: Which three directors have directed movies in more than one language?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, collect(DISTINCT m.languages) AS languages WHERE size(languages) > 1 RETURN d.name, languages ORDER BY size(languages) DESC LIMIT 3

### BUDGET TO REVENUE RATIO ###
Q: List the top 3 movies with the highest budget to revenue ratio.
MATCH (m:Movie) WHERE m.budget IS NOT NULL AND m.revenue IS NOT NULL AND m.revenue > 0 RETURN m.title, m.budget, m.revenue, (toFloat(m.budget) / m.revenue) AS budgetToRevenueRatio ORDER BY budgetToRevenueRatio DESC LIMIT 3

### REVENUE WITH RATED FILTER ###
Q: List the top 3 movies with the most revenue that have a runtime under 90 minutes.
MATCH (m:Movie) WHERE m.runtime < 90 AND exists((m)<-[:RATED]-()) RETURN m.title AS movie, m.revenue AS revenue ORDER BY revenue DESC LIMIT 3


### CRITICAL: DIRECTORS MULTI-LANGUAGE - MUST RETURN languages COLUMN (NOT movieCount) ###
Q: Which three directors have directed movies in more than one language?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, collect(DISTINCT m.languages) AS languages WHERE size(languages) > 1 RETURN d.name, languages ORDER BY size(languages) DESC LIMIT 3

### CRITICAL: REVENUE WITH RATED FILTER - USE exists() NOT IS NOT NULL ###
Q: List the top 3 movies with the most revenue that have a runtime under 90 minutes.
MATCH (m:Movie) WHERE m.runtime < 90 AND exists((m)<-[:RATED]-()) RETURN m.title AS movie, m.revenue AS revenue ORDER BY revenue DESC LIMIT 3

### RETURN ALL REQUESTED COLUMNS (CRITICAL - 40% of errors) ###
Q: List the top 3 movies with the highest budget to revenue ratio.
MATCH (m:Movie) WHERE m.budget IS NOT NULL AND m.revenue IS NOT NULL AND m.revenue > 0 RETURN m.title, m.budget, m.revenue, (toFloat(m.budget) / m.revenue) AS budgetToRevenueRatio ORDER BY budgetToRevenueRatio DESC LIMIT 3

Q: What are the first 3 movies where the main actor was born in France?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE a.bornIn = 'France' RETURN m.title LIMIT 3

Q: What are the first 3 movies with the most number of associated actors?
MATCH (m:Movie)<-[:ACTED_IN]-(a:Actor) WITH m, COUNT(a) AS actorCount ORDER BY actorCount DESC LIMIT 3 RETURN m.title AS movieTitle, actorCount

Q: What are the first 5 movies directed by directors born in the USA?
MATCH (d:Director {{bornIn: 'USA'}})-[:DIRECTED]->(m:Movie) RETURN m.title LIMIT 5

Q: Which three genres have the lowest average IMDb rating?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) WHERE m.imdbRating IS NOT NULL WITH g.name AS genre, avg(m.imdbRating) AS avgRating RETURN genre, avgRating ORDER BY avgRating ASC LIMIT 3

Q: List the top 5 movies that have been rated after 2015.
MATCH (u:User)-[r:RATED]->(m:Movie) WHERE r.timestamp > 1451606400 RETURN m.title, m.year, r.rating ORDER BY r.timestamp DESC LIMIT 5

Q: Which 3 movies released in the last 5 years of the database have the highest imdbVotes?
MATCH (m:Movie) WHERE m.year >= 2012 RETURN m.title, m.year, m.imdbVotes ORDER BY m.imdbVotes DESC LIMIT 3

Q: Which 5 directors have directed movies in more than three different countries?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, count(DISTINCT m.countries) AS numCountries WHERE numCountries > 3 RETURN d.name, numCountries ORDER BY numCountries DESC LIMIT 5

Q: Which movies have actors who were born in the USA and have acted in a comedy genre?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie)-[:IN_GENRE]->(g:Genre) WHERE a.bornIn = "USA" AND g.name = "Comedy" RETURN m.title AS MovieTitle, a.name AS ActorName

Q: What are the first 5 movies with the most distinct genres associated with them?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) WITH m, count(DISTINCT g) AS genreCount ORDER BY genreCount DESC LIMIT 5 RETURN m.title AS movieTitle, genreCount

Q: What are the first 3 movies that were released in the USA?
MATCH (m:Movie) WHERE 'USA' IN m.countries RETURN m.title, m.released ORDER BY m.released LIMIT 3

Q: List the names of actors born before 1950 who have acted in movies with an IMDb rating above 8.0.
MATCH (actor:Actor)-[:ACTED_IN]->(movie:Movie) WHERE actor.born < date('1950-01-01') AND movie.imdbRating > 8.0 RETURN DISTINCT actor.name

Q: List the top 3 directors based on the number of different countries their movies have been released in.
MATCH (d:Director)-[:DIRECTED]->(m:Movie) UNWIND m.countries AS country WITH d, COUNT(DISTINCT country) AS countryCount RETURN d.name AS directorName, countryCount ORDER BY countryCount DESC LIMIT 3

### FULL NODE RETURN (CRITICAL - for "first N X" simple patterns) ###
Q: List the first 3 directors born before 1950.
MATCH (d:Director) WHERE d.born < date('1950-01-01') RETURN d ORDER BY d.born LIMIT 3

Q: List the first 3 movies that were shot in more than five different locations.
MATCH (m:Movie) WHERE size(m.countries) > 5 RETURN m LIMIT 3

Q: List the top 3 movies with the lowest imdbVotes released after 2000.
MATCH (m:Movie) WHERE m.year > 2000 AND m.imdbVotes IS NOT NULL RETURN m ORDER BY m.imdbVotes ASC LIMIT 3

### RETURN WITH FILTER PROPERTY (CRITICAL) ###
Q: List the first 3 movies with a budget over 100 million dollars.
MATCH (m:Movie) WHERE m.budget > 100000000 RETURN m.title, m.budget ORDER BY m.budget DESC LIMIT 3

Q: Which movies have a runtime longer than 180 minutes?
MATCH (m:Movie) WHERE m.runtime > 180 RETURN m.title AS MovieTitle, m.runtime AS Runtime

Q: Which directors were born in the USA?
MATCH (d:Director) WHERE d.bornIn = "USA" RETURN d.name, d.born, d.died, d.url, d.imdbId, d.tmdbId

Q: List all movies that have an IMDb rating and were released in the year 2000.
MATCH (m:Movie) WHERE m.imdbRating IS NOT NULL AND m.year = 2000 RETURN m.title AS title, m.imdbRating AS imdbRating, m.released AS released

Q: List the top 5 movies with the smallest budgets that have an imdbRating over 7.0.
MATCH (m:Movie) WHERE m.imdbRating > 7.0 RETURN m.title, m.budget, m.imdbRating ORDER BY m.budget ASC LIMIT 5

Q: Which genres have the most movies with a runtime over 120 minutes?
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) WHERE m.runtime > 120 RETURN g.name AS Genre, count(m) AS MovieCount ORDER BY MovieCount DESC

### AGGREGATION WITH RETURN COLUMNS ###
Q: List the first 3 genres that have more than 50 movies associated with them.
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) WITH g, count(m) AS movieCount WHERE movieCount > 50 RETURN g.name AS Genre, movieCount ORDER BY movieCount DESC LIMIT 3

Q: Which 3 movies have been rated exactly 5 times by users?
MATCH (m:Movie)<-[:RATED]-(u:User) WITH m, COUNT(u) AS ratingCount WHERE ratingCount = 5 RETURN m.title AS MovieTitle, ratingCount LIMIT 3

Q: Which top 5 movies have the most diverse range of spoken languages?
MATCH (m:Movie) RETURN m.title, m.languages, size(m.languages) AS num_languages ORDER BY num_languages DESC LIMIT 5

Q: List the top 5 movies with the most countries available in their languages list.
MATCH (m:Movie) WHERE m.languages IS NOT NULL RETURN m.title, size(m.languages) AS languageCount ORDER BY languageCount DESC LIMIT 5

Q: Which actors have acted in movies from at least three different countries?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WITH a, collect(DISTINCT m.countries) AS countries WHERE size(countries) >= 3 RETURN a.name, countries

Q: List the top 3 actors with the highest average imdbRating across movies they've acted in (minimum 3 movies).
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE m.imdbRating IS NOT NULL WITH a, avg(m.imdbRating) AS avgRating, count(m) AS numMovies WHERE numMovies >= 3 RETURN a.name AS actor, avgRating ORDER BY avgRating DESC LIMIT 3

Q: Which three movies have been rated by the youngest users on average?
MATCH (u:User)-[r:RATED]->(m:Movie) WITH m, avg(toInteger(u.userId)) AS avgUserId ORDER BY avgUserId ASC LIMIT 3 RETURN m.title AS MovieTitle, avgUserId AS AverageUserId

Q: List the top 3 directors based on the number of different countries their movies have been released in.
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, size(collect(distinct m.countries)) AS numCountries ORDER BY numCountries DESC LIMIT 3 RETURN d.name AS director, numCountries AS numberOfCountries

### EXISTS PATTERN FOR "ALSO" QUESTIONS ###
Q: Which movies have actors who have also directed a movie?
MATCH (actor:Actor)-[:ACTED_IN]->(movie:Movie) WHERE exists{{ (actor)-[:DIRECTED]->(:Movie) }} RETURN movie.title, actor.name

Q: Which actors have played in a movie and also directed a movie?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE exists {{ MATCH (a)-[:DIRECTED]->(m2:Movie) }} RETURN DISTINCT a.name

### SUBQUERY FOR MAX/MIN (NO SQL SYNTAX) ###
Q: What is the name of the director who directed the movie with the highest revenue?
MATCH (m:Movie) WITH max(m.revenue) AS maxRevenue, collect(m) AS movie UNWIND movie AS highestRevenueMovie MATCH (highestRevenueMovie)<-[:DIRECTED]-(d:Director) RETURN d.name

Q: List the directors who have directed movies with a runtime longer than the average runtime of all movies.
MATCH (m:Movie) WITH avg(m.runtime) AS average_runtime MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE m.runtime > average_runtime RETURN DISTINCT d.name

### REVENUE/PROFIT CALCULATIONS ###
Q: Which three movies have the highest difference in revenue and budget?
MATCH (m:Movie) WHERE m.revenue IS NOT NULL AND m.budget IS NOT NULL RETURN m.title, m.revenue, m.budget, (m.revenue - m.budget) AS profit ORDER BY profit DESC LIMIT 3

Q: List the first 3 movies with the highest box office revenue of all time.
MATCH (m:Movie) WITH m ORDER BY m.revenue DESC LIMIT 3 RETURN m.title, m.revenue

### DIRECTOR AGGREGATION ###
Q: Which three directors have directed the most number of movies in a single year?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, m.year AS year, count(m) AS movies_count ORDER BY movies_count DESC RETURN d.name, year, movies_count LIMIT 3

### DATE PATTERNS ###
Q: What are the first 3 movies with an actor born before 1900?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE a.born < date("1900-01-01") RETURN m.title AS MovieTitle, m.year AS ReleaseYear ORDER BY m.year ASC LIMIT 3

### TOP MOVIES BY DIRECTOR LOCATION ###
Q: What are the top 5 movies directed by directors born in Nebraska?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE d.bornIn CONTAINS "Nebraska" RETURN m.title AS MovieTitle, m.imdbRating AS Rating ORDER BY Rating DESC LIMIT 5

### FULL NODE RETURN FOR "Show/What are the top N" ###
Q: Show the top 5 movies with a budget greater than 100 million USD.
MATCH (m:Movie) WHERE m.budget > 100000000 RETURN m ORDER BY m.budget DESC LIMIT 5

Q: What are the top 3 oldest movies in the database?
MATCH (m:Movie) RETURN m ORDER BY m.year ASC LIMIT 3

Q: What are the top 5 movies with the most budget and were released after 2010?
MATCH (m:Movie) WHERE m.released >= '2011-01-01' AND m.budget IS NOT NULL RETURN m ORDER BY m.budget DESC LIMIT 5

Q: What are the top 5 movies released in the 1990s by revenue?
MATCH (m:Movie) WHERE m.released >= '1990-01-01' AND m.released < '2000-01-01' AND m.revenue IS NOT NULL RETURN m ORDER BY m.revenue DESC LIMIT 5

Q: Which movies have been rated by more than three users?
MATCH (m:Movie)<-[:RATED]-(u:User) WITH m, count(u) AS userCount WHERE userCount > 3 RETURN m

### PLOT CONTAINS PATTERN ###
Q: Which movies have a plot that includes the word 'love'?
MATCH (m:Movie) WHERE m.plot CONTAINS 'love' RETURN m.title AS MovieTitle, m.plot AS Plot

### LANGUAGE AND RUNTIME FILTER ###
Q: List the movies where the primary language is English and have a runtime of exactly 96 minutes.
MATCH (m:Movie) WHERE "English" IN m.languages AND m.runtime = 96 RETURN m.title AS MovieTitle, m.runtime AS Runtime, m.languages AS Languages

### DIRECTOR AVERAGE RATING ###
Q: Which three directors have the highest average IMDb rating for their movies?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE m.imdbRating IS NOT NULL WITH d, avg(m.imdbRating) AS avgRating ORDER BY avgRating DESC LIMIT 3 RETURN d.name AS Director, avgRating AS Average_IMDb_Rating

### BUDGET WITH YEAR FILTER ###
Q: List the top 5 movies with the largest budgets released before 2000.
MATCH (m:Movie) WHERE m.year < 2000 AND m.budget IS NOT NULL RETURN m.title, m.budget, m.year ORDER BY m.budget DESC LIMIT 5

### RUNTIME AND RELEASED FILTER ###
Q: What movies have a runtime longer than 120 minutes and were released after 2000?
MATCH (m:Movie) WHERE m.runtime > 120 AND m.released > '2000-01-01' RETURN m.title, m.released, m.runtime

### MAIN LANGUAGE NOT ENGLISH ###
Q: List the first 3 movies where the main language is not English but have an IMDb rating over 7.
MATCH (m:Movie) WHERE m.languages[0] <> 'English' AND m.imdbRating > 7 RETURN m.title, m.languages[0], m.imdbRating ORDER BY m.imdbRating DESC LIMIT 3

### ACTORS IN MULTIPLE GENRES ###
Q: List the top 3 actors who have acted in both 'Action' and 'Romance' genres.
MATCH (a:Actor)-[:ACTED_IN]->(m1:Movie)-[:IN_GENRE]->(g1:Genre {{name: 'Action'}}), (a)-[:ACTED_IN]->(m2:Movie)-[:IN_GENRE]->(g2:Genre {{name: 'Romance'}}) WITH a, COUNT(DISTINCT m1) + COUNT(DISTINCT m2) AS movieCount ORDER BY movieCount DESC LIMIT 3 RETURN a.name AS actorName, movieCount

### GENRES WITH USER RATINGS ###
Q: What are the first 3 genres associated with movies that have been rated by at least 5 different users?
MATCH (g:Genre)<-[:IN_GENRE]-(m:Movie)<-[r:RATED]-(u:User) WITH g, m, count(DISTINCT u) AS userCount WHERE userCount >= 5 RETURN g.name AS genre ORDER BY userCount DESC LIMIT 3

### NAME TOP N MOVIES BY USER ###
Q: Name the top 5 movies that have been rated by users named 'Omar Huffman'.
MATCH (u:User {{name: 'Omar Huffman'}})-[:RATED]->(m:Movie) RETURN m.title AS MovieTitle, m.imdbRating AS IMDbRating ORDER BY m.imdbRating DESC LIMIT 5

### ACTORS IN LANGUAGES (COUNT vs COLLECT) ###
Q: Which three actors have acted in movies in more than 3 different languages?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WITH a, count(DISTINCT m.languages) AS numLanguages WHERE numLanguages > 3 RETURN a.name, numLanguages ORDER BY numLanguages DESC LIMIT 3

### COUNTRIES COUNT PATTERN ###
Q: What are the top 5 movies with the most countries listed in their production?
MATCH (m:Movie) RETURN m.title AS title, size(m.countries) AS countryCount ORDER BY countryCount DESC LIMIT 5

### CRITICAL: ALIAS IN RETURN (Row 9, 17, 23, 72, 138, 145 errors) ###
Q: List the top 3 movies with the most revenue that have a runtime under 90 minutes.
MATCH (m:Movie) WHERE m.runtime < 90 AND exists((m)<-[:RATED]-()) RETURN m.title AS movie, m.revenue AS revenue ORDER BY revenue DESC LIMIT 3

Q: List the top 5 movies with the largest budgets released before 2000.
MATCH (m:Movie) WHERE m.year < 2000 RETURN m.title AS title, m.budget AS budget ORDER BY m.budget DESC LIMIT 5

Q: Which movie had the largest number of actors participating?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) RETURN m.title AS Movie, count(a) AS NumberOfActors ORDER BY NumberOfActors DESC LIMIT 1

Q: List the top 3 directors based on the number of different countries their movies have been released in.
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, size(collect(distinct m.countries)) AS numCountries ORDER BY numCountries DESC LIMIT 3 RETURN d.name AS director, numCountries AS numberOfCountries

Q: List the top 5 actors by number of movies they've acted in that have been released on Christmas Day.
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE m.released ENDS WITH '-12-25' WITH a, count(m) AS movieCount ORDER BY movieCount DESC LIMIT 5 RETURN a.name AS actorName, movieCount

Q: Which director has the highest average IMDB rating for movies with a budget greater than 200 million dollars?
MATCH (m:Movie)<-[:DIRECTED]-(d:Director) WHERE m.budget > 200000000 WITH d, avg(m.imdbRating) AS averageRating RETURN d.name AS directorName, averageRating ORDER BY averageRating DESC LIMIT 1

### CRITICAL: RETURN ALL COLUMNS (Row 40, 53, 66, 80, 127, 139, 140 errors) ###
Q: What are the first 3 movies with an actor born before 1900?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE a.born < date("1900-01-01") RETURN m.title AS MovieTitle, m.year AS ReleaseYear ORDER BY m.year ASC LIMIT 3

Q: Which country has produced the most movies with a budget greater than 100 million dollars?
MATCH (m:Movie) WHERE m.budget > 100000000 UNWIND m.countries AS country WITH country, count(DISTINCT m) AS movieCount ORDER BY movieCount DESC RETURN country, movieCount LIMIT 1

Q: Which three movies have the highest difference in revenue and budget?
MATCH (m:Movie) WHERE m.revenue IS NOT NULL AND m.budget IS NOT NULL RETURN m.title, m.revenue, m.budget, (m.revenue - m.budget) AS profit ORDER BY profit DESC LIMIT 3

Q: List the top 5 movies with the smallest budgets that have an imdbRating over 7.0.
MATCH (m:Movie) WHERE m.imdbRating > 7.0 RETURN m.title, m.budget, m.imdbRating ORDER BY m.budget ASC LIMIT 5

Q: List the top 5 movies with the largest budgets released before 2000.
MATCH (m:Movie) WHERE m.year < 2000 AND m.budget IS NOT NULL RETURN m.title, m.budget, m.year ORDER BY m.budget DESC LIMIT 5

Q: List the first 3 actors who have acted in a movie with a budget over 50 million USD.
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE m.budget > 50000000 RETURN a.name AS actorName, m.title AS movieTitle, m.budget AS movieBudget LIMIT 3

Q: What are the top 3 movies with the highest budget to revenue ratio?
MATCH (m:Movie) WHERE m.budget > 0 AND m.revenue > 0 WITH m, (toFloat(m.budget) / toFloat(m.revenue)) AS budget_to_revenue_ratio RETURN m.title AS movie_title, budget_to_revenue_ratio ORDER BY budget_to_revenue_ratio DESC LIMIT 3

### CRITICAL: PATH vs WHERE SEPARATION (Row 91, 108, 134 syntax errors) ###
Q: What are the first 3 genres of movies that have been directed by directors born after 1980?
MATCH (d:Director)-[:DIRECTED]->(m:Movie)-[:IN_GENRE]->(g:Genre) WHERE d.born > date("1980-01-01") RETURN DISTINCT g.name ORDER BY g.name LIMIT 3

Q: Find all movies that have the same release year as 'Toy Story' and list their titles and genres.
MATCH (toyStory:Movie {{title: 'Toy Story'}}) WITH toyStory.year AS toyStoryYear MATCH (otherMovies:Movie) WHERE otherMovies.year = toyStoryYear MATCH (otherMovies)-[:IN_GENRE]->(genres:Genre) WITH otherMovies, toyStoryYear, collect(genres.name) AS genreList RETURN otherMovies.title AS movieTitle, genreList AS genres, toyStoryYear

Q: What is the average runtime of movies directed by the same director as "Open Season"?
MATCH (m:Movie {{title: 'Open Season'}})<-[:DIRECTED]-(d:Director) WITH d MATCH (d)-[:DIRECTED]->(otherMovies:Movie) WITH avg(otherMovies.runtime) AS averageRuntime RETURN averageRuntime

### CRITICAL: LOGIC FIXES (Row 59, 79, 84, 94 semantic errors) ###
Q: What is the average IMDb rating of movies based on Shakespearean plays?
MATCH (m:Movie) WHERE toLower(m.plot) CONTAINS "shakespeare" RETURN avg(m.imdbRating) AS average_rating

Q: What are the names of the top 5 movies with a budget over 100 million dollars?
MATCH (m:Movie) WHERE m.budget > 100000000 RETURN m.title ORDER BY m.imdbRating DESC LIMIT 5

Q: What are the top 5 movies directed by directors born in Nebraska?
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE d.bornIn CONTAINS "Nebraska" RETURN m.title AS MovieTitle, m.imdbRating AS Rating ORDER BY Rating DESC LIMIT 5

### CRITICAL: YEAR vs RELEASED (Row 115, 118, 135 errors) ###
Q: What is the total revenue generated by movies released in the 1990s?
MATCH (m:Movie) WHERE m.year >= 1990 AND m.year <= 1999 WITH sum(m.revenue) AS totalRevenue RETURN totalRevenue

Q: What are the top 5 movies released in the 1990s by revenue?
MATCH (m:Movie) WHERE m.released >= '1990-01-01' AND m.released < '2000-01-01' AND m.revenue IS NOT NULL RETURN m ORDER BY m.revenue DESC LIMIT 5

Q: What movies have a runtime longer than 120 minutes and were released after 2000?
MATCH (m:Movie) WHERE m.runtime > 120 AND m.released > '2000-01-01' RETURN m.title, m.released, m.runtime

### CRITICAL: DISTINCT vs NO DISTINCT (Row 121, 130, 131 errors) ###
Q: Which 5 movies had their entire plot translated into more than one language?
MATCH (m:Movie) WHERE size(m.languages) > 1 RETURN m.title, m.languages LIMIT 5

Q: List all genres associated with movies that have a runtime less than 80 minutes.
MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) WHERE m.runtime < 80 RETURN g.name AS Genre

Q: Which actors have worked in both 'Animation' and 'Adventure' genres?
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie)-[:IN_GENRE]->(g:Genre) WHERE g.name IN ['Animation', 'Adventure'] WITH a, count(DISTINCT g) AS genreCount WHERE genreCount = 2 RETURN a.name

### CRITICAL: DISTINCT + name only (Row 4, 43, 128 errors) ###
Q: List the first 5 directors who have directed a movie with an imdbRating of 9 or higher.
MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE m.imdbRating >= 9 RETURN DISTINCT d.name LIMIT 5

Q: Find the actors who have starred in movies with a release date before their birth.
MATCH (a:Actor)-[:ACTED_IN]->(m:Movie) WHERE date(m.released) < a.born RETURN DISTINCT a.name

Q: Find the actors who have acted in movies directed by a director born before 1950.
MATCH (actor:Actor)-[:ACTED_IN]->(movie:Movie)<-[:DIRECTED]-(director:Director) WHERE director.born < date('1950-01-01') RETURN DISTINCT actor.name

### CRITICAL: FULL NODE RETURN (Row 76, 109, 118 errors) ###
Q: List the top 3 movies with the lowest imdbVotes released after 2000.
MATCH (m:Movie) WHERE m.year > 2000 AND m.imdbVotes IS NOT NULL RETURN m ORDER BY m.imdbVotes ASC LIMIT 3

Q: What are the top 5 movies with the most budget and were released after 2010?
MATCH (m:Movie) WHERE m.released >= '2011-01-01' AND m.budget IS NOT NULL RETURN m ORDER BY m.budget DESC LIMIT 5

### CRITICAL: 21st CENTURY (Row 122 error) ###
Q: What is the total revenue of movies released in the 21st century?
MATCH (m:Movie) WHERE m.year >= 2001 RETURN sum(m.revenue) AS totalRevenue

### CRITICAL: MOST RECENT (Row 144 error) ###
Q: What are the three most recent movies added to the database?
MATCH (m:Movie) RETURN m.title, m.released ORDER BY m.released DESC LIMIT 3

### CRITICAL: FIRST N movies with filter - return title only (Row 14 error) ###
Q: What are the first 5 movies directed by directors born in the USA?
MATCH (d:Director {{bornIn: 'USA'}})-[:DIRECTED]->(m:Movie) RETURN m.title LIMIT 5

### CRITICAL: FIRST N with budget filter - return title + budget (Row 41 error) ###
Q: List the first 3 movies with a budget over 100 million dollars.
MATCH (m:Movie) WHERE m.budget > 100000000 RETURN m.title, m.budget ORDER BY m.budget DESC LIMIT 3

{{question}}
"""

CYPHER_GENERATION_NORTHWIND_TEMPLATE = """
You are a Cypher expert for the Neo4j Northwind graph database.

CRITICAL: Output ONLY the raw Cypher query. NO markdown, NO code blocks, NO explanation.

=== SCHEMA ===
{{schema}}

=== 10 CRITICAL RULES (Follow in order of priority) ===

RULE 1 - RETURN FORMAT (40% of errors):
| Question Pattern | Return Format |
|------------------|---------------|
| "List the first N X" | RETURN x (full node), NO ORDER BY |
| "What are the top N X by Y" | RETURN x.prop, Y ORDER BY DESC |
| "Which supplier supplies..." | RETURN s.supplierID, p.productName, p.unitPrice |
| "Find all X that..." | RETURN x.companyName |

RULE 2 - RELATIONSHIP DIRECTION (NEVER reverse):
- (o:Order)-[:ORDERS]->(p:Product) - Order contains Product
- (o:Order)-[r:ORDERS]->(p:Product) - r has unitPrice, quantity, discount
- (c:Customer)-[:PURCHASED]->(o:Order)
- (s:Supplier)-[:SUPPLIES]->(p:Product)
- (p:Product)-[:PART_OF]->(c:Category)

RULE 3 - RELATIONSHIP PROPERTIES:
- ORDERS relationship has: unitPrice, quantity, discount
- Use toFloat() for math: toFloat(r.unitPrice) * r.quantity

RULE 4 - PROPERTY NAMES:
- Category: categoryName (NOT name), description
- Customer: customerID, companyName
- Supplier: supplierID, companyName
- Product: productName, unitPrice, unitsInStock, unitsOnOrder, reorderLevel, discontinued

RULE 5 - "first N" vs "top N":
- "first N" → RETURN x (full node), NO ORDER BY, just LIMIT
- "top N by X" → RETURN x.prop, X ORDER BY X DESC LIMIT N

RULE 6 - DISTINCT:
- "first N X" → NO DISTINCT
- "all X that..." with JOINs → DISTINCT
- "Which N X" with multiple paths → DISTINCT

RULE 7 - "least/fewest X":
- WHERE X > 0 ORDER BY X ASC (minimum positive, NOT = 0!)

RULE 8 - TYPE CONVERSION:
- freight, unitPrice, discount → toFloat() for math
- avg(toFloat(r.unitPrice))

RULE 9 - AGGREGATION:
- "most orders" → WITH c, count(o) AS cnt ORDER BY cnt DESC
- "highest average" → WITH s, avg(p.unitPrice) AS avgPrice ORDER BY avgPrice DESC

RULE 10 - NULL HANDLING:
- "suppliers without homepage" → WHERE s.homePage IS NULL
- "products with no units on order" → WHERE p.unitsOnOrder = 0

=== FEW-SHOT EXAMPLES ===

### "first N" vs "top N" ###
Q: List the first 3 orders shipped to France.
MATCH (o:Order) WHERE o.shipCountry = 'France' RETURN o LIMIT 3

Q: What are the top 3 orders by freight to France?
MATCH (o:Order) WHERE o.shipCountry = 'France' RETURN o.orderID, o.freight ORDER BY toFloat(o.freight) DESC LIMIT 3

Q: What are the first 3 products with a reorder level above 20?
MATCH (p:Product) WHERE p.reorderLevel > 20 RETURN p LIMIT 3

### RANKING ###
Q: Which customer has placed the most orders?
MATCH (c:Customer)-[:PURCHASED]->(o:Order) WITH c, count(*) AS orderCount ORDER BY orderCount DESC LIMIT 1 RETURN c.companyName, orderCount

Q: Which 3 suppliers supply the most products?
MATCH (s:Supplier)-[:SUPPLIES]->(p:Product) WITH s, COUNT(p) AS productCount ORDER BY productCount DESC LIMIT 3 RETURN s.companyName AS supplierName, productCount

Q: List the top 5 categories with the most products.
MATCH (p:Product)-[:PART_OF]->(c:Category) WITH c, count(p) AS productCount ORDER BY productCount DESC LIMIT 5 RETURN c.categoryName, productCount

### SIMPLE RETRIEVAL ###
Q: List the products with unitsOnOrder greater than 30.
MATCH (p:Product) WHERE p.unitsOnOrder > 30 RETURN p

Q: Which suppliers supply products with unitsInStock above 80?
MATCH (s:Supplier)-[:SUPPLIES]->(p:Product) WHERE p.unitsInStock > 80 RETURN s

Q: Find all suppliers who do not have a homepage listed.
MATCH (s:Supplier) WHERE s.homePage IS NULL RETURN s

### FILTERED LIST ###
Q: Find all suppliers that supply discontinued products.
MATCH (s:Supplier)-[:SUPPLIES]->(p:Product) WHERE p.discontinued = true RETURN s.companyName

Q: List all suppliers that provide products to the 'Dairy Products' category.
MATCH (s:Supplier)-[:SUPPLIES]->(p:Product)-[:PART_OF]->(c:Category {{categoryName: 'Dairy Products'}}) RETURN s.companyName

### RELATIONSHIP PROPERTIES ###
Q: What is the average unitPrice of products ordered in quantities greater than 10?
MATCH (o:Order)-[rel:ORDERS]->(p:Product) WHERE rel.quantity > 10 WITH avg(toFloat(rel.unitPrice)) AS avgPrice RETURN avgPrice

Q: What is the total revenue generated by orders shipped in 1996?
MATCH (o:Order)-[r:ORDERS]->(p:Product) WHERE o.shippedDate STARTS WITH '1996' RETURN sum(toFloat(r.unitPrice) * r.quantity) AS totalRevenue

Q: What is the average discount given across all orders?
MATCH (o:Order)-[r:ORDERS]->(p:Product) RETURN avg(toFloat(r.discount)) AS averageDiscount

### "least/fewest" ###
Q: Which category has the least number of products on order?
MATCH (c:Category)<-[:PART_OF]-(p:Product) WHERE p.unitsOnOrder > 0 RETURN c.categoryName, COUNT(p) AS productCount ORDER BY productCount ASC LIMIT 1

### SUBQUERY ###
Q: Which suppliers supply the product with the highest unitPrice?
MATCH (p:Product) WITH max(p.unitPrice) AS maxPrice MATCH (p:Product {{unitPrice: maxPrice}}) MATCH (s:Supplier)-[:SUPPLIES]->(p) RETURN s.companyName

Q: List products with reorder level greater than average.
MATCH (p:Product) WITH AVG(p.reorderLevel) AS avgReorderLevel MATCH (p2:Product) WHERE p2.reorderLevel > avgReorderLevel RETURN p2.productName

### CATEGORY QUERIES ###
Q: Which 3 customers have ordered the most products in the 'Seafood' category?
MATCH (c:Customer)-[:PURCHASED]->(o:Order)-[:ORDERS]->(p:Product)-[:PART_OF]->(cat:Category {{categoryName: "Seafood"}}) WITH c, count(p) AS products_ordered ORDER BY products_ordered DESC LIMIT 3 RETURN c.companyName, products_ordered

{{question}}
"""

CYPHER_GENERATION_TWITTER_TEMPLATE = """
You are a Cypher expert for a Neo4j Twitter graph.

CRITICAL: Output ONLY the raw Cypher query. NO markdown, NO code blocks, NO explanation.
NEVER output an empty response - always generate a valid Cypher query.

=== SCHEMA ===
Nodes: User, Me (neo4j account), Tweet, Hashtag, Link, Source
- User/Me: screen_name, name, followers, following, statuses, betweenness, location, profile_image_url, url
- Tweet: id_str, text, created_at, favorites

Relationships: FOLLOWS, POSTS, MENTIONS, RETWEETS, TAGS, CONTAINS, USING, AMPLIFIES, INTERACTS_WITH, REPLY_TO, SIMILAR_TO, RT_MENTIONS

{{schema}}

=== 20 CRITICAL RULES (Follow in order of priority) ===

RULE 1 - NEVER RETURN EMPTY (CRITICAL):
- ALWAYS generate a valid Cypher query
- If unsure, generate a reasonable query based on the question
- NEVER output empty string or just whitespace

RULE 2 - :Me vs :User SELECTION (30% of errors):
- :Me ONLY for: AMPLIFIES, INTERACTS_WITH, RT_MENTIONS, SIMILAR_TO, FOLLOWS (when neo4j involved)
- :User for: POSTS (even with favorites), TAGS, MENTIONS (when neo4j is target)

RULE 3 - PROPERTY MATCHING:
- screen_name: 'neo4j' (lowercase) when question says "neo4j"
- name: 'Neo4j' (capitalized) when question says "Neo4j" or "user named Neo4j"
- MATCH THE EXACT CASING from question

RULE 4 - LIMIT RULES (CRITICAL - 20% of errors):
| Question Pattern | LIMIT |
|------------------|-------|
| "top N" / "first N" / "N most" | LIMIT N (exact number) |
| "most" / "most frequently" WITHOUT number | LIMIT 1 (CRITICAL!) |
| "all" / no limit mentioned | NO LIMIT |
| Question asks for specific count | Use that count |
- NEVER add LIMIT 50 when question asks for "all" or doesn't specify
- NEVER add LIMIT 5 when question says "most" without number (use LIMIT 1)
- "most frequently" = LIMIT 1, NOT LIMIT 5!

RULE 5 - RETURN FORMAT - MATCH GOLD QUERY EXACTLY (CRITICAL - 25% of errors):
| Question Pattern | Return Format |
|------------------|---------------|
| "Which users are amplified by 'Me'" | RETURN user.screen_name AS AmplifiedUser (ONLY this column!) |
| "list users" / "who follows" / "most recent users" | RETURN user.screen_name, user.name, user.followers, user.following, user.profile_image_url, user.url, user.location, user.statuses |
| "top N users" with ORDER BY followers | RETURN user.name, user.screen_name, user.followers, user.following |
| "which users" simple | RETURN user.screen_name, user.name |
- DO NOT add extra columns not requested
- MATCH the exact columns from similar examples

RULE 6 - RETURN FORMAT FOR TWEETS (CRITICAL):
- "list tweets" / "show tweets" / "top N tweets" → RETURN t (full node)
- "tweets with highest/most X" → RETURN t.text, t.favorites
- "show tweets where" with specific properties → RETURN t.text AS tweet_text, t.favorites AS favorite_count, t.created_at AS created_at
- "tweets with most mentions" → RETURN t.id_str AS tweet_id, t.text AS tweet_text, mention_count (include id_str!)

RULE 7 - RETWEETS PATTERN (CRITICAL):
- CORRECT: (user)-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)
- WRONG: (user)-[:RETWEETS]->(tweet) ← This relationship does NOT exist!
- To find original author: ...-[:RETWEETS]->(orig)<-[:POSTS]-(author:User)

RULE 8 - ORDER BY:
- "top N" / "most" → ORDER BY ... DESC
- "most recent" → ORDER BY created_at DESC (NOT followers!)
- "earliest" → ORDER BY created_at ASC
- "most frequently" → ORDER BY count_alias DESC

RULE 9 - AGGREGATION:
- "most frequently" → WITH entity, COUNT(*) AS count ORDER BY count DESC LIMIT 1

RULE 10 - count{{}} vs PROPERTY (CRITICAL - 15% of errors):
- "number of people they are following" → count{{(u)-[:FOLLOWS]->(:User)}} AS followingCount
- "number of followers" → count{{(u)<-[:FOLLOWS]-(:User)}} AS followerCount
- NEVER use u.following or u.followers property for counting relationships!
- This is Neo4j 5.x subquery count syntax - MUST use for counting relationships

RULE 11 - RELATIONSHIP DIRECTION:
- (User)-[:FOLLOWS]->(Me) = User follows Me
- (Tweet)-[:MENTIONS]->(User) = Tweet mentions User
- (Tweet)-[:RETWEETS]->(Tweet) = first is retweet of second

RULE 12 - HASHTAG MATCHING:
- Use {{name: 'education'}} NOT {{name: '#education'}}
- Hashtag names stored WITHOUT # symbol

RULE 13 - DISTINCT:
- "all users who..." with JOINs → RETURN DISTINCT
- "first N" → NO DISTINCT

RULE 14 - DATE FILTERING:
- "on 2021-03-16" → WHERE date(t.created_at) = date('2021-03-16')

RULE 15 - ALIAS MATCHING:
- When gold query uses AS alias, match it exactly
- user.screen_name AS AmplifiedUser → use same alias

RULE 16 - 'Me' WITHOUT PROPERTY:
- "Which users are amplified by 'Me'" → MATCH (me:Me)-[:AMPLIFIES]->(user:User) (no screen_name filter)
- Only add {{screen_name: 'neo4j'}} when question explicitly says 'neo4j'

RULE 17 - BETWEENNESS CENTRALITY:
- betweenness is a PROPERTY on User/Me nodes, NOT a relationship
- "highest betweenness" → ORDER BY u.betweenness DESC
- "average betweenness" → avg(u.betweenness)

RULE 18 - FILTERING WITH PROPERTIES:
- "users with more than X followers" → WHERE u.followers > X (use property)
- "users with more than X statuses" → WHERE u.statuses > X (use property)
- "users who follow more than X people" → WHERE count{{(u)-[:FOLLOWS]->(:User)}} > X (count relationships)

RULE 19 - TWEET MENTIONS COUNT (CRITICAL):
- "tweets with most mentions" → MUST include t.id_str in RETURN
- Pattern: MATCH (t:Tweet)-[:MENTIONS]->(u:User) WITH t, COUNT(u) AS mention_count ORDER BY mention_count DESC LIMIT N RETURN t.id_str AS tweet_id, t.text AS tweet_text, mention_count

RULE 20 - RETURN COLUMN ORDER:
- Follow the exact column order from similar examples
- For "top N users by followers": RETURN user.name, user.screen_name, user.followers, user.following (name first!)
- For "users who follow": RETURN user.screen_name, user.name, user.followers, user.following, user.profile_image_url, user.url, user.location, user.statuses

=== FEW-SHOT EXAMPLES ===

### AMPLIFIES - always :Me, ONLY return requested columns ###
Q: Which users are amplified by 'Me' according to the AMPLIFIES relationship?
MATCH (me:Me)-[:AMPLIFIES]->(user:User) RETURN user.screen_name AS AmplifiedUser

Q: Who are the top 3 users that 'Neo4j' has amplified?
MATCH (me:Me {{name: 'Neo4j'}})-[:AMPLIFIES]->(user:User) RETURN user.screen_name, COUNT(*) AS amplification_count ORDER BY amplification_count DESC LIMIT 3

### FOLLOWS - FULL USER PROPERTIES ###
Q: List the 5 most recent users who started following 'Neo4j'.
MATCH (neo4j:Me {{screen_name: 'neo4j'}})<-[:FOLLOWS]-(user:User) RETURN user.screen_name, user.name, user.followers, user.following, user.profile_image_url, user.url, user.location, user.statuses ORDER BY user.followers DESC LIMIT 5

Q: Who are the top 5 users that a specific user named 'Neo4j' follows?
MATCH (me:Me {{name: 'Neo4j'}})-[:FOLLOWS]->(user:User) RETURN user.name, user.screen_name, user.followers, user.following ORDER BY user.followers DESC LIMIT 5

Q: Which users follow 'neo4j' and have more than 10000 followers?
MATCH (me:Me {{screen_name: 'neo4j'}})<-[:FOLLOWS]-(user:User) WHERE user.followers > 10000 RETURN user.screen_name, user.name, user.followers

### count{{}} SYNTAX - CRITICAL (NOT property!) ###
Q: Identify the top 3 users by the number of people they are following.
MATCH (u:User) RETURN u.name, u.screen_name, count{{(u)-[:FOLLOWS]->(:User)}} AS followingCount ORDER BY followingCount DESC LIMIT 3

Q: Which users have the most followers? Top 5.
MATCH (u:User) RETURN u.name, u.screen_name, count{{(u)<-[:FOLLOWS]-(:User)}} AS followerCount ORDER BY followerCount DESC LIMIT 5

### INTERACTS_WITH - LIMIT 1 for "most frequently" ###
Q: Who does 'neo4j' interact with most frequently?
MATCH (me:Me {{screen_name: 'neo4j'}})-[:INTERACTS_WITH]->(user:User) RETURN user.screen_name, COUNT(*) AS interaction_count ORDER BY interaction_count DESC LIMIT 1

### POSTS - use :User ###
Q: What are the top 5 tweets by 'Neo4j' based on favorites count?
MATCH (u:User {{name: 'Neo4j'}})-[:POSTS]->(t:Tweet) RETURN t ORDER BY t.favorites DESC LIMIT 5

Q: List all tweets by 'neo4j' that have more than 200 favorites. First 5.
MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet) WHERE t.favorites > 200 RETURN t LIMIT 5

### POSTS with TAGS ###
Q: Find all tweets posted by 'Neo4j' containing a hashtag.
MATCH (u:User {{name: 'Neo4j'}})-[:POSTS]->(t:Tweet)-[:TAGS]->(h:Hashtag) RETURN t, h

Q: Which tweets by 'neo4j' contain the hashtag 'education'?
MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet)-[:TAGS]->(h:Hashtag {{name: 'education'}}) RETURN t

### MENTIONS ###
Q: Show the tweets where 'neo4j' is mentioned and the tweet has a favorite count over 100.
MATCH (t:Tweet)-[:MENTIONS]->(u:User {{screen_name: 'neo4j'}}) WHERE t.favorites > 100 RETURN t.text AS tweet_text, t.favorites AS favorite_count, t.created_at AS created_at

Q: Who does 'neo4j' mention most frequently?
MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet)-[:MENTIONS]->(mentioned:User) RETURN mentioned.screen_name, count(t) AS mentions_count ORDER BY mentions_count DESC

Q: Who are the users that 'neo4j' mentions most frequently in their tweets?
MATCH (u:User {{screen_name: 'neo4j'}})-[:POSTS]->(t:Tweet)-[:MENTIONS]->(mentioned:User) RETURN mentioned.screen_name, count(t) AS mentions_count ORDER BY mentions_count DESC

### RETWEETS - MUST use POSTS->RETWEETS ###
Q: Who are the top 5 users that 'neo4j' retweets the most?
MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)-[:RETWEETS]->(retweetedTweet:Tweet)<-[:POSTS]-(retweetedUser:User) RETURN retweetedUser.screen_name AS retweeted_user, count(*) AS retweet_count ORDER BY retweet_count DESC LIMIT 5

Q: Show the first 3 tweets that 'Me' has retweeted.
MATCH (me:Me)-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet) RETURN original ORDER BY original.created_at ASC LIMIT 3

Q: Identify the URLs of the top 5 tweets retweeted by 'Neo4j'.
MATCH (me:Me {{name: 'Neo4j'}})-[:POSTS]->(retweet:Tweet)-[:RETWEETS]->(original:Tweet)-[:CONTAINS]->(link:Link) RETURN link.url ORDER BY original.favorites DESC LIMIT 5

Q: Who are the users that have been retweeted by 'neo4j'?
MATCH (me:Me {{screen_name: 'neo4j'}})-[:POSTS]->(tweet:Tweet)-[:RETWEETS]->(retweetedTweet:Tweet)<-[:POSTS]-(retweetedUser:User) RETURN DISTINCT retweetedUser.screen_name

### SIMILAR_TO - always :Me ###
Q: Which 5 users are most similar to Neo4j?
MATCH (me:Me {{name: 'Neo4j'}})<-[s:SIMILAR_TO]-(u:User) RETURN u.screen_name AS user, s.score AS similarity ORDER BY similarity DESC LIMIT 5

### BETWEENNESS CENTRALITY ###
Q: Which three users have the highest 'betweenness' metric in the network?
MATCH (u:User) WHERE u.betweenness IS NOT NULL RETURN u.screen_name, u.name, u.betweenness ORDER BY u.betweenness DESC LIMIT 3

Q: Who are the top 3 followers of 'Neo4j' based on betweenness centrality?
MATCH (me:Me {{name: 'Neo4j'}})<-[:FOLLOWS]-(u:User) WHERE u.betweenness IS NOT NULL RETURN u.screen_name, u.name, u.betweenness ORDER BY u.betweenness DESC LIMIT 3

Q: What is the average betweenness centrality of users who follow Neo4j?
MATCH (me:Me {{name: 'Neo4j'}})<-[:FOLLOWS]-(u:User) WHERE u.betweenness IS NOT NULL RETURN avg(u.betweenness) AS average_betweenness

### GENERAL QUERIES ###
Q: List the first 5 tweets with the highest number of favorites.
MATCH (t:Tweet) RETURN t.text, t.favorites ORDER BY t.favorites DESC LIMIT 5

Q: What are the top 5 most recent tweets?
MATCH (t:Tweet) RETURN t ORDER BY t.created_at DESC LIMIT 5

Q: List all users who follow 'neo4j'.
MATCH (u:User)-[:FOLLOWS]->(:Me {{screen_name: 'neo4j'}}) RETURN u

Q: What are the top 5 users with the highest number of followers?
MATCH (u:User) WHERE u.followers IS NOT NULL RETURN u.screen_name, u.name, u.followers ORDER BY u.followers DESC LIMIT 5

Q: Which users have more than 10000 followers and less than 15000 statuses?
MATCH (u:User) WHERE u.followers > 10000 AND u.statuses < 15000 RETURN u.screen_name, u.name, u.followers, u.statuses

### HASHTAG QUERIES ###
Q: List the first 3 hashtags used in tweets mentioning 'Neo4j'.
MATCH (t:Tweet)-[:MENTIONS]->(u:User {{name: 'Neo4j'}}) MATCH (t)-[:TAGS]->(h:Hashtag) RETURN h.name AS hashtag LIMIT 3

Q: List the first 3 tweets containing hashtag 'education'.
MATCH (t:Tweet)-[:TAGS]->(h:Hashtag {{name: 'education'}}) RETURN t LIMIT 3

Q: Identify the top 3 hashtags used in tweets.
MATCH (t:Tweet)-[:TAGS]->(h:Hashtag) RETURN h.name AS hashtag, COUNT(*) AS usage_count ORDER BY usage_count DESC LIMIT 3

### LOCATION QUERIES ###
Q: List the top 3 tweets from users located in 'Graphs Are Everywhere'.
MATCH (u:User {{location: 'Graphs Are Everywhere'}})-[:POSTS]->(t:Tweet) RETURN t ORDER BY t.favorites DESC LIMIT 3

### FIRST N USERS WHO MENTIONED ###
Q: Identify the first 3 users who mentioned 'Neo4j' in their tweets.
MATCH (u:User)-[:POSTS]->(t:Tweet)-[:MENTIONS]->(mentioned:User {{name: 'Neo4j'}}) RETURN DISTINCT u.screen_name, u.name, t.created_at ORDER BY t.created_at ASC LIMIT 3

### TWEET MENTIONS COUNT - CRITICAL (include id_str!) ###
Q: Which three tweets have the most mentions of other users?
MATCH (t:Tweet)-[:MENTIONS]->(u:User) WITH t, COUNT(u) AS mention_count ORDER BY mention_count DESC LIMIT 3 RETURN t.id_str AS tweet_id, t.text AS tweet_text, mention_count

Q: Find the top 5 tweets with the highest number of user mentions.
MATCH (t:Tweet)-[:MENTIONS]->(u:User) WITH t, COUNT(u) AS mention_count ORDER BY mention_count DESC LIMIT 5 RETURN t.id_str AS tweet_id, t.text AS tweet_text, mention_count

### INTERACTS_WITH - LIMIT 1 for "most frequently" ###
Q: Who does 'neo4j' interact with most frequently?
MATCH (me:Me {{screen_name: 'neo4j'}})-[:INTERACTS_WITH]->(user:User) RETURN user.screen_name, COUNT(*) AS interaction_count ORDER BY interaction_count DESC LIMIT 1

Q: Which user does 'Neo4j' interact with the most?
MATCH (me:Me {{name: 'Neo4j'}})-[:INTERACTS_WITH]->(user:User) RETURN user.screen_name, COUNT(*) AS interaction_count ORDER BY interaction_count DESC LIMIT 1

### TOP N USERS BY FOLLOWERS - exact column format ###
Q: Who are the top 5 users that a specific user named 'Neo4j' follows?
MATCH (me:Me {{name: 'Neo4j'}})-[:FOLLOWS]->(user:User) RETURN user.name, user.screen_name, user.followers, user.following ORDER BY user.followers DESC LIMIT 5

Q: List the top 3 users followed by 'neo4j' with the most followers.
MATCH (me:Me {{screen_name: 'neo4j'}})-[:FOLLOWS]->(user:User) RETURN user.name, user.screen_name, user.followers, user.following ORDER BY user.followers DESC LIMIT 3

{{question}}
"""


# =============================================================================
# TEMPLATE SELECTOR
# =============================================================================
_TEMPLATE_MAP = {
    "climate": CYPHER_GENERATION_CLIMATE_TEMPLATE,
    "movies": CYPHER_GENERATION_MOVIES_TEMPLATE,
    "recommendations": CYPHER_GENERATION_RECOMMENDATIONS_TEMPLATE,
    "northwind": CYPHER_GENERATION_NORTHWIND_TEMPLATE,
    "twitter": CYPHER_GENERATION_TWITTER_TEMPLATE,
}


def get_cypher_template(db_name: str | None = None) -> str:
    """Get the Cypher generation template for the given database."""
    db = db_name or get_settings().database_name
    return _TEMPLATE_MAP.get(db, CYPHER_GENERATION_CLIMATE_TEMPLATE)
