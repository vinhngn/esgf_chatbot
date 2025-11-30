entity_climate_definitions = """
The definitions of the entity types are given below:
Activity: A coordinated modeling effort or scientific campaign.
ExperimentFamily: A group of related experiments sharing a common scientific goal.
Experiment: A specific simulation scenario (e.g., historical, ssp585).
SubExperiment: A variant or subset of an experiment, usually with specific configurations.
Source: A climate model or system used to generate data (e.g., GFDL-ESM4).
SourceType: The classification of the model
SourceComponent: A component of a climate model.
PhysicalScheme: A physical process representation used in a model (e.g., cloud scheme).
PhysicalFeature: Unique physical characteristics of a model (e.g., terrain-following grid).
SimulationType: Type of simulation performed (e.g., transient, equilibrium).
Metric: Quantitative measure of model performance (e.g., climate sensitivity).
Project: A broader initiative under which models/experiments are conducted (e.g., CMIP6).
Institute: Organization responsible for developing models or running simulations.
Variable: Scientific quantities output by models (e.g., temperature, precipitation).
Realm: Component of the Earth system (e.g., atmosphere, ocean).
Frequency: Temporal resolution of model output (e.g., daily, monthly).
Resolution: Spatial resolution or grid size of the data.
Ensemble: A collection of model runs differing in initial conditions or configurations.
Member: An individual member of an ensemble.
MIPEra: A major generation or version of coordinated experiments (e.g., CMIP5, CMIP6).
RCM (Regional Climate Model): A model used for fine-resolution regional simulations.
Domain: Geographical coverage of a model.
Continent: A large continuous landmass (e.g., Asia, Africa).
Country: A sovereign state or territory (e.g., India, USA).
Country Subdivision: Administrative units within countries (e.g., California).
City: Urban locality (e.g., Paris).
No Country Region: Areas not under country jurisdiction (e.g., open ocean).
Water Bodies: Oceans, seas, and lakes (e.g., Pacific Ocean).
Instrument: Device used to observe environmental variables (e.g., radiometer).
Platform: Physical carrier for an instrument (e.g., satellite).
Weather event: Specific events like storms, droughts.
Teleconnection: Large-scale climate patterns (e.g., ENSO, NAO).
Ocean circulation: Movements of ocean waters (e.g., AMOC).
Natural hazard: Geophysical events impacting systems (e.g., tsunami, earthquake).
""".strip()

entity_movies_definitions = """
The definitions of the entity types are given below:
Movie: Represents a film with attributes such as title, release year, tagline, and number of votes.
Person: Represents an individual (actor, director, writer, or producer) with a name and birth year.
""".strip()

entity_recommendations_definitions = """
The definitions of the entity types are given below:
Movie: Represents a film with attributes such as title, release year, runtime, budget, revenue, rating, and metadata like IMDb and TMDB identifiers. Includes descriptive fields such as plot, languages, countries, and poster or embedding vectors for recommendation tasks.
Genre: Represents a movie genre or category (e.g., Action, Comedy, Drama) associated with one or more movies.
User: Represents a user who has rated movies in the system, identified by a unique userId and optionally a display name.
Actor: Represents a person who performed in one or more movies. Contains biographical details such as name, birth and death dates, birthplace, and links to IMDb or TMDB profiles.
Director: Represents a person who directed one or more movies. Includes similar attributes to Actor, such as name, biography, birth and death information, and media profile links.
Person: Represents a generic individual involved in the film industry (e.g., actor, director, or other contributor) with profile details such as name, birth information, IMDb/TMDB identifiers, and biography.
""".strip()

entity_northwind_definitions = """
The definitions of the entity types are given below:
Product: Represents an item available for sale, including details such as product name, quantity per unit, units in stock or on order, unit price, reorder level, and whether it is discontinued.
Category: Represents a grouping or classification of products (e.g., Beverages, Condiments), including a category name, description, and associated image or picture.
Supplier: Represents a company or individual providing products, including supplier ID, company and contact information, address, region, and communication details such as phone, fax, and homepage.
Customer: Represents an organization or person that places orders, including company and contact information, address, region, and phone or fax details.
Order: Represents a purchase transaction made by a customer, containing information about order date, shipment details, freight cost, and associated customer and employee.
Relationships:
- PART_OF: Connects a Product to its Category.
- SUPPLIES: Connects a Supplier to the Products they supply.
- PURCHASED: Connects a Customer to an Order they have placed.
- ORDERS: Connects an Order to the Products it contains, with relationship properties such as order ID, product ID, quantity, unit price, and discount.
""".strip()

entity_twitter_definitions = """
The definitions of the entity types are given below:
User: Represents a Twitter account other than yourself. Includes attributes such as name, screen name (handle), URL, location, profile image, and network metrics such as number of followers, following, statuses (tweets), and betweenness centrality.
Me: Represents your own Twitter account in the graph. Includes the same properties as User, such as name, screen name, URL, location, profile image, and social metrics like followers, following, and betweenness.
Tweet: Represents an individual post on Twitter, including attributes such as tweet ID, creation date and time, text content, number of favorites (likes), and import method (e.g., API or manual import).
Hashtag: Represents a hashtag (keyword) used in tweets to categorize content or topics.
Link: Represents a hyperlink or URL contained in a tweet, typically referencing an external resource.
Source: Represents the platform, client, or app used to post a tweet (e.g., Twitter Web App, iPhone, Android).
Relationships:
- FOLLOWS: Connects a User or Me to another User or Me to indicate following behavior.
- POSTS: Connects a User or Me to a Tweet they have authored.
- INTERACTS_WITH: Indicates engagement between users (e.g., likes, replies, mentions, retweets).
- SIMILAR_TO: Connects users based on similarity metrics such as mutual interests or follower overlap, with a score property.
- RT_MENTIONS: Represents when Me retweets or mentions another User.
- AMPLIFIES: Represents amplification behavior, such as retweeting or boosting content from another user.
- MENTIONS: Connects a Tweet to the User or Me it mentions.
- USING: Connects a Tweet to the Source used to publish it.
- TAGS: Connects a Tweet to a Hashtag it contains.
- CONTAINS: Connects a Tweet to a Link it includes.
- RETWEETS: Connects a Tweet to another Tweet it retweets.
- REPLY_TO: Connects a Tweet to another Tweet it replies to.
""".strip()


"""
import tomllib

with open(".streamlit/secrets.toml", "rb") as f:
    db = tomllib.load(f)["NEO4J_DATABASE"].lower()



if db == "climate":
    entity_definitions = entity_climate_definitions
elif db == "movies":
    entity_definitions = entity_movies_definitions
elif db == "recommendations":
    entity_definitions = entity_recommendations_definitions
elif db == "northwind":
    entity_definitions = entity_northwind_definitions
elif db == "twitter":
    entity_definitions = entity_twitter_definitions

"""