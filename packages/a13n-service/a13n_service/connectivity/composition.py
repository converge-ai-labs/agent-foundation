"""Explicit, copyable Connectivity adapter registration."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol, TypeVar

_ADAPTER_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

AdapterT = TypeVar("AdapterT")


class AdapterResolver[AdapterT](Protocol):
    def create(self, key: str, *, config_version: str) -> AdapterT: ...

    def versions(self) -> tuple[tuple[str, str], ...]: ...


@dataclass(frozen=True, slots=True)
class AdapterDefinition[AdapterT]:
    """One trusted adapter factory and its accepted configuration versions."""

    key: str
    config_versions: frozenset[str]
    factory: Callable[[], AdapterT]


class AdapterRegistry[AdapterT]:
    """Distribution-owned adapter allowlist with explicit duplicate handling."""

    def __init__(self, definitions: Iterable[AdapterDefinition[AdapterT]] = ()) -> None:
        self._definitions: dict[str, AdapterDefinition[AdapterT]] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: AdapterDefinition[AdapterT]) -> None:
        if _ADAPTER_KEY.fullmatch(definition.key) is None:
            raise ValueError("adapter key must be lowercase ASCII with optional digits or underscores")
        if not definition.config_versions or any(
            _ADAPTER_KEY.fullmatch(version) is None for version in definition.config_versions
        ):
            raise ValueError("adapter must declare stable configuration versions")
        if definition.key in self._definitions:
            raise ValueError(f"adapter key is already registered: {definition.key}")
        self._definitions[definition.key] = definition

    def create(self, key: str, *, config_version: str) -> AdapterT:
        definition = self._definitions.get(key)
        if definition is None:
            raise ValueError(f"adapter is not registered: {key}")
        if config_version not in definition.config_versions:
            raise ValueError(f"adapter configuration version is not registered: {key}@{config_version}")
        return definition.factory()

    def versions(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (key, version)
            for key, definition in sorted(self._definitions.items())
            for version in sorted(definition.config_versions)
        )

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def copy(self) -> AdapterRegistry[AdapterT]:
        return AdapterRegistry(self._definitions.values())
