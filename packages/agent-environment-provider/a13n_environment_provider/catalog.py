from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from importlib.metadata import entry_points

from .errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
)
from .management import EnvironmentProvider

ENVIRONMENT_PROVIDER_ENTRY_POINT_GROUP = "a13n_environment_provider.providers"
_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")


class EnvironmentProviderCatalog:
    """Explicit allowlisted catalog of inert Environment Providers."""

    def __init__(self, providers: Iterable[EnvironmentProvider] = ()) -> None:
        self._providers: dict[str, EnvironmentProvider] = {}
        for provider in providers:
            self.register(provider)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def register(self, provider: EnvironmentProvider) -> None:
        if not isinstance(provider, EnvironmentProvider):
            raise TypeError("provider must be an EnvironmentProvider")
        key = provider.key
        if not _KEY_PATTERN.fullmatch(key):
            raise _catalog_error("Environment Provider key is invalid.", key)
        if key in self._providers:
            raise _catalog_error("Environment Provider key is already registered.", key)
        self._providers[key] = provider

    def resolve(self, provider_key: str) -> EnvironmentProvider:
        try:
            return self._providers[provider_key]
        except KeyError as error:
            raise _catalog_error("Environment Provider is not registered.", provider_key) from error

    def load_entry_points(self, *, enabled_keys: Collection[str]) -> None:
        enabled = frozenset(enabled_keys)
        selected = {
            point.name: point
            for point in entry_points(group=ENVIRONMENT_PROVIDER_ENTRY_POINT_GROUP)
            if point.name in enabled
        }
        missing = enabled - selected.keys()
        if missing:
            raise _catalog_error("Enabled Environment Provider entry point is missing.", sorted(missing)[0])
        for key in sorted(enabled):
            point = selected[key]
            try:
                loaded = point.load()
                provider = loaded() if isinstance(loaded, type) else loaded
            except BaseException as error:
                raise _catalog_error("Environment Provider entry point failed to load.", key) from error
            if not isinstance(provider, EnvironmentProvider) or provider.key != point.name:
                raise _catalog_error("Environment Provider entry point returned an invalid provider.", key)
            self.register(provider)


def build_environment_provider_catalog(
    *,
    builtin_keys: Collection[str] = (),
    extension_keys: Collection[str] = (),
) -> EnvironmentProviderCatalog:
    from .direct_local.provider import DirectLocalEnvironmentProvider
    from .docker.provider import DockerEnvironmentProvider
    from .local_envd.provider import LocalEnvdEnvironmentProvider

    builtins: dict[str, EnvironmentProvider] = {
        "a13n.direct-local": DirectLocalEnvironmentProvider(),
        "a13n.local-envd": LocalEnvdEnvironmentProvider(),
        "a13n.docker": DockerEnvironmentProvider(),
    }
    unknown = set(builtin_keys) - builtins.keys()
    if unknown:
        raise _catalog_error("Unknown built-in Environment Provider key.", sorted(unknown)[0])
    catalog = EnvironmentProviderCatalog(builtins[key] for key in builtin_keys)
    if extension_keys:
        catalog.load_entry_points(enabled_keys=extension_keys)
    return catalog


def _catalog_error(message: str, key: str) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        message,
        code="provider_catalog_invalid",
        category=EnvironmentProviderErrorCategory.INVALID,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        context=EnvironmentProviderErrorContext(provider_key=key if _KEY_PATTERN.fullmatch(key) else None),
    )
