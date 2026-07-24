from __future__ import annotations

from services.control_center.models import ConnectionKind, DatabaseConnection, SshTunnel

NEO4J_LABS_URI = "neo4j+s://demo.neo4jlabs.com"
NEO4J_LABS_DATABASES = (
    "movies",
    "northwind",
    "recommendations",
    "twitter",
    "stackoverflow",
)

CYPHERBENCH_PORTS = {
    "company": 15062,
    "fictional_character": 15063,
    "flight_accident": 15064,
    "geography": 15065,
}


def built_in_connections() -> list[DatabaseConnection]:
    connections = [
        DatabaseConnection(
            id=f"preset-{name}",
            name=f"Neo4j Labs - {name}",
            profile_name=name,
            kind=ConnectionKind.DIRECT,
            uri=NEO4J_LABS_URI,
            database=name,
            username=name,
            built_in=True,
        )
        for name in NEO4J_LABS_DATABASES
    ]
    connections.extend(
        DatabaseConnection(
            id=f"preset-{name}",
            name=f"CypherBench - {name}",
            profile_name=name,
            kind=ConnectionKind.SSH_TUNNEL,
            uri=f"bolt://127.0.0.1:{port}",
            database="neo4j",
            username="neo4j",
            ssh=SshTunnel(
                jump_host="cis-linux2.temple.edu",
                target_host="exxacta100.cis.temple.edu",
                remote_port=port,
                local_port=port,
            ),
            built_in=True,
        )
        for name, port in CYPHERBENCH_PORTS.items()
    )
    return connections
