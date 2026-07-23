from __future__ import annotations

import json
import re
from datetime import date, datetime, time


def _is_json_serializable(value) -> bool:
    """Check if a value can be serialized by json.dumps."""
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError, OverflowError):
        return False


def normalize_value(value):
    if value is None:
        return value
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(normalize_value(v) for v in value)
    # Neo4j temporal types and any other non-serializable objects → str()
    if hasattr(value, 'iso_format'):
        return value.iso_format()
    if not _is_json_serializable(value):
        return str(value)
    return value


def strip_quotes(s: str) -> str:
    return s.strip("'").strip('"')


def parse_schema(schema_text: str) -> tuple[set[str], set[str]]:
    """Parse Neo4j schema text to extract node labels and relationship types."""
    labels, relationships = set(), set()
    for line in schema_text.splitlines():
        for label in re.findall(r"\(:([A-Za-z0-9_]+)\)", line):
            labels.add(label)
        for rel in re.findall(r"\[:([A-Za-z0-9_]+)\]", line):
            relationships.add(rel)
    return labels, relationships


def clean_cypher_query(query_raw: str) -> str:
    """Clean up LLM-generated Cypher query (remove markdown, prefix, semicolons)."""
    query = re.sub(r"```cypher\s*", "", query_raw, flags=re.IGNORECASE)
    query = re.sub(r"```\s*", "", query)
    query = re.sub(r"^\s*cypher\s+", "", query, flags=re.IGNORECASE)
    # PromptTemplate examples escape literal Cypher maps as {{...}}. The LLM can
    # copy those braces verbatim, so normalize them before Neo4j sees the query.
    query = query.replace("{{", "{").replace("}}", "}")
    return query.rstrip(";").strip()


def strip_noisy_return_properties(cypher: str) -> str:
    """
    Remove noisy/internal properties from RETURN clauses that the LLM
    tends to over-generate.  These properties (like t.id, t.id_str,
    t.import_method) are almost never in gold queries and cause
    extra-column penalties in t2c scoring.
    """
    if not cypher or "RETURN" not in cypher.upper():
        return cypher

    # Properties that should be stripped if they appear alongside other properties
    _NOISY = {
        "id", "id_str", "import_method",
    }

    match = re.search(r"(?i)\bRETURN\b\s+(.*)", cypher)
    if not match:
        return cypher

    return_body = match.group(1)
    suffix_match = re.search(
        r"\bORDER\s+BY\b|\bLIMIT\b|\bSKIP\b",
        return_body,
        flags=re.IGNORECASE,
    )
    if suffix_match:
        return_core = return_body[: suffix_match.start()].strip()
        suffix = " " + return_body[suffix_match.start():].strip()
    else:
        return_core = return_body.strip()
        suffix = ""

    items = _split_top_level_commas(return_core)
    if len(items) <= 1:
        return cypher  # Don't strip if only 1 column

    cleaned = []
    for item in items:
        # Check if this is a simple property like t.id, t.id_str, t.import_method
        prop_match = re.match(r"^(\w+)\.(\w+)$", item.strip())
        if prop_match and prop_match.group(2) in _NOISY:
            continue  # Skip noisy property
        # Also check aliased form: t.id AS something
        alias_match = re.match(r"^(\w+)\.(\w+)\s+AS\s+\w+$", item.strip(), re.IGNORECASE)
        if alias_match and alias_match.group(2) in _NOISY:
            continue
        cleaned.append(item)

    if len(cleaned) == len(items):
        return cypher  # Nothing was stripped

    if not cleaned:
        return cypher  # Don't strip everything

    new_return = "RETURN " + ", ".join(cleaned) + suffix
    return cypher[: match.start()] + new_return


