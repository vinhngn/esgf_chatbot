"""Neo4j planner evidence collected through public read-only interfaces."""

from __future__ import annotations

from typing import Any, Callable

Query = Callable[..., list[dict]]


def _unavailable(exc: Exception) -> dict:
    return {
        "status": "unavailable",
        "count": None,
        "error_type": type(exc).__name__,
        "message": str(exc)[:500],
    }


def available(count: int) -> dict:
    return {
        "status": "available",
        "count": count,
        "error_type": None,
        "message": "",
    }


def collect_indexes(
    session: Any,
    query: Query,
) -> tuple[list[dict], dict]:
    try:
        rows = query(
            session,
            """
            SHOW INDEXES
            YIELD name, type, entityType, labelsOrTypes, properties, state,
                  populationPercent, indexProvider, owningConstraint, options
            RETURN name, type, entityType, labelsOrTypes, properties, state,
                   populationPercent, indexProvider, owningConstraint, options
            ORDER BY name
            """,
        )
    except Exception as exc:
        return [], _unavailable(exc)

    indexes = []
    for row in rows:
        options = row.get("options") or {}
        index_config = options.get("indexConfig") or {}
        indexes.append(
            {
                "name": str(row.get("name") or ""),
                "type": str(row.get("type") or ""),
                "entity_type": str(row.get("entityType") or ""),
                "labels_or_types": [
                    str(value)
                    for value in row.get("labelsOrTypes") or []
                    if not str(value).startswith("_")
                ],
                "properties": [
                    str(prop) for prop in row.get("properties") or []
                ],
                "state": str(row.get("state") or ""),
                "population_percent": row.get("populationPercent"),
                "owning_constraint": row.get("owningConstraint"),
                "dimensions": index_config.get("vector.dimensions"),
                "similarity_function": index_config.get(
                    "vector.similarity_function"
                ),
                "provider": row.get("indexProvider")
                or options.get("indexProvider"),
            }
        )
    return indexes, available(len(indexes))


def vector_indexes(indexes: list[dict]) -> list[dict]:
    return [
        index
        for index in indexes
        if str(index.get("type") or "").upper() == "VECTOR"
        and index.get("labels_or_types")
        and index.get("properties")
    ]


def collect_constraints(
    session: Any,
    query: Query,
) -> tuple[list[dict], dict]:
    try:
        rows = query(session, "SHOW CONSTRAINTS")
    except Exception as exc:
        return [], _unavailable(exc)

    constraints = [
        {
            "name": str(row.get("name") or ""),
            "type": str(row.get("type") or ""),
            "entity_type": str(row.get("entityType") or ""),
            "labels_or_types": [
                str(value)
                for value in row.get("labelsOrTypes") or []
                if not str(value).startswith("_")
            ],
            "properties": [
                str(prop) for prop in row.get("properties") or []
            ],
            "owned_index": row.get("ownedIndex"),
            "property_type": row.get("propertyType"),
        }
        for row in rows
    ]
    return constraints, available(len(constraints))


def _index_statistics(
    rows: list[dict],
    node_counts: dict[str, int],
) -> list[dict]:
    statistics = []
    for row in rows:
        total_size = int(row.get("totalSize") or 0)
        estimated_unique_size = int(row.get("estimatedUniqueSize") or 0)
        labels = [str(value) for value in row.get("labels") or []]
        entity_count = node_counts.get(labels[0], 0) if len(labels) == 1 else 0
        statistics.append(
            {
                "index_type": str(row.get("indexType") or ""),
                "labels": labels,
                "relationship_types": [
                    str(value)
                    for value in row.get("relationshipTypes") or []
                ],
                "properties": [
                    str(value) for value in row.get("properties") or []
                ],
                "total_size": total_size,
                "estimated_unique_size": estimated_unique_size,
                "unique_value_selectivity": (
                    1.0 / estimated_unique_size
                    if estimated_unique_size > 0
                    else None
                ),
                "property_exists_selectivity": (
                    total_size / entity_count
                    if entity_count > 0
                    else None
                ),
                "updates_since_estimation": int(
                    row.get("updatesSinceEstimation") or 0
                ),
                "provider": str(row.get("indexProvider") or ""),
            }
        )
    return statistics


