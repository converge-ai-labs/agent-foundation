"""The one immutable index of the Provider definitions a host selected for a domain."""

from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType
from typing import ClassVar, Protocol


class CatalogDefinition(Protocol):
    DOMAIN: ClassVar[str]

    @property
    def type(self) -> str: ...


class ProviderNotSelected(ValueError):
    """A referenced Provider type is not selected by this deployment."""


class ProviderCatalog[D: CatalogDefinition](Mapping[str, D]):
    """Own the unique-type-per-domain rule; selection never opens a Provider."""

    def __init__(self, definitions: Iterable[D] = ()) -> None:
        selected: dict[str, D] = {}
        for definition in definitions:
            if definition.type in selected:
                raise ValueError(f"duplicate {definition.DOMAIN} Provider type {definition.type!r}")
            selected[definition.type] = definition
        self._definitions = MappingProxyType(selected)

    def __getitem__(self, provider_type: str) -> D:
        return self._definitions[provider_type]

    def __iter__(self) -> Iterator[str]:
        return iter(self._definitions)

    def __len__(self) -> int:
        return len(self._definitions)

    def require(self, provider_type: str) -> D:
        try:
            return self._definitions[provider_type]
        except KeyError as error:
            raise ProviderNotSelected(f"Provider type {provider_type!r} is not selected") from error
