"""Immutable host-selected Connector definitions; selection never opens backends."""

from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType

from .definition import ConnectorProviderDefinition


class ConnectorProviderCatalog(Mapping[str, ConnectorProviderDefinition]):
    def __init__(self, definitions: Iterable[ConnectorProviderDefinition] = ()) -> None:
        selected = {}
        for definition in definitions:
            if not isinstance(definition, ConnectorProviderDefinition):
                raise TypeError("Connector entries must be ConnectorProviderDefinition values")
            if definition.type in selected:
                raise ValueError(f"duplicate Connector Provider type {definition.type!r}")
            selected[definition.type] = definition
        self._definitions = MappingProxyType(selected)

    def __getitem__(self, key: str) -> ConnectorProviderDefinition:
        return self._definitions[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._definitions)

    def __len__(self) -> int:
        return len(self._definitions)