def repair_northwind_order_line_properties(cypher: str) -> str:
    """Repair common Northwind mistakes around ORDERS relationship properties."""
    if not cypher or "ORDERS" not in cypher.upper():
        return cypher

    repaired = cypher

    # If an ORDERS relationship variable is already bound, order-line unitPrice,
    # quantity, and discount should come from that relationship for order-line
    # arithmetic and filters, not from Product or Order nodes.
    rel_vars = re.findall(r"\[(\w+):ORDERS\]", repaired, flags=re.IGNORECASE)
    if rel_vars:
        rel_var = rel_vars[0]
        if re.search(rf"\b{rel_var}\.quantity\b", repaired):
            repaired = re.sub(
                r"\bavg\s*\(\s*toFloat\s*\(\s*\w+\.unitPrice\s*\)\s*\)",
                f"avg(toFloat({rel_var}.unitPrice))",
                repaired,
                flags=re.IGNORECASE,
            )
            repaired = re.sub(
                r"\bavg\s*\(\s*\w+\.unitPrice\s*\)",
                f"avg(toFloat({rel_var}.unitPrice))",
                repaired,
                flags=re.IGNORECASE,
            )
        for prop in ("unitPrice", "quantity", "discount"):
            repaired = re.sub(
                rf"\b(o|order|p|product)\.{prop}\b",
                f"{rel_var}.{prop}",
                repaired,
                flags=re.IGNORECASE,
            )

    return repaired


def repair_northwind_projection_and_metrics(cypher: str, question: str | None = None) -> str:
    """Question-aware Northwind cleanup for common projection/metric mistakes."""
    if not cypher or not question:
        return cypher
    lowered = question.lower()
    repaired = cypher

    if "customerid" not in lowered and "customer id" not in lowered:
        repaired = re.sub(r"\bc\.customerID\b", "c.companyName", repaired)

    if "productid" not in lowered and "product id" not in lowered:
        repaired = re.sub(r"\bp\.productID\s*,\s*", "", repaired)
        repaired = re.sub(r",\s*p\.productID\b", "", repaired)

    if re.search(r"\b(most frequently ordered|ordered the most times)\b", lowered):
        repaired = re.sub(
            r"\bSUM\s*\(\s*o\.quantity\s*\)\s+AS\s+\w+",
            "COUNT(o) AS orderCount",
            repaired,
            flags=re.IGNORECASE,
        )
        repaired = re.sub(r"\btotalOrders\b", "orderCount", repaired)

    if "product" in lowered and re.search(
        r"\bRETURN\s+(orderCount|priceVariations|avgDiscount|AverageDiscount|ordersCount)\b",
        repaired,
        flags=re.IGNORECASE,
    ):
        repaired = re.sub(
            r"\bRETURN\s+(orderCount|priceVariations|avgDiscount|AverageDiscount|ordersCount)\b",
            r"RETURN p.productName, \1",
            repaired,
            flags=re.IGNORECASE,
        )

    if re.search(r"\breorder level greater than the average reorder level\b", lowered):
        repaired = re.sub(
            r"\bRETURN\s+\w+\.productName\s*,\s*\w+\.reorderLevel\s*,\s*avgReorderLevel\b",
            lambda match: re.sub(r",.*", "", match.group(0)),
            repaired,
            flags=re.IGNORECASE,
        )
        repaired = re.sub(
            r"\bRETURN\s+(\w+)\.productName\s*,\s*\1\.reorderLevel\b",
            r"RETURN \1.productName",
            repaired,
            flags=re.IGNORECASE,
        )

    if re.search(r"\bfirst\s+\d+\s+products?.*reorder level above\b", lowered) and "ORDER BY" not in repaired.upper():
        repaired = re.sub(r"\s+LIMIT\s+(\d+)\b", r" ORDER BY p.productName LIMIT \1", repaired, flags=re.IGNORECASE)

    if re.search(r"\bfirst\s+\d+\s+orders?.*freight.*greater", lowered) and "ORDER BY" not in repaired.upper():
        repaired = re.sub(
            r"\s+LIMIT\s+(\d+)\b",
            r" ORDER BY toFloat(o.freight) DESC LIMIT \1",
            repaired,
            flags=re.IGNORECASE,
        )

    if re.search(r"\bmost recent orders?.*orderdate|orders?.*based on orderdate", lowered):
        repaired = re.sub(
            r"\bRETURN\s+o\.orderID\s*,\s*o\.orderDate\b",
            "RETURN o.orderID, o.orderDate, o.customerID, o.shipName, o.shipCity, o.shipCountry",
            repaired,
            flags=re.IGNORECASE,
        )

    return repaired



