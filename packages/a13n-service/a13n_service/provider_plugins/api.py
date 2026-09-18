"""Small typed registration surface supported for external Provider packages."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from a13n_environment import EnvironmentProvider

PROVIDER_EXTENSION_API_VERSION = 1


class _DomainRegistry[T]:
    def __init__(self, label: str, value_type: type[T], key: Callable[[T], str]) -> None:
        self._label = label
        self._value_type = value_type
        self._key = key
        self._values: dict[str, T] = {}

    def register(self, value: T) -> None:
        if not isinstance(value, self._value_type):
            raise TypeError(f"{self._label} Provider registration has an invalid type")
        key = self._key(value)
        if key in self._values:
            raise ValueError(f"duplicate {self._label} Provider type {key!r}")
        self._values[key] = value

    def values(self) -> tuple[T, ...]:
        return tuple(self._values[key] for key in sorted(self._values))


class ProviderPluginRegistry:
    """Mutable only while one selected entry point registers inert definitions."""

    def __init__(self, *, api_version: int) -> None:
        if api_version != PROVIDER_EXTENSION_API_VERSION:
            raise ValueError(
                f"unsupported Provider extension API version {api_version}; expected {PROVIDER_EXTENSION_API_VERSION}"
            )
        self.environment = _DomainRegistry[EnvironmentProvider](
            "Environment", EnvironmentProvider, lambda item: item.key
        )


class ProviderRegister(Protocol):
    a13n_provider_api_version: int

    def __call__(self, registry: ProviderPluginRegistry) -> None: ...


def provider_plugin(*, api_version: int) -> Callable[[Callable[[ProviderPluginRegistry], None]], ProviderRegister]:
    """Declare the exact extension API version implemented by one entry point."""

    def declare(register: Callable[[ProviderPluginRegistry], None]) -> ProviderRegister:
        versioned = cast(ProviderRegister, register)
        versioned.a13n_provider_api_version = api_version
        return versioned

    return declare
