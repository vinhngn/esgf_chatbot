from __future__ import annotations

from dataclasses import dataclass

from neo4j import GraphDatabase

from services.control_center.models import ConnectionKind, DatabaseConnection
from services.control_center.secrets import SecretStore
from services.control_center.tunnel import SshTunnelSession


@dataclass(frozen=True)
class ConnectionInfo:
    server: str
    database: str


def suggested_database_password(connection: DatabaseConnection) -> str:
    if connection.id.startswith("preset-") and connection.kind == ConnectionKind.DIRECT:
        return connection.profile_name
    if connection.id.startswith("preset-") and connection.kind == ConnectionKind.SSH_TUNNEL:
        return "cypherbench"
    return ""


class ConnectionSession:
    def __init__(self, connection: DatabaseConnection, secrets: SecretStore) -> None:
        self.connection = connection
        self.secrets = secrets
        self.tunnel: SshTunnelSession | None = None

    @property
    def database_password(self) -> str:
        return self.secrets.get(self.connection.id, "database_password") or (
            suggested_database_password(self.connection)
        )

    def open(self) -> ConnectionInfo:
        if self.connection.kind == ConnectionKind.SSH_TUNNEL:
            if self.connection.ssh is None:
                raise ValueError("SSH settings are missing")
            self.tunnel = SshTunnelSession(
                self.connection.ssh,
                self.secrets.get(self.connection.id, "ssh_password"),
            )
            self.tunnel.start()

        driver = GraphDatabase.driver(
            self.connection.runtime_uri,
            auth=(self.connection.username, self.database_password),
            connection_timeout=20,
        )
        try:
            driver.verify_connectivity()
            with driver.session(database=self.connection.database) as session:
                result = session.run(
                    "CALL dbms.components() YIELD name, versions "
                    "RETURN name AS product, versions[0] AS version LIMIT 1"
                ).single()
            product = str(result["product"]) if result else "Neo4j"
            version = str(result["version"]) if result else "unknown"
            return ConnectionInfo(
                server=f"{product} {version}",
                database=self.connection.database,
            )
        except Exception:
            self.close()
            raise
        finally:
            driver.close()

    def close(self) -> None:
        if self.tunnel:
            self.tunnel.stop()
            self.tunnel = None
