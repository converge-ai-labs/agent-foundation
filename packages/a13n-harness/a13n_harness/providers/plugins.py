"""Inert immutable manifests loaded only for explicitly selected installed plugins."""

from __future__ import annotations

import importlib.metadata
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .web.definition import WebProviderDefinition

PROVIDER_API_VERSION = 1
ENTRY_POINT_GROUP = "a13n.providers"


@dataclass(frozen=True, slots=True)
class ProviderManifest:
    api_version: int
    web: tuple[WebProviderDefinition[Any, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.api_version != PROVIDER_API_VERSION:
            raise ValueError(f"unsupported Provider API version {self.api_version!r}")
        if not isinstance(self.web, tuple) or not all(isinstance(item, WebProviderDefinition) for item in self.web):
            raise TypeError("Web definitions must be an immutable tuple")
        if len({item.type for item in self.web}) != len(self.web):
            raise ValueError("duplicate Web Provider type")


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
    plugins = []
    seen: set[str] = set()
    for entry in selected_entry_points(enabled):
        manifest = entry.load()
        if not isinstance(manifest, ProviderManifest):
            raise TypeError(f"Provider plugin {entry.name!r} must export a ProviderManifest")
        for definition in manifest.web:
            if definition.type in seen:
                raise ValueError(f"duplicate Web Provider type {definition.type!r}")
            seen.add(definition.type)
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