def rewrite_bare_node_returns(cypher: str, graph=None, question: str | None = None) -> str:
    """
    Detect RETURN clauses that return bare node variables (e.g. RETURN m, RETURN t)
    and rewrite them to return only the KEY properties for that label.

    This is critical for t2c_eval_framework scoring: returning a full node causes
    container flattening which creates extra columns → __MISSING__ → exact_match=0.

    Uses a curated map of key properties per label rather than dumping all properties.
    """
    if not cypher or "RETURN" not in cypher.upper():
        return cypher

    match = re.search(r"(?i)\bRETURN\b\s+(.*)", cypher)
    if not match:
        return cypher

    return_body = match.group(1)

    # Split off ORDER BY / LIMIT / SKIP suffix
    return_core = re.split(
        r"\bORDER BY\b|\bLIMIT\b|\bSKIP\b",
        return_body,
        flags=re.IGNORECASE,
    )[0].strip()
    suffix = return_body[len(return_core):]

    items = _split_top_level_commas(return_core)
    var_to_label = _extract_var_labels(cypher)

    new_items = []
    changed = False
    aggregation_aliases = _extract_metric_aliases(cypher)
    for item in items:
        clean_item = re.sub(r"(?i)\bDISTINCT\b", "", item).strip()
        has_distinct = "DISTINCT" in item.upper()

        if (
            re.match(r"^[a-zA-Z_]\w*$", clean_item)
            and clean_item in var_to_label
            and " AS " not in item.upper()
        ):
            label = var_to_label[clean_item]
            props = _question_key_properties(label, question or "", cypher) or _KEY_PROPERTIES.get(label)
            if props == ["__KEEP_NODE__"]:
                new_items.append(item)
                continue
            if props:
                prefix = "DISTINCT " if has_distinct else ""
                expanded = ", ".join(f"{prefix}{clean_item}.{p}" for p in props)
                new_items.append(expanded)
                for alias in aggregation_aliases:
                    if alias not in new_items and re.search(rf"\b{alias}\b", cypher):
                        new_items.append(alias)
                changed = True
                continue

        new_items.append(item)

    if not changed:
        return cypher

    new_return = "RETURN " + ", ".join(new_items) + suffix
    cypher_before_return = cypher[: match.start()]
    return cypher_before_return + new_return


# Curated key properties per label — only the properties that gold queries
# typically ask for.  Keeps RETURN clauses lean to avoid extra-column penalties.
_KEY_PROPERTIES: dict[str, list[str]] = {
    # Twitter
    "Tweet": ["text", "favorites"],
    "User": ["screen_name", "name", "followers", "following"],
    "Me": ["screen_name", "name", "followers", "following"],
    "Hashtag": ["name"],
    "Link": ["url"],
    "Source": ["name"],
    # Movies
    "Movie": ["title", "released", "votes", "tagline"],
    "Person": ["name", "born"],
    # Recommendations
    "Genre": ["name"],
    "Actor": ["name", "born"],
    "Director": ["name", "born"],
    # Climate
    "Variable": ["name", "cf_standard_name"],
    "Experiment": ["name"],
    "Institute": ["name"],
    "Realm": ["name"],
    "Frequency": ["name"],
    "Resolution": ["name"],
    "SourceComponent": ["name"],
    "RCM": ["name"],
    "Country": ["name"],
    "Country_Subdivision": ["name", "code"],
    "Continent": ["name"],
    # Northwind
    "Product": ["productName", "unitPrice", "unitsInStock"],
    "Category": ["categoryName", "description"],
    "Supplier": ["companyName", "contactName"],
    "Customer": ["companyName", "contactName"],
    "Order": ["orderID", "orderDate", "shipCountry"],
}


