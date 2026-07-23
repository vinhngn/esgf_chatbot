"""
Semantic Schema definitions per database type.
This replaces simple natural language with exact Cypher-style property structures,
which dramatically improves LLM understanding of property locations.
"""
from config import get_settings

entity_climate_definitions = """
=== SEMANTIC SCHEMA ===
Node: Source {name: STRING}
Node: RCM {name: STRING}
Node: Variable {name: STRING, cf_standard_name: STRING}
Node: Experiment {name: STRING}
Node: Institute {name: STRING}
Node: SourceComponent {name: STRING}
Node: SourceType {name: STRING}
Node: Realm {name: STRING}
Node: Frequency {name: STRING}
Node: Resolution {name: STRING}
Node: Country {name: STRING, code: STRING}
Node: Country_Subdivision {name: STRING, code: STRING}
Node: Continent {name: STRING}
Relationship: (Source)-[:PRODUCES_VARIABLE]->(Variable)
Relationship: (Source)-[:USED_IN_EXPERIMENT]->(Experiment)
Relationship: (Source)-[:PRODUCED_BY_INSTITUTE]->(Institute)
Relationship: (Source)-[:HAS_SOURCE_COMPONENT]->(SourceComponent)
Relationship: (Source)-[:IS_OF_TYPE]->(SourceType)
Relationship: (Source)-[:APPLIES_TO_REALM]->(Realm)
Relationship: (Source)-[:HAS_FREQUENCY]->(Frequency)
Relationship: (Source)-[:HAS_RESOLUTION]->(Resolution)
Relationship: (RCM)-[:DRIVEN_BY_SOURCE]->(Source)
Relationship: (RCM)-[:COVERS_REGION]->(Country|Country_Subdivision|Continent)
Relationship: (Country_Subdivision)-[:PART_OF]->(Country)
""".strip()

entity_movies_definitions = """
=== SEMANTIC SCHEMA ===
Node: Person {name: STRING, born: INTEGER}
Node: Movie {title: STRING, released: INTEGER, votes: INTEGER, tagline: STRING}
Relationship: (Person)-[:ACTED_IN {roles: LIST<STRING>}]->(Movie)
Relationship: (Person)-[:REVIEWED {rating: INTEGER, summary: STRING}]->(Movie)
Relationship: (Person)-[:DIRECTED]->(Movie)
Relationship: (Person)-[:PRODUCED]->(Movie)
Relationship: (Person)-[:WROTE]->(Movie)
Relationship: (Person)-[:FOLLOWS]->(Person)
""".strip()

entity_recommendations_definitions = """
=== SEMANTIC SCHEMA ===
Node: Movie {title: STRING, year: INTEGER, released: STRING, runtime: INTEGER, budget: INTEGER, revenue: INTEGER, imdbRating: FLOAT, imdbVotes: INTEGER, plot: STRING, languages: LIST<STRING>, countries: LIST<STRING>, poster: STRING}
Node: User {userId: STRING, name: STRING}
Node: Genre {name: STRING}
Node: Actor {name: STRING, born: DATE, died: DATE, bornIn: STRING, tmdbId: STRING, imdbId: STRING, bio: STRING}
Node: Director {name: STRING, born: DATE, died: DATE, bornIn: STRING, tmdbId: STRING, imdbId: STRING, bio: STRING}
Node: Person {name: STRING, born: DATE, died: DATE, bornIn: STRING, tmdbId: STRING, imdbId: STRING, bio: STRING}
Relationship: (Actor)-[:ACTED_IN]->(Movie)
Relationship: (Director)-[:DIRECTED]->(Movie)
Relationship: (Person)-[:ACTED_IN|:DIRECTED]->(Movie)
Relationship: (Movie)-[:IN_GENRE]->(Genre)
Relationship: (User)-[:RATED {rating: FLOAT, timestamp: INTEGER}]->(Movie)
""".strip()

entity_northwind_definitions = """
=== SEMANTIC SCHEMA ===
Node: Product {productID: STRING, productName: STRING, unitPrice: FLOAT, unitsInStock: INTEGER, unitsOnOrder: INTEGER, reorderLevel: INTEGER, discontinued: BOOLEAN}
Node: Category {categoryID: STRING, categoryName: STRING, description: STRING}
Node: Supplier {supplierID: STRING, companyName: STRING, contactName: STRING, contactTitle: STRING, address: STRING, city: STRING, region: STRING, postalCode: STRING, country: STRING, phone: STRING, fax: STRING, homePage: STRING}
Node: Customer {customerID: STRING, companyName: STRING, contactName: STRING, contactTitle: STRING, address: STRING, city: STRING, region: STRING, postalCode: STRING, country: STRING, phone: STRING, fax: STRING}
Node: Order {orderID: STRING, orderDate: STRING, requiredDate: STRING, shippedDate: STRING, shipVia: STRING, freight: FLOAT, shipName: STRING, shipAddress: STRING, shipCity: STRING, shipRegion: STRING, shipPostalCode: STRING, shipCountry: STRING}
Relationship: (Product)-[:PART_OF]->(Category)
Relationship: (Supplier)-[:SUPPLIES]->(Product)
Relationship: (Customer)-[:PURCHASED]->(Order)
Relationship: (Order)-[:ORDERS {unitPrice: FLOAT, quantity: INTEGER, discount: FLOAT}]->(Product)
""".strip()

entity_twitter_definitions = """
=== SEMANTIC SCHEMA ===
Node: User {screen_name: STRING, name: STRING, location: STRING, followers: INTEGER, following: INTEGER, statuses: INTEGER, betweenness: FLOAT, profile_image_url: STRING, url: STRING}
Node: Me {screen_name: STRING, name: STRING, location: STRING, followers: INTEGER, following: INTEGER, statuses: INTEGER, betweenness: FLOAT, profile_image_url: STRING, url: STRING}
Node: Tweet {id_str: STRING, text: STRING, created_at: STRING, favorites: INTEGER}
Node: Hashtag {name: STRING}
Node: Link {url: STRING}
Node: Source {name: STRING}
Relationship: (User|Me)-[:FOLLOWS]->(User|Me)
Relationship: (User|Me)-[:POSTS]->(Tweet)
Relationship: (Me)-[:AMPLIFIES]->(User)
Relationship: (Me)-[:INTERACTS_WITH]->(User)
Relationship: (Me)-[:SIMILAR_TO {score: FLOAT}]->(User)
Relationship: (Me)-[:RT_MENTIONS]->(User)
Relationship: (Tweet)-[:MENTIONS]->(User|Me)
Relationship: (Tweet)-[:RETWEETS]->(Tweet)
Relationship: (Tweet)-[:TAGS]->(Hashtag)
Relationship: (Tweet)-[:CONTAINS]->(Link)
Relationship: (Tweet)-[:USING]->(Source)
Relationship: (Tweet)-[:REPLY_TO]->(Tweet)
""".strip()


_DEFINITIONS_MAP = {
    "climate": entity_climate_definitions,
    "movies": entity_movies_definitions,
    "recommendations": entity_recommendations_definitions,
    "northwind": entity_northwind_definitions,
    "twitter": entity_twitter_definitions,
}


def get_entity_definitions(db_name: str | None = None) -> str:
    """Get entity definitions for the given database (or current config)."""
    db = db_name or get_settings().profile_database_name
    return _DEFINITIONS_MAP.get(db, entity_climate_definitions)


# Module-level convenience (uses current config)
entity_definitions = get_entity_definitions()
