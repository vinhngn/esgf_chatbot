"""Concrete adapters, imported lazily to keep the core package lightweight."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "CallableTraceSink",
    "JsonProfileStore",
    "InjectedPipelineBackend",
    "LangChainNeo4jClient",
    "LegacyServiceBackend",
    "NullTraceSink",
]

_ADAPTER_MODULES = {
    "CallableTraceSink": "neo4j_t2c.adapters.tracing",
    "JsonProfileStore": "neo4j_t2c.adapters.profiles",
    "InjectedPipelineBackend": "neo4j_t2c.adapters.pipeline",
    "LangChainNeo4jClient": "neo4j_t2c.adapters.neo4j",
    "LegacyServiceBackend": "neo4j_t2c.adapters.legacy",
    "NullTraceSink": "neo4j_t2c.adapters.tracing",
}


def __getattr__(name: str) -> Any:
    module_name = _ADAPTER_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
