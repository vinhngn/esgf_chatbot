# Pipeline Trace Samples

This file records 10 text-to-Cypher calls per database with pipeline trace IDs.
Each trace shows how the user question becomes a grounded schema context, then a final Cypher query.

Important: the model's hidden reasoning is not available through the API. The trace records prompts, structured grounding output, validation decisions, final Cypher, and execution summaries.

## movies

### 1. Find the top 5 movies with the most votes.

- status: OK
- trace_id: `0e9f96780a`
- rows_returned: 5
- cypher: `MATCH (m:Movie) WHERE m.votes IS NOT NULL RETURN m.title, m.votes ORDER BY m.votes DESC LIMIT 5`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 2. List all movies with a 'Pretty funny at times' review summary.

- status: OK
- trace_id: `136b4088f3`
- rows_returned: 1
- cypher: `MATCH (m:Movie)<-[r:REVIEWED]-(p:Person) WHERE r.summary = 'Pretty funny at times' RETURN m.title`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 3. List the movies with more than 100 votes.

- status: OK
- trace_id: `9e849879c7`
- rows_returned: 15
- cypher: `MATCH (m:Movie) WHERE m.votes > 100 RETURN m.title`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 4. Who are the top 3 producers by the number of movies with different taglines?

- status: OK
- trace_id: `8f1b1c582c`
- rows_returned: 3
- cypher: `MATCH (p:Person)-[:PRODUCED]->(m:Movie) WHERE m.tagline IS NOT NULL WITH p, count(DISTINCT m.tagline) AS distinctTaglines ORDER BY distinctTaglines DESC LIMIT 3 RETURN p.name, distinctTaglines`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 5. Which people have directed movies with a budget higher than $100 million?

- status: OK
- trace_id: `e454d0273d`
- rows_returned: 0
- cypher: `MATCH (p:Person)-[:DIRECTED]->(m:Movie) WHERE m.budget > 100000000 RETURN p.name, m.title`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 6. What are the roles of Keanu Reeves in 'The Matrix'?

- status: OK
- trace_id: `86313b2017`
- rows_returned: 1
- cypher: `MATCH (p:Person)-[r:ACTED_IN]->(m:Movie) WHERE p.name = 'Keanu Reeves' AND m.title = 'The Matrix' RETURN r.roles AS roles`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 7. List all people who have written and directed the same movie.

- status: OK
- trace_id: `6d0ec869bf`
- rows_returned: 4
- cypher: `MATCH (p:Person)-[:WROTE]->(m:Movie) WHERE (p)-[:DIRECTED]->(m) RETURN DISTINCT p.name`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 8. Which top 5 people have directed movies with more than 200 votes?

- status: OK
- trace_id: `0d4500a2c3`
- rows_returned: 5
- cypher: `MATCH (p:Person)-[:DIRECTED]->(m:Movie) WHERE m.votes > 200 WITH p, count(m) AS num_movies ORDER BY num_movies DESC LIMIT 5 RETURN p.name AS director`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 9. List the names of people who acted in movies directed by Nancy Meyers.

- status: OK
- trace_id: `2c50ab7685`
- rows_returned: 3
- cypher: `MATCH (p:Person)-[:ACTED_IN]->(m:Movie)<-[:DIRECTED]-(d:Person {name: 'Nancy Meyers'}) RETURN DISTINCT p.name`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 10. What are the top 3 highest rated reviews and which movies they are associated with?

- status: OK
- trace_id: `1cc258b039`
- rows_returned: 3
- cypher: `MATCH (p:Person)-[r:REVIEWED]->(m:Movie) RETURN m.title, r.rating ORDER BY r.rating DESC LIMIT 3`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

## northwind

### 1. Identify the 5 suppliers with the highest average unit price of products supplied.

- status: OK
- trace_id: `6e0d241cbc`
- rows_returned: 5
- cypher: `MATCH (s:Supplier)-[:SUPPLIES]->(p:Product) WITH s, avg(p.unitPrice) AS avgUnitPrice ORDER BY avgUnitPrice DESC LIMIT 5 RETURN s.companyName AS Supplier, avgUnitPrice AS AverageUnitPrice`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 2. Find the `customerID` and `shipCity` for the customer with the highest total order value (sum of `unitPrice` * `quantity` for all their orders).

- status: OK
- trace_id: `5ccb71428a`
- rows_returned: 1
- cypher: `MATCH (c:Customer)-[:PURCHASED]->(o:Order)-[orders:ORDERS]->(p:Product) WITH c, sum(toFloat(orders.unitPrice) * orders.quantity) AS totalOrderValue, o.shipCity AS shipCity WITH c, totalOrderValue, shipCity ORDER BY totalOrderValue DESC LIMIT 1 RETURN c.customerID, shipCity`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 3. What is the average `unitPrice` of products that have been ordered in quantities greater than 10?

- status: OK
- trace_id: `9161646ecb`
- rows_returned: 1
- cypher: `MATCH (o:Order)-[rel:ORDERS]->(p:Product) WHERE rel.quantity > 10 WITH avg(toFloat(rel.unitPrice)) AS avgPrice RETURN avgPrice`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 4. Which 3 customers have ordered the most distinct products?

