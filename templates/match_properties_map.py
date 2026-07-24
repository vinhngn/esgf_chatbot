"""
Match properties map per database type.
FIX: Added default fallback so match_properties_map is always defined.
"""

from config import get_settings

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

match_movies_properties_map = {
    "Movie": ["title", "tagline", "released", "votes"],
    "Person": ["name", "born"],
}

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
    "Actor": ["name", "born", "died", "bornIn", "imdbId", "tmdbId", "url"],
    "Director": ["name", "bio", "born", "died", "bornIn", "imdbId", "tmdbId", "poster", "url"],
    "Person": ["name", "born", "died", "bornIn", "imdbId", "tmdbId", "url"],
}

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
    "Category": ["categoryName", "categoryID", "description", "picture"],
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

match_twitter_properties_map = {
    "User": [
        "name",
        "screen_name",
        "url",
        "location",
        "profile_image_url",
        "followers",
        "following",
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
    "Tweet": ["id", "id_str", "text", "created_at", "favorites", "import_method"],
    "Hashtag": ["name"],
    "Link": ["url"],
    "Source": ["name"],
}

_PROPERTIES_MAP = {
    "climate": match_climate_properties_map,
    "movies": match_movies_properties_map,
    "recommendations": match_recommendations_properties_map,
    "northwind": match_northwind_properties_map,
    "twitter": match_twitter_properties_map,
}


def get_match_properties_map(db_name: str | None = None) -> dict:
    """Get match properties map for the given database (or current config)."""
    db = db_name or get_settings().profile_database_name
    return _PROPERTIES_MAP.get(db, match_climate_properties_map)


# Module-level convenience
match_properties_map = get_match_properties_map()