def collect_graph_statistics(
    session: Any,
    query: Query,
) -> tuple[dict, dict]:
    try:
        rows = query(session, "CALL db.stats.retrieve('GRAPH COUNTS')")
    except Exception as exc:
        return {}, _unavailable(exc)
    if not rows or not isinstance(rows[0].get("data"), dict):
        return {}, _unavailable(ValueError("GRAPH COUNTS returned no data"))

    data = rows[0]["data"]
    node_rows = data.get("nodes") or []
    relationship_rows = data.get("relationships") or []
    node_counts: dict[str, int] = {}
    relationship_counts: dict[str, int] = {}
    start_counts: dict[str, int] = {}
    end_counts: dict[str, int] = {}
    all_node_count = 0
    all_relationship_count = 0

    for row in node_rows:
        count = int(row.get("count") or 0)
        if row.get("label"):
            node_counts[str(row["label"])] = count
        else:
            all_node_count = count

    for row in relationship_rows:
        count = int(row.get("count") or 0)
        rel_type = str(row.get("relationshipType") or "")
        start_label = str(row.get("startLabel") or "")
        end_label = str(row.get("endLabel") or "")
        if not rel_type:
            all_relationship_count = count
        elif start_label:
            start_counts[f"{start_label}|{rel_type}"] = count
        elif end_label:
            end_counts[f"{rel_type}|{end_label}"] = count
        else:
            relationship_counts[rel_type] = count

    statistics = {
        "source": "db.stats.retrieve('GRAPH COUNTS')",
        "all_node_count": all_node_count,
        "all_relationship_count": all_relationship_count,
        "node_counts": node_counts,
        "relationship_counts": relationship_counts,
        "relationship_start_counts": start_counts,
        "relationship_end_counts": end_counts,
        "index_statistics": _index_statistics(
            data.get("indexes") or [],
            node_counts,
        ),
    }
    return statistics, available(len(node_rows) + len(relationship_rows))


def fallback_graph_statistics(labels: list[dict]) -> dict:
    """Retain exact per-label counts when GRAPH COUNTS is unavailable."""
    return {
        "source": "label_count_queries",
        "all_node_count": None,
        "all_relationship_count": None,
        "node_counts": {
            str(label["label"]): int(label.get("count") or 0)
            for label in labels
            if label.get("label")
        },
        "relationship_counts": {},
        "relationship_start_counts": {},
        "relationship_end_counts": {},
        "index_statistics": [],
    }


def _quote_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def _count_query(
    session: Any,
    query: Query,
    cypher: str,
) -> int | None:
    try:
        rows = query(session, cypher)
    except Exception:
        return None
    if not rows or rows[0].get("count") is None:
        return None
    return int(rows[0]["count"])


def collect_count_store_statistics(
    session: Any,
    query: Query,
    labels: list[dict],
    relationships: list[dict],
) -> tuple[dict, dict]:
    """Use count-store-compatible Cypher when db.stats is unavailable."""
    node_counts = {
        str(label["label"]): int(label.get("count") or 0)
        for label in labels
        if label.get("label")
    }
    relationship_counts: dict[str, int] = {}
    start_counts: dict[str, int] = {}
    end_counts: dict[str, int] = {}

    all_node_count = _count_query(
        session,
        query,
        "MATCH (n) RETURN count(n) AS count",
    )
    all_relationship_count = _count_query(
        session,
        query,
        "MATCH ()-[r]->() RETURN count(r) AS count",
    )
    seen_relationships: set[str] = set()
    seen_starts: set[tuple[str, str]] = set()
    seen_ends: set[tuple[str, str]] = set()

    for relationship in relationships:
        rel_type = str(relationship.get("type") or "")
        if not rel_type:
            continue
        quoted_type = _quote_identifier(rel_type)
        if rel_type not in seen_relationships:
            seen_relationships.add(rel_type)
            count = _count_query(
                session,
                query,
                f"MATCH ()-[r:{quoted_type}]->() RETURN count(r) AS count",
            )
            if count is not None:
                relationship_counts[rel_type] = count

        for pattern in relationship.get("patterns") or []:
            start_label = str(pattern.get("from") or "")
            end_label = str(pattern.get("to") or "")
            start_key = (start_label, rel_type)
            if start_label and start_key not in seen_starts:
                seen_starts.add(start_key)
                count = _count_query(
                    session,
                    query,
                    "MATCH "
                    f"(:{_quote_identifier(start_label)})"
                    f"-[r:{quoted_type}]->() "
                    "RETURN count(r) AS count",
                )
                if count is not None:
                    start_counts[f"{start_label}|{rel_type}"] = count

            end_key = (rel_type, end_label)
            if end_label and end_key not in seen_ends:
                seen_ends.add(end_key)
                count = _count_query(
                    session,
                    query,
                    "MATCH ()"
                    f"-[r:{quoted_type}]->"
                    f"(:{_quote_identifier(end_label)}) "
                    "RETURN count(r) AS count",
                )
                if count is not None:
                    end_counts[f"{rel_type}|{end_label}"] = count

    statistics = {
        "source": "cypher_count_store_queries",
        "all_node_count": all_node_count,
        "all_relationship_count": all_relationship_count,
        "node_counts": node_counts,
        "relationship_counts": relationship_counts,
        "relationship_start_counts": start_counts,
        "relationship_end_counts": end_counts,
        "index_statistics": [],
    }
    observed = (
        len(node_counts)
        + len(relationship_counts)
        + len(start_counts)
        + len(end_counts)
    )
    return statistics, {
        "status": "fallback",
        "count": observed,
        "error_type": None,
        "message": "db.stats unavailable; used count-store-compatible Cypher",
    }


