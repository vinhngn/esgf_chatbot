"""Secret storage abstractions; production values use the operating-system keyring."""

from __future__ import annotations

from typing import Protocol


class SecretStore(Protocol):
    def get(self, connection_id: str, name: str) -> str:
        ...

    def set(self, connection_id: str, name: str, value: str) -> None:
        ...

    def delete(self, connection_id: str, name: str) -> None:
        ...


class KeyringSecretStore:
    SERVICE = "esgf-text2cypher-client"

    @staticmethod
    def _key(connection_id: str, name: str) -> str:
        return f"{connection_id}:{name}"

    def get(self, connection_id: str, name: str) -> str:
        import keyring

        return keyring.get_password(self.SERVICE, self._key(connection_id, name)) or ""

    def set(self, connection_id: str, name: str, value: str) -> None:
        import keyring

        key = self._key(connection_id, name)
        if value:
            keyring.set_password(self.SERVICE, key, value)
        else:
            self.delete(connection_id, name)

    def delete(self, connection_id: str, name: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(self.SERVICE, self._key(connection_id, name))
        except PasswordDeleteError:
            pass


class MemorySecretStore:
    """In-memory implementation used by tests and temporary sessions."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def get(self, connection_id: str, name: str) -> str:
        return self._values.get((connection_id, name), "")

    def set(self, connection_id: str, name: str, value: str) -> None:
        if value:
            self._values[(connection_id, name)] = value
        else:
            self.delete(connection_id, name)

    def delete(self, connection_id: str, name: str) -> None:
        self._values.pop((connection_id, name), None)