- status: OK
- trace_id: `37c1d76b69`
- rows_returned: 3
- cypher: `MATCH (c:Customer)-[:PURCHASED]->(o:Order)-[:ORDERS]->(p:Product) WITH c, COUNT(DISTINCT p.productID) AS distinctProducts ORDER BY distinctProducts DESC LIMIT 3 RETURN c.companyName`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 5. What is the average discount applied to orders shipped to 'Denmark'?

- status: OK
- trace_id: `456687c1f7`
- rows_returned: 1
- cypher: `MATCH (o:Order {shipCountry: 'Denmark'})-[rel:ORDERS]->(p:Product) WITH avg(toFloat(rel.discount)) AS averageDiscount RETURN averageDiscount`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 6. What is the average unit price of products ordered by customers in the 'USA'?

- status: OK
- trace_id: `cd65d3ff0d`
- rows_returned: 1
- cypher: `MATCH (c:Customer {country: 'USA'})-[:PURCHASED]->(o:Order)-[rel:ORDERS]->(p:Product) WITH avg(toFloat(rel.unitPrice)) AS avgUnitPrice RETURN avgUnitPrice`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 7. Which suppliers supply products with a unitsInStock value above 80?

- status: OK
- trace_id: `1ccab1f1b8`
- rows_returned: 13
- cypher: `MATCH (s:Supplier)-[:SUPPLIES]->(p:Product) WHERE p.unitsInStock > 80 RETURN s.companyName, p.unitsInStock`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 8. Which categories have products with a unit price less than $10?

- status: OK
- trace_id: `10c8651ad2`
- rows_returned: 6
- cypher: `MATCH (p:Product)-[:PART_OF]->(c:Category) WHERE p.unitPrice < 10 RETURN DISTINCT c.categoryName`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 9. List the 3 suppliers who supply the most products that have been discontinued.

- status: OK
- trace_id: `9dd749eb04`
- rows_returned: 3
- cypher: `MATCH (s:Supplier)-[:SUPPLIES]->(p:Product) WHERE p.discontinued = true WITH s, COUNT(p) AS discontinuedCount ORDER BY discontinuedCount DESC LIMIT 3 RETURN s.companyName`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 10. Which customers (show `customerID` and `companyName`) have placed orders that include the top 3 most expensive products?

- status: OK
- trace_id: `0ec3586841`
- rows_returned: 3
- cypher: `MATCH (c:Customer)-[:PURCHASED]->(o:Order)-[orders:ORDERS]->(p:Product) WITH p, c ORDER BY orders.unitPrice DESC LIMIT 3 WITH DISTINCT c RETURN c.customerID, c.companyName`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

## recommendations

### 1. List the first 3 movies that have been directed by actors.

- status: OK
- trace_id: `da0e3a27d1`
- rows_returned: 3
- cypher: `MATCH (p:Actor)-[:DIRECTED]->(m:Movie)<-[:ACTED_IN]-(p) RETURN m.title LIMIT 3`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 2. List the movies released in the year the user "Omar Huffman" was born.

- status: OK
- trace_id: `e17ffe59f9`
- rows_returned: 0
- cypher: `MATCH (u:User {name: "Omar Huffman"})-[:RATED]->(m:Movie) WHERE m.released CONTAINS u.born RETURN m.released, m.title`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 3. 00e4 of the Valley of the Wind (Kaze no tani no Naushika)""}

- status: OK
- trace_id: `a5bba3fbf7`
- rows_returned: 0
- cypher: `MATCH (m:Movie {title: "00e4 of the Valley of the Wind (Kaze no tani no Naushika)"}) RETURN m`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 4. List the first 5 directors who have directed a movie with an imdbRating of 9 or higher.

- status: OK
- trace_id: `f2e25c81c6`
- rows_returned: 3
- cypher: `MATCH (d:Director)-[:DIRECTED]->(m:Movie) WHERE m.imdbRating >= 9 RETURN DISTINCT d.name LIMIT 5`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 5. Which three directors have directed movies in more than one language?

- status: OK
- trace_id: `2f38992d93`
- rows_returned: 3
- cypher: `MATCH (d:Director)-[:DIRECTED]->(m:Movie) WITH d, collect(DISTINCT m.languages) AS languages WHERE size(languages) > 1 RETURN d.name, languages ORDER BY size(languages) DESC LIMIT 3`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 6. What are the IMDb ratings of movies that have a plot mentioning 'evil exterminator'?

- status: OK
- trace_id: `be0d51fff9`
- rows_returned: 1
- cypher: `MATCH (m:Movie) WHERE m.plot CONTAINS 'evil exterminator' RETURN m.imdbRating`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 7. List the top 3 movies with the highest budget to revenue ratio.

