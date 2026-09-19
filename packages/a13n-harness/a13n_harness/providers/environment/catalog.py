"""Immutable selected Environment definitions; plugin loading belongs to providers.plugins."""

from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType

from .definition import EnvironmentProviderDefinition
from .errors import EnvironmentProviderErrorCategory, provider_error


class EnvironmentProviderCatalog(Mapping[str, EnvironmentProviderDefinition]):
    def __init__(self, definitions: Iterable[EnvironmentProviderDefinition] = ()) -> None:
        selected = {}
        for definition in definitions:
            if not isinstance(definition, EnvironmentProviderDefinition):
                raise TypeError("Environment entries must be EnvironmentProviderDefinition values")
            if definition.type in selected:
                raise ValueError(f"duplicate Environment Provider type {definition.type!r}")
            selected[definition.type] = definition
        self._definitions = MappingProxyType(selected)

    def __getitem__(self, key: str) -> EnvironmentProviderDefinition:
        return self._definitions[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._definitions)

    def __len__(self) -> int:
        return len(self._definitions)

    def require(self, key: str) -> EnvironmentProviderDefinition:
        """Fail safely when a deployment no longer selects a referenced Provider type."""
        try:
            return self._definitions[key]
        except KeyError as error:
            raise provider_error(key, "provider_not_selected", EnvironmentProviderErrorCategory.MISSING) from error
