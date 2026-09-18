"""Immutable host-selected Memory definitions; selection never opens backends."""

from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType

from .definition import MemoryProviderDefinition


class MemoryProviderCatalog(Mapping[str, MemoryProviderDefinition]):
    def __init__(self, definitions: Iterable[MemoryProviderDefinition] = ()) -> None:
        selected = {}
        for definition in definitions:
            if not isinstance(definition, MemoryProviderDefinition):
                raise TypeError("Memory entries must be MemoryProviderDefinition values")
            if definition.type in selected:
                raise ValueError(f"duplicate Memory Provider type {definition.type!r}")
            selected[definition.type] = definition
        self._definitions = MappingProxyType(selected)

    def __getitem__(self, key: str) -> MemoryProviderDefinition:
        return self._definitions[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._definitions)

    def __len__(self) -> int:
        return len(self._definitions)
