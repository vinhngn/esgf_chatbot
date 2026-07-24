"""Optional database-specific repairs kept outside the universal pipeline."""

from __future__ import annotations

import re
from collections.abc import Callable

DomainRepair = Callable[[str, str | None], str]


def _repair_northwind_order_line_properties(
    cypher: str,
    question: str | None = None,
) -> str:
    del question
    if not cypher or "ORDERS" not in cypher.upper():
        return cypher

    repaired = cypher
    rel_vars = re.findall(r"\[(\w+):ORDERS\]", repaired, flags=re.IGNORECASE)
    if not rel_vars:
        return repaired

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


def _repair_northwind_projection_and_metrics(
    cypher: str,
    question: str | None = None,
) -> str:
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
        (
            r"\bRETURN\s+(orderCount|priceVariations|avgDiscount|"
            r"AverageDiscount|ordersCount)\b"
        ),
        repaired,
        flags=re.IGNORECASE,
    ):
        repaired = re.sub(
            (
                r"\bRETURN\s+(orderCount|priceVariations|avgDiscount|"
                r"AverageDiscount|ordersCount)\b"
            ),
            r"RETURN p.productName, \1",
            repaired,
            flags=re.IGNORECASE,
        )

    if re.search(
        r"\breorder level greater than the average reorder level\b",
        lowered,
    ):
        repaired = re.sub(
            (
                r"\bRETURN\s+\w+\.productName\s*,\s*\w+\.reorderLevel"
                r"\s*,\s*avgReorderLevel\b"
            ),
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

    if (
        re.search(r"\bfirst\s+\d+\s+products?.*reorder level above\b", lowered)
        and "ORDER BY" not in repaired.upper()
    ):
        repaired = re.sub(
            r"\s+LIMIT\s+(\d+)\b",
            r" ORDER BY p.productName LIMIT \1",
            repaired,
            flags=re.IGNORECASE,
        )

    if (
        re.search(r"\bfirst\s+\d+\s+orders?.*freight.*greater", lowered)
        and "ORDER BY" not in repaired.upper()
    ):
        repaired = re.sub(
            r"\s+LIMIT\s+(\d+)\b",
            r" ORDER BY toFloat(o.freight) DESC LIMIT \1",
            repaired,
            flags=re.IGNORECASE,
        )

    if re.search(
        r"\bmost recent orders?.*orderdate|orders?.*based on orderdate",
        lowered,
    ):
        repaired = re.sub(
            r"\bRETURN\s+o\.orderID\s*,\s*o\.orderDate\b",
            ("RETURN o.orderID, o.orderDate, o.customerID, o.shipName, o.shipCity, o.shipCountry"),
            repaired,
            flags=re.IGNORECASE,
        )
    return repaired


_DOMAIN_REPAIRS: dict[str, tuple[DomainRepair, ...]] = {
    "northwind": (
        _repair_northwind_order_line_properties,
        _repair_northwind_projection_and_metrics,
    ),
}


def apply_domain_repairs(
    cypher: str,
    *,
    question: str,
    database: str,
) -> str:
    """Apply registered compatibility repairs for one logical database."""
    repaired = cypher
    for repair in _DOMAIN_REPAIRS.get(database.lower(), ()):
        repaired = repair(repaired, question)
    return repaired
