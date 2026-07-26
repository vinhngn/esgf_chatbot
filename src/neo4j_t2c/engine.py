"""Public engine façade over a replaceable Text-to-Cypher backend."""

from __future__ import annotations

from collections.abc import Mapping

from neo4j_t2c.adapters.legacy import LegacyServiceBackend
from neo4j_t2c.contracts import Text2CypherRequest, Text2CypherResult
from neo4j_t2c.ports import Text2CypherBackend


class Text2CypherEngine:
    """Generate and execute Cypher through a small, stable public interface."""

    def __init__(
        self,
        service: Text2CypherBackend | None = None,
    ) -> None:
        self._backend = service or LegacyServiceBackend()

    def generate(
        self,
        request: Text2CypherRequest | str,
        *,
        schema: str = "",
    ) -> Text2CypherResult:
        """Process one request through the configured Text-to-Cypher backend."""
        if isinstance(request, Text2CypherRequest):
            if schema:
                raise ValueError(
                    "Pass schema through Text2CypherRequest or the schema argument, not both"
                )
            normalized = request
        else:
            normalized = Text2CypherRequest(question=request, schema=schema)

        payload = self._backend(normalized.question, normalized.schema_text)
        if not isinstance(payload, Mapping):
            raise TypeError("Text-to-Cypher backend must return a mapping")
        return Text2CypherResult.from_service_payload(payload)

    def invoke(
        self,
        request: Text2CypherRequest | str,
        *,
        schema: str = "",
    ) -> Text2CypherResult:
        """LangChain-style alias for :meth:`generate`."""
        return self.generate(request, schema=schema)
