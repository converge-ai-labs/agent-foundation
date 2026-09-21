"""Explicit distribution registry of cohesive native providers."""

from functools import partial

from ..adapters import IngressAdapter
from ..composition import AdapterDefinition, AdapterRegistry
from ..subscriptions import EventSubscriptions
from .common.origins import normalize_provider_origins
from .definition import NativeProvider
from .github.definition import PROVIDER as GITHUB
from .lark.definition import PROVIDER as LARK
from .slack.definition import PROVIDER as SLACK

_PROVIDERS = {provider.key: provider for provider in (SLACK, LARK, GITHUB)}


def require_native_provider(key: str) -> NativeProvider:
    try:
        return _PROVIDERS[key]
    except KeyError as error:
        raise ValueError("native_provider_unavailable") from error


def built_in_ingress_adapter_registry(
    *, allowed_provider_origins: tuple[str, ...] = ()
) -> AdapterRegistry[IngressAdapter]:
    origins = tuple(normalize_provider_origins(allowed_provider_origins))
    return AdapterRegistry(
        AdapterDefinition[IngressAdapter](
            key=p.key, config_versions=p.config_versions, factory=partial(p.ingress, origins)
        )
        for p in _PROVIDERS.values()
    )


def event_subscription_adapters() -> dict[tuple[str, str], EventSubscriptions]:
    return {
        (provider.key, version): provider.event_subscriptions
        for provider in _PROVIDERS.values()
        if provider.event_subscriptions is not None
        for version in provider.event_subscriptions.config_versions
        if version in provider.config_versions
    }
