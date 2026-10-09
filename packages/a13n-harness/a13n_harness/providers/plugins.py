"""Inert immutable manifests loaded only for explicitly selected installed plugins."""

from __future__ import annotations

import importlib.metadata
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from a13n_environment.definition import EnvironmentProviderDefinition

PROVIDER_API_VERSION = 2
ENTRY_POINT_GROUP = "a13n_harness.providers.plugins"


@dataclass(frozen=True, slots=True)
class ProviderManifest:
    api_version: int
    environment: tuple[EnvironmentProviderDefinition[Any, Any, Any, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.api_version != PROVIDER_API_VERSION:
            raise ValueError(f"unsupported Provider API version {self.api_version!r}")
        if not isinstance(self.environment, tuple) or not all(
            isinstance(item, EnvironmentProviderDefinition) for item in self.environment
        ):
            raise TypeError("Environment definitions must be an immutable tuple")


@dataclass(frozen=True, slots=True)
class LoadedProviderPlugin:
    entry_point: str
    distribution_name: str
    distribution_version: str
    import_target: str
    manifest: ProviderManifest


def selected_entry_points(enabled: Iterable[str]) -> tuple[importlib.metadata.EntryPoint, ...]:
    selected = tuple(enabled)
    if len(set(selected)) != len(selected):
        raise ValueError("duplicate Provider plugin entry-point name")
    if any(re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) is None for name in selected):
        raise ValueError("invalid Provider plugin entry-point name")
    available: dict[str, list[importlib.metadata.EntryPoint]] = {}
    if selected:
        for entry in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
            if entry.name in selected:
                available.setdefault(entry.name, []).append(entry)
    entries = []
    for name in selected:
        matches = available.get(name, [])
        if not matches:
            raise ValueError(f"selected Provider plugin {name!r} is not installed")
        if len(matches) != 1:
            raise ValueError(f"selected Provider plugin {name!r} is ambiguous")
        entries.append(matches[0])
    return tuple(entries)


def load_provider_plugins(enabled: Iterable[str]) -> tuple[LoadedProviderPlugin, ...]:
    """Import only the selected plugins; the Host's ProviderCatalog owns unique types."""
    plugins = []
    for entry in selected_entry_points(enabled):
        manifest = entry.load()
        if not isinstance(manifest, ProviderManifest):
            raise TypeError(f"Provider plugin {entry.name!r} must export a ProviderManifest")
        plugins.append(
            LoadedProviderPlugin(
                entry.name,
                entry.dist.metadata["Name"] if entry.dist else "unknown",
                entry.dist.version if entry.dist else "unknown",
                entry.value,
                manifest,
            )
        )
    return tuple(plugins)