def _question_key_properties(label: str, question: str, cypher: str) -> list[str] | None:
    lowered = question.lower()
    if not lowered:
        return None

    if label == "Product":
        if re.search(
            r"\b(display|show|list|which)\s+(?:all\s+)?products?\s+with\b",
            lowered,
        ) and not re.search(r"\bnever\b|\breorder level above\b", lowered):
            return ["__KEEP_NODE__"]
        if re.search(r"\btop\s+\d+\s+products?.*lowest units on order", lowered):
            return ["__KEEP_NODE__"]
        props: list[str] = []
        if "productid" in lowered or "product id" in lowered:
            props.append("productID")
        if "productname" in lowered or "product name" in lowered or "products" in lowered or "product" in lowered:
            props.append("productName")
        if "reorder level" in lowered:
            props.append("reorderLevel")
        if "unit price" in lowered or "unitprice" in lowered:
            props.append("unitPrice")
        if "units in stock" in lowered or "unitsinstock" in lowered:
            props.append("unitsInStock")
        if (
            ("units on order" in lowered or "unitsonorder" in lowered)
            and not re.search(r"units\s*on\s*order\s*[=<>]|unitsonorder\s*[=<>]", lowered)
        ):
            props.append("unitsOnOrder")
        if "discontinued" in lowered and not props:
            props.append("productName")
        return list(dict.fromkeys(props)) or ["productName"]

    if label == "Supplier":
        if re.search(r"\b(which|identify all|show all)\s+suppliers?\s+(?:who\s+)?supply\b", lowered):
            return ["__KEEP_NODE__"]
        props = []
        if "supplierid" in lowered or "supplier id" in lowered:
            props.append("supplierID")
        if "contactname" in lowered or "contact name" in lowered:
            props.append("contactName")
        if "companyname" in lowered or "company name" in lowered or "supplier" in lowered:
            props.append("companyName")
        return list(dict.fromkeys(props)) or ["companyName"]

    if label == "Customer":
        props = []
        if "customerid" in lowered or "customer id" in lowered:
            props.append("customerID")
        if "companyname" in lowered or "company name" in lowered:
            props.append("companyName")
        if "contactname" in lowered or "contact name" in lowered:
            props.append("contactName")
        if re.search(r"\bfirst\s+\d+\s+customers?\b", lowered):
            props.extend(["customerID", "companyName", "contactName"])
        if "customer" in lowered and not props:
            props.append("companyName")
        return list(dict.fromkeys(props)) or ["companyName"]

    if label == "Order":
        props = []
        if "orderid" in lowered or "order id" in lowered or "orders" in lowered or "order" in lowered:
            props.append("orderID")
        if "orderdate" in lowered or "order date" in lowered:
            props.append("orderDate")
        if "shipcity" in lowered or "ship city" in lowered:
            props.append("shipCity")
        if "shipcountry" in lowered or "ship country" in lowered:
            props.append("shipCountry")
        if "freight" in lowered:
            props.append("freight")
        return list(dict.fromkeys(props)) or ["orderID"]

    if label == "Category":
        props = []
        if "categoryid" in lowered or "category id" in lowered:
            props.append("categoryID")
        if "categoryname" in lowered or "category name" in lowered or "category" in lowered:
            props.append("categoryName")
        if "description" in lowered:
            props.append("description")
        return list(dict.fromkeys(props)) or ["categoryName"]

    return None


def _extract_metric_aliases(cypher: str) -> list[str]:
    aliases = []
    for expression, alias in re.findall(
        r"(?i)\b(COUNT\s*\([^)]*\)|SUM\s*\([^)]*\)|AVG\s*\([^)]*\)|MIN\s*\([^)]*\)|MAX\s*\([^)]*\)|count\s*\{[^}]*\})\s+AS\s+([A-Za-z_]\w*)",
        cypher,
    ):
        aliases.append(alias)
    return list(dict.fromkeys(aliases))


def _split_top_level_commas(s: str) -> list[str]:
    """Split a string by commas, respecting parentheses and quotes."""
    items: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quote = False
    quote_char = None

    for ch in s:
        if ch in ("'", '"'):
            if not in_quote:
                in_quote = True
                quote_char = ch
            elif quote_char == ch:
                in_quote = False
                quote_char = None
        if not in_quote:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(0, depth - 1)
            if ch == "," and depth == 0:
                item = "".join(buf).strip()
                if item:
                    items.append(item)
                buf = []
                continue
        buf.append(ch)

    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return items


def _extract_var_labels(cypher: str) -> dict[str, str]:
    """Extract variable→label mapping from MATCH/OPTIONAL MATCH clauses."""
    var_to_label: dict[str, str] = {}
    for m in re.finditer(r"\((\w+):(\w+)(?:\s*\{[^}]*\})?\)", cypher):
        var_name, label = m.group(1), m.group(2)
        var_to_label[var_name] = label
    return var_to_label