- status: OK
- trace_id: `d5d8656841`
- rows_returned: 3
- cypher: `MATCH (m:Movie) WHERE m.budget IS NOT NULL AND m.revenue IS NOT NULL AND m.revenue > 0 RETURN m.budget, m.revenue, (toFloat(m.budget) / m.revenue) AS budgetRevenueRatio ORDER BY budgetRevenueRatio DESC LIMIT 3`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 8. What is the average budget for movies in the "Science Fiction" genre?

- status: OK
- trace_id: `db2b71a0da`
- rows_returned: 1
- cypher: `MATCH (m:Movie)-[:IN_GENRE]->(g:Genre {name: 'Science Fiction'}) RETURN avg(m.budget)`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 9. List the top 3 movies with the most revenue that have a runtime under 90 minutes.

- status: OK
- trace_id: `305b5c1c13`
- rows_returned: 3
- cypher: `MATCH (m:Movie) WHERE m.runtime < 90 AND m.revenue IS NOT NULL AND m.revenue > 0 RETURN m.revenue ORDER BY m.revenue DESC LIMIT 3`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 10. List the top 5 users who have given the highest average ratings to movies.

- status: OK
- trace_id: `01c894abaa`
- rows_returned: 5
- cypher: `MATCH (u:User)-[r:RATED]->(m:Movie) RETURN u.name, AVG(r.rating) AS averageRating ORDER BY averageRating DESC LIMIT 5`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

## twitter

### 1. List the 5 most recent users who started following 'Neo4j'.

- status: OK
- trace_id: `9173cf396e`
- rows_returned: 5
- cypher: `MATCH (neo4j:Me {screen_name: 'neo4j'})<-[:FOLLOWS]-(user:User) RETURN user.name, user.screen_name ORDER BY user.followers DESC LIMIT 5`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 2. Which users are amplified by 'Me' according to the AMPLIFIES relationship?

- status: OK
- trace_id: `ae8a2a2cc3`
- rows_returned: 144
- cypher: `MATCH (me:Me)-[:AMPLIFIES]->(user:User) RETURN user.name`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 3. Who are the top 5 users that a specific user named 'Neo4j' follows?

- status: OK
- trace_id: `8e137227dd`
- rows_returned: 5
- cypher: `MATCH (me:Me {name: 'Neo4j'})-[:FOLLOWS]->(user:User) RETURN user.screen_name ORDER BY user.followers DESC LIMIT 5`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 4. Identify the top 3 users by the number of people they are following.

- status: OK
- trace_id: `7ec03b0f35`
- rows_returned: 3
- cypher: `MATCH (u:User) RETURN u.following ORDER BY u.following DESC LIMIT 3`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 5. Who does 'neo4j' interact with most frequently?

- status: OK
- trace_id: `1b9eaf2db9`
- rows_returned: 1
- cypher: `MATCH (me:Me {screen_name: 'neo4j'})-[:INTERACTS_WITH]->(user:User) RETURN user.screen_name, COUNT(*) AS interaction_count ORDER BY interaction_count DESC LIMIT 1`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 6. Show the tweets where 'neo4j' is mentioned and the tweet has a favorite count over 100.

- status: OK
- trace_id: `53f90dd66b`
- rows_returned: 1
- cypher: `MATCH (t:Tweet)-[:MENTIONS]->(u:User {screen_name: 'neo4j'}) WHERE t.favorites > 100 RETURN t.text AS tweet_text, t.favorites AS favorite_count`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 7. List the first 5 tweets with the highest number of favorites.

- status: OK
- trace_id: `74f88beeb9`
- rows_returned: 5
- cypher: `MATCH (t:Tweet) RETURN t ORDER BY t.favorites DESC LIMIT 5`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 8. Who are the users that 'neo4j' mentions most frequently in their tweets?

- status: OK
- trace_id: `208bb0d869`
- rows_returned: 5
- cypher: `MATCH (t:Tweet)-[:MENTIONS]->(u:User) RETURN u.name, u.screen_name, COUNT(t) AS mention_count ORDER BY mention_count DESC LIMIT 5`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 9. List the tweets that mention users who have retweeted tweets that mention "Neo4j".

- status: OK
- trace_id: `57f5367c93`
- rows_returned: 0
- cypher: `MATCH (t:Tweet)-[:MENTIONS]->(u:User)<-[:RETWEETS]-(r:Tweet) WHERE t.text CONTAINS 'Neo4j' RETURN t`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`

### 10. Find all tweets posted by 'Neo4j' containing a hashtag.

- status: OK
- trace_id: `b057d98ec4`
- rows_returned: 1095
- cypher: `MATCH (u:User {name: 'Neo4j'})-[:POSTS]->(t:Tweet)-[:TAGS]->(h:Hashtag) RETURN t, h`
- stage_flow: `T2C-01 -> CHAIN-01 -> CHAIN-02 -> GROUND-01 -> GROUND-02 -> GROUND-03 -> GROUND-04 -> GROUND-05 -> GROUND-06 -> CHAIN-03 -> CHAIN-04 -> CHAIN-05 -> CHAIN-06 -> CHAIN-07 -> CHAIN-08 -> CHAIN-09`
