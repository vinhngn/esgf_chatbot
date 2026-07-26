from __future__ import annotations

import json

from services.text2cypher import service


def test_schema_metadata_comes_from_profile_instead_of_domain_templates(
    monkeypatch,
    tmp_path,
) -> None:
    profile = {
        "database": "custom_graph",
        "source": "custom.csv",
        "source_type": "benchmark_csv",
        "row_count": 1,
        "examples": [{"row": 1, "question": "List nodes", "cypher": "MATCH (n) RETURN n"}],
        "schema_profile": {
            "labels": [{"label": "CustomNode", "count": 3, "properties": []}],
            "relationships": [],
            "paths": [],
            "vector_indexes": [],
            "summary": {"label_count": 1, "relationship_type_count": 0},
        },
    }
    (tmp_path / "custom_graph_profile.json").write_text(
        json.dumps(profile),
        encoding="utf-8",
    )
    monkeypatch.setattr(service, "profile_dir", lambda: tmp_path)

    metadata = service.get_schema_info("custom_graph")

    assert metadata["database"] == "custom_graph"
    assert metadata["profile_available"] is True
    assert metadata["schema_profile"]["labels"][0]["label"] == "CustomNode"
    assert "cypher_template" not in metadata
    assert "entity_definitions" not in metadata
    assert "match_properties" not in metadata