def _ratio(
    numerator: int | float | None,
    denominator: int | float | None,
) -> float | None:
    if numerator is None or not denominator:
        return None
    return float(numerator) / float(denominator)


def attach_pattern_statistics(
    relationships: list[dict],
    statistics: dict,
) -> list[dict]:
    node_counts = statistics.get("node_counts") or {}
    relationship_counts = statistics.get("relationship_counts") or {}
    start_counts = statistics.get("relationship_start_counts") or {}
    end_counts = statistics.get("relationship_end_counts") or {}
    enriched = []

    for relationship in relationships:
        rel_type = str(relationship.get("type") or "")
        patterns = []
        for pattern in relationship.get("patterns") or []:
            start_label = str(pattern.get("from") or "")
            end_label = str(pattern.get("to") or "")
            start_count = start_counts.get(f"{start_label}|{rel_type}")
            end_count = end_counts.get(f"{rel_type}|{end_label}")
            relationship_count = relationship_counts.get(rel_type)

            if start_count is not None and end_count is not None:
                estimated_count = min(start_count, end_count)
                count_kind = "upper_bound"
                count_method = "neo4j_min_endpoint_counts"
            elif start_count is not None:
                estimated_count = start_count
                count_kind = "directional_count"
                count_method = "start_label_count"
            elif end_count is not None:
                estimated_count = end_count
                count_kind = "directional_count"
                count_method = "end_label_count"
            elif relationship_count is not None:
                estimated_count = relationship_count
                count_kind = "relationship_total"
                count_method = "relationship_type_count"
            else:
                estimated_count = pattern.get("count")
                count_kind = "unavailable"
                count_method = "none"

            patterns.append(
                {
                    **pattern,
                    "count": estimated_count,
                    "count_kind": count_kind,
                    "count_method": count_method,
                    "relationship_count": relationship_count,
                    "start_endpoint_count": start_count,
                    "end_endpoint_count": end_count,
                    "outgoing_fanout": _ratio(
                        estimated_count,
                        node_counts.get(start_label),
                    ),
                    "incoming_fanout": _ratio(
                        estimated_count,
                        node_counts.get(end_label),
                    ),
                }
            )
        enriched.append({**relationship, "patterns": patterns})
    return enriched


def pattern_steps(relationships: list[dict]) -> list[dict]:
    return [
        {
            "from": pattern.get("from"),
            "type": relationship.get("type"),
            "to": pattern.get("to"),
            "estimated_count": pattern.get("count"),
            "count_kind": pattern.get("count_kind"),
            "count_method": pattern.get("count_method"),
            "outgoing_fanout": pattern.get("outgoing_fanout"),
            "incoming_fanout": pattern.get("incoming_fanout"),
        }
        for relationship in relationships
        for pattern in relationship.get("patterns") or []
    ]
