#DB: climate model
match_climate_properties_map = {
        "Experiment": ["name", "experiment_title", "names"],
        "SubExperiment": ["name", "names"],
        "Activity": ["name", "names"],
        "Realm": ["name", "names"],
        "Country": ["name", "iso", "iso3", "country", "fips"],
        "Project": ["name", "names"],
        "Variable": ["name", "cf_standard_name", "variable_long_name", "names"],
        "Forcing": ["name", "names"],
        "Institute": ["name", "names"],
        "ExperimentFamily": ["name", "names"],
        "Frequency": ["name", "names"],
        "GridLabel": ["name", "names"],
        "Member": ["name", "names"],
        "MIPEra": ["name", "names"],
        "Resolution": ["name", "names"],
        "Source": ["name", "names"],
        "SourceType": ["name", "names"],
        "Ensemble": ["name", "names"],
        "Domain": ["name", "names"],
        "RCM": ["name", "names", "rcm_version"],
        "Continent": ["name", "iso"],
        "Water_Bodies": ["name", "Name"],
        "City": ["name", "asciiname", "alternatenames"],
        "No_Country_Region": ["name", "asciiname", "alternatenames"],
        "Country_Subdivision": ["name", "code", "asciiname"],
        "SourceComponent": ["name"],
    }

#DB: movies
match_movies_properties_map = {
    "Movie": ["title", "tagline", "released", "votes"],
    "Person": ["name", "born"],
}
# DB: recommendations
match_recommendations_properties_map = {
    "Movie": [
        "title",
        "year",
        "released",
        "runtime",
        "budget",
        "revenue",
        "imdbRating",
        "imdbVotes",
        "imdbId",
        "tmdbId",
        "countries",
        "languages",
        "plot",
        "plotEmbedding",
        "posterEmbedding",
        "poster",
        "movieId",
        "url",
    ],
    "Genre": ["name"],
    "User": ["userId", "name"],
    "Actor": [
        "name",
        "bio",
        "born",
        "died",
        "bornIn",
        "imdbId",
        "tmdbId",
        "poster",
        "url",
    ],
    "Director": [
        "name",
        "bio",
        "born",
        "died",
        "bornIn",
        "imdbId",
        "tmdbId",
        "poster",
        "url",
    ],
    "Person": [
        "name",
        "bio",
        "born",
        "died",
        "bornIn",
        "imdbId",
        "tmdbId",
        "poster",
        "url",
    ],
}

# DB: northwind
match_northwind_properties_map = {
    "Product": [
        "productName",
        "quantityPerUnit",
        "unitsOnOrder",
        "supplierID",
        "productID",
        "discontinued",
        "categoryID",
        "reorderLevel",
        "unitsInStock",
        "unitPrice",
    ],
    "Category": [
        "categoryName",
        "categoryID",
        "description",
        "picture",
    ],
    "Supplier": [
        "supplierID",
        "companyName",
        "contactName",
        "contactTitle",
        "address",
        "city",
        "region",
        "postalCode",
        "country",
        "phone",
        "fax",
        "homePage",
    ],
    "Customer": [
        "customerID",
        "companyName",
        "contactName",
        "contactTitle",
        "address",
        "city",
        "region",
        "postalCode",
        "country",
        "phone",
        "fax",
    ],
    "Order": [
        "orderID",
        "orderDate",
        "requiredDate",
        "shippedDate",
        "shipName",
        "shipAddress",
        "shipCity",
        "shipRegion",
        "shipPostalCode",
        "shipCountry",
        "shipVia",
        "employeeID",
        "customerID",
        "freight",
    ],
}

# DB: twitter
match_twitter_properties_map = {
    "User": [
        "name",
        "screen_name",
        "url",
        "location",
        "profile_image_url",
        "followers",
        "following",
        "statuses",
        "betweenness",
    ],
    "Me": [
        "name",
        "screen_name",
        "url",
        "location",
        "profile_image_url",
        "followers",
        "following",
        "betweenness",
    ],
    "Tweet": [
        "id",
        "id_str",
        "text",
        "created_at",
        "favorites",
        "import_method",
    ],
    "Hashtag": ["name"],
    "Link": ["url"],
    "Source": ["name"],
}


import tomllib

with open(".streamlit/secrets.toml", "rb") as f:
    db = tomllib.load(f)["NEO4J_DATABASE"].lower()



if db == "climate":
    match_properties_map = match_climate_properties_map
elif db == "movies":
    match_properties_map = match_movies_properties_map
elif db == "recommendations":
    match_properties_map = match_recommendations_properties_map
elif db == "northwind":
    match_properties_map = match_northwind_properties_map
elif db == "twitter":
    match_properties_map = match_twitter_properties_map