"""Composable input aliases for concrete model configuration and settings."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any, cast

from pydantic_ai.settings import ModelSettings

from a13n_harness.spec import ModelConfiguration

type ModelConfigurationTransform = Callable[[ModelConfiguration], ModelConfiguration]
type ModelSettingsTransform = Callable[[ModelSettings], ModelSettings]


@dataclass(frozen=True, slots=True)
class ModelConfigurationAlias:
    """One provider-scoped input alias that transforms model configuration."""

    key: str
    provider: str
    transform: ModelConfigurationTransform

    def __post_init__(self) -> None:
        _validate_alias(self.key, self.provider, self.transform, kind="configuration")


@dataclass(frozen=True, slots=True)
class ModelSettingsAlias:
    """One provider-scoped input alias that transforms concrete settings."""

    key: str
    provider: str
    transform: ModelSettingsTransform

    def __post_init__(self) -> None:
        _validate_alias(self.key, self.provider, self.transform, kind="settings")


def _validate_alias(key: str, provider: str, transform: Callable[..., object], *, kind: str) -> None:
    if not isinstance(key, str) or not key or key != key.strip() or ":" not in key or "\x00" in key:
        raise ValueError(f"model {kind} alias key must be a namespaced non-blank string")
    if (
        not isinstance(provider, str)
        or not provider
        or provider != provider.strip()
        or any(character in provider for character in ":@\x00")
    ):
        raise ValueError(f"model {kind} alias provider must be an unqualified non-blank string")
    if not callable(transform):
        raise TypeError(f"model {kind} alias transform must be callable")


class ModelConfigurationAliasCatalog(Mapping[str, ModelConfigurationAlias]):
    """Immutable model-configuration aliases selected before Agent construction."""

    def __init__(self, entries: Mapping[str, ModelConfigurationAlias]) -> None:
        copied: dict[str, ModelConfigurationAlias] = {}
        for key, entry in entries.items():
            if not isinstance(key, str) or not isinstance(entry, ModelConfigurationAlias):
                raise TypeError("model configuration alias catalog must map strings to ModelConfigurationAlias values")
            if key != entry.key:
                raise ValueError("model configuration alias catalog keys must match entry keys")
            copied[key] = entry
        self._entries = MappingProxyType(copied)

    def __getitem__(self, key: str) -> ModelConfigurationAlias:
        return self._entries[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[ModelConfigurationAlias, ...]:
        """Return aliases in canonical key order."""
        return tuple(self._entries[key] for key in sorted(self._entries))

    def with_updates(
        self,
        updates: Mapping[str, ModelConfigurationAlias],
    ) -> ModelConfigurationAliasCatalog:
        """Return an immutable catalog after shallow alias replacement."""
        merged = dict(self._entries)
        for key, entry in updates.items():
            if not isinstance(key, str) or not isinstance(entry, ModelConfigurationAlias):
                raise TypeError("model configuration alias updates must map strings to ModelConfigurationAlias values")
            if key != entry.key:
                raise ValueError("model configuration alias update key must match its entry")
            merged[key] = entry
        return ModelConfigurationAliasCatalog(merged)


class ModelSettingsAliasCatalog(Mapping[str, ModelSettingsAlias]):
    """Immutable model-settings aliases selected before Agent construction."""

    def __init__(self, entries: Mapping[str, ModelSettingsAlias]) -> None:
        copied: dict[str, ModelSettingsAlias] = {}
        for key, entry in entries.items():
            if not isinstance(key, str) or not isinstance(entry, ModelSettingsAlias):
                raise TypeError("model settings alias catalog must map strings to ModelSettingsAlias values")
            if key != entry.key:
                raise ValueError("model settings alias catalog keys must match entry keys")
            copied[key] = entry
        self._entries = MappingProxyType(copied)

    def __getitem__(self, key: str) -> ModelSettingsAlias:
        return self._entries[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[ModelSettingsAlias, ...]:
        """Return aliases in canonical key order."""
        return tuple(self._entries[key] for key in sorted(self._entries))

    def with_updates(self, updates: Mapping[str, ModelSettingsAlias]) -> ModelSettingsAliasCatalog:
        """Return an immutable catalog after shallow alias replacement."""
        merged = dict(self._entries)
        for key, entry in updates.items():
            if not isinstance(key, str) or not isinstance(entry, ModelSettingsAlias):
                raise TypeError("model settings alias updates must map strings to ModelSettingsAlias values")
            if key != entry.key:
                raise ValueError("model settings alias update key must match its entry")
            merged[key] = entry
        return ModelSettingsAliasCatalog(merged)


def _context_window(tokens: int) -> ModelConfigurationTransform:
    def transform(configuration: ModelConfiguration) -> ModelConfiguration:
        return configuration.model_copy(update={"context_window": tokens})

    return transform


def _max_output_tokens(tokens: int) -> ModelSettingsTransform:
    def transform(settings: ModelSettings) -> ModelSettings:
        resolved: dict[str, Any] = dict(settings)
        resolved["max_tokens"] = tokens
        return cast(ModelSettings, resolved)

    return transform


def _anthropic_interleaved_thinking(settings: ModelSettings) -> ModelSettings:
    resolved: dict[str, Any] = dict(settings)
    resolved["anthropic_thinking"] = {"type": "adaptive"}
    return cast(ModelSettings, resolved)


def _anthropic_thinking_disabled(settings: ModelSettings) -> ModelSettings:
    resolved: dict[str, Any] = dict(settings)
    resolved["anthropic_thinking"] = {"type": "disabled"}
    resolved.pop("anthropic_effort", None)
    return cast(ModelSettings, resolved)


@lru_cache(maxsize=1)
def get_model_configuration_alias_catalog() -> ModelConfigurationAliasCatalog:
    """Return release-pinned built-in model-configuration aliases."""
    entries = {
        "anthropic:context-200k": ModelConfigurationAlias(
            key="anthropic:context-200k",
            provider="anthropic",
            transform=_context_window(200_000),
        ),
        "anthropic:context-400k": ModelConfigurationAlias(
            key="anthropic:context-400k",
            provider="anthropic",
            transform=_context_window(400_000),
        ),
        "anthropic:context-1m": ModelConfigurationAlias(
            key="anthropic:context-1m",
            provider="anthropic",
            transform=_context_window(1_000_000),
        ),
    }
    return ModelConfigurationAliasCatalog(entries)


@lru_cache(maxsize=1)
def get_model_settings_alias_catalog() -> ModelSettingsAliasCatalog:
    """Return the release-pinned built-in model-settings aliases."""
    entries = {
        "anthropic:interleaved-thinking": ModelSettingsAlias(
            key="anthropic:interleaved-thinking",
            provider="anthropic",
            transform=_anthropic_interleaved_thinking,
        ),
        "anthropic:max-output-32k": ModelSettingsAlias(
            key="anthropic:max-output-32k",
            provider="anthropic",
            transform=_max_output_tokens(32_768),
        ),
        "anthropic:max-output-64k": ModelSettingsAlias(
            key="anthropic:max-output-64k",
            provider="anthropic",
            transform=_max_output_tokens(65_536),
        ),
        "anthropic:max-output-128k": ModelSettingsAlias(
            key="anthropic:max-output-128k",
            provider="anthropic",
            transform=_max_output_tokens(131_072),
        ),
        "anthropic:thinking-disabled": ModelSettingsAlias(
            key="anthropic:thinking-disabled",
            provider="anthropic",
            transform=_anthropic_thinking_disabled,
        ),
    }
    return ModelSettingsAliasCatalog(entries)


def _copy_configuration(configuration: ModelConfiguration) -> ModelConfiguration:
    return ModelConfiguration.model_validate(configuration.model_dump())


def _copy_settings(settings: Mapping[str, Any]) -> ModelSettings:
    return cast(ModelSettings, deepcopy(dict(settings)))


def _model_provider(model: str, *, kind: str) -> str:
    if not isinstance(model, str):
        raise TypeError(f"model must be a provider-qualified string when resolving {kind} aliases")
    if not model or model != model.strip() or "\x00" in model:
        raise ValueError(f"model must be a provider-qualified string when resolving {kind} aliases")

    gateway, separator, route = model.partition("@")
    if separator:
        if not gateway or gateway != gateway.strip() or "@" in route:
            raise ValueError("gateway model strings must use format gateway@provider:model")
    else:
        route = gateway

    provider, provider_separator, model_name = route.partition(":")
    if (
        not provider_separator
        or not provider
        or provider != provider.strip()
        or not model_name
        or model_name != model_name.strip()
        or ":" in provider
    ):
        raise ValueError(f"model {kind} aliases require format provider:model or gateway@provider:model")
    return provider


def _alias_keys(aliases: Sequence[str], *, kind: str) -> tuple[str, ...]:
    if isinstance(aliases, (str, bytes)):
        raise TypeError(f"model {kind} aliases must be a sequence of strings, not one string")
    selected_aliases = tuple(aliases)
    if not all(isinstance(alias, str) for alias in selected_aliases):
        raise TypeError(f"model {kind} aliases must be strings")
    return selected_aliases


def resolve_model_configuration(
    model: str,
    *,
    aliases: Sequence[str] = (),
    overrides: ModelConfiguration | None = None,
    catalog: ModelConfigurationAliasCatalog | None = None,
) -> ModelConfiguration | None:
    """Resolve ordered aliases and concrete overrides to model configuration."""
    selected_aliases = _alias_keys(aliases, kind="configuration")
    selected_catalog = get_model_configuration_alias_catalog() if catalog is None else catalog
    if not isinstance(selected_catalog, ModelConfigurationAliasCatalog):
        raise TypeError("catalog must be a ModelConfigurationAliasCatalog")

    resolved: ModelConfiguration | None = None
    if selected_aliases:
        provider = _model_provider(model, kind="configuration")
        resolved = ModelConfiguration()
        for key in selected_aliases:
            try:
                entry = selected_catalog[key]
            except KeyError as exc:
                raise ValueError(f"unknown model configuration alias: {key!r}") from exc
            if entry.provider != provider:
                raise ValueError(
                    f"model configuration alias {key!r} requires provider {entry.provider!r}, got {provider!r}"
                )
            transformed = entry.transform(_copy_configuration(resolved))
            if not isinstance(transformed, ModelConfiguration):
                raise TypeError(f"model configuration alias {key!r} transform must return ModelConfiguration")
            resolved = _copy_configuration(transformed)

    if overrides is not None:
        if not isinstance(overrides, ModelConfiguration):
            raise TypeError("model configuration overrides must be ModelConfiguration or None")
        if resolved is None:
            return _copy_configuration(overrides)
        values = resolved.model_dump()
        values.update(deepcopy(overrides.model_dump(exclude_unset=True)))
        resolved = ModelConfiguration.model_validate(values)
    return resolved


def resolve_model_settings(
    model: str,
    *,
    aliases: Sequence[str] = (),
    overrides: ModelSettings | None = None,
    catalog: ModelSettingsAliasCatalog | None = None,
) -> ModelSettings:
    """Resolve ordered input aliases and concrete overrides to native settings."""
    selected_aliases = _alias_keys(aliases, kind="settings")

    resolved = ModelSettings()
    selected_catalog = get_model_settings_alias_catalog() if catalog is None else catalog
    if not isinstance(selected_catalog, ModelSettingsAliasCatalog):
        raise TypeError("catalog must be a ModelSettingsAliasCatalog")

    if selected_aliases:
        provider = _model_provider(model, kind="settings")
        for key in selected_aliases:
            try:
                entry = selected_catalog[key]
            except KeyError as exc:
                raise ValueError(f"unknown model settings alias: {key!r}") from exc
            if entry.provider != provider:
                raise ValueError(f"model settings alias {key!r} requires provider {entry.provider!r}, got {provider!r}")
            transformed = entry.transform(_copy_settings(resolved))
            if not isinstance(transformed, dict):
                raise TypeError(f"model settings alias {key!r} transform must return ModelSettings")
            resolved = _copy_settings(transformed)

    if overrides is not None:
        if not isinstance(overrides, dict):
            raise TypeError("model settings overrides must be ModelSettings or None")
        resolved.update(_copy_settings(overrides))
    return _copy_settings(resolved)


__all__ = [
    "ModelConfigurationAlias",
    "ModelConfigurationAliasCatalog",
    "ModelConfigurationTransform",
    "ModelSettingsAlias",
    "ModelSettingsAliasCatalog",
    "ModelSettingsTransform",
    "get_model_configuration_alias_catalog",
    "get_model_settings_alias_catalog",
    "resolve_model_configuration",
    "resolve_model_settings",
]
