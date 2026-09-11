"""Trusted process-local Trace Query provider registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .domain import (
    ObservationCollection,
    ProviderTraceQuery,
    ProviderTraceRead,
    Trace,
    TraceCollection,
    TraceQueryCapabilities,
)


class TraceQueryProvider(Protocol):
    @property
    def capabilities(self) -> TraceQueryCapabilities: ...

    @property
    def cursor_namespace(self) -> str: ...

    async def list_traces(self, query: ProviderTraceQuery) -> TraceCollection: ...

    async def get_trace(self, query: ProviderTraceRead) -> Trace | None: ...

    async def list_observations(self, query: ProviderTraceRead) -> ObservationCollection: ...


TraceQueryProviderFactory = Callable[[], TraceQueryProvider]


class TraceQueryProviderRegistry:
    """Distribution-fixed provider factories with duplicate-key rejection."""

    def __init__(self) -> None:
        self._factories: dict[str, TraceQueryProviderFactory] = {}

    def register(self, key: str, factory: TraceQueryProviderFactory) -> None:
        if (
            not key
            or key == "none"
            or not key.isascii()
            or not key[0].isalpha()
            or not all(character.islower() or character.isdigit() or character == "_" for character in key)
        ):
            raise ValueError("Trace Query provider key is invalid")
        if key in self._factories:
            raise ValueError(f"Trace Query provider key is already registered: {key}")
        self._factories[key] = factory

    def create(self, key: str) -> TraceQueryProvider:
        try:
            factory = self._factories[key]
        except KeyError as error:
            raise ValueError(f"Trace Query provider is not registered: {key}") from error
        return factory()

    def copy(self) -> TraceQueryProviderRegistry:
        """Return an isolated registry for one application composition."""

        registry = TraceQueryProviderRegistry()
        registry._factories.update(self._factories)
        return registry

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))
