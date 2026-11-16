"""Trusted installed App declarations. Only assembly imports callbacks; business layers use metadata."""

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from types import MappingProxyType

from fastapi import APIRouter

from a13n_service.app_plugins import AppManifest, Frontend
from a13n_service.runs.runtime import Runtime
from a13n_service.settings import Section


@dataclass(frozen=True)
class OrganizationSweep:
    """One bounded organization work unit. The host selects tenants; the callback owns replica coordination."""

    name: str
    every: float
    timeout: float
    run: Callable[[Runtime, str], Awaitable[object]]


@dataclass(frozen=True)
class AppDefinition:
    app_id: str
    name: str
    factory_keys: frozenset[str] = frozenset()
    routers: tuple[APIRouter, ...] = ()
    settings: Mapping[str, type[Section]] = field(default_factory=dict)
    sweeps: tuple[OrganizationSweep, ...] = ()
    startup: Callable[[Runtime, AsyncExitStack], Awaitable[None]] | None = None
    frontend: Frontend | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "factory_keys", frozenset(self.factory_keys))
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))


@dataclass(frozen=True)
class InstalledApp:
    definition: AppDefinition
    distribution: str
    version: str

    @property
    def app_id(self) -> str:
        return self.definition.app_id

    @property
    def manifest(self) -> AppManifest:
        return AppManifest(
            app_id=self.app_id,
            name=self.definition.name,
            distribution=self.distribution,
            version=self.version,
            factory_keys=self.definition.factory_keys,
            frontend=self.definition.frontend,
        )


def discover_apps() -> tuple[InstalledApp, ...]:
    """Load each installed definition once per explicit build, including disabled Apps."""
    apps = []
    for entry in sorted(entry_points(group="a13n_service.app_plugins"), key=lambda item: item.name):
        definition = entry.load()()
        if not isinstance(definition, AppDefinition) or definition.app_id != entry.name or entry.dist is None:
            raise ValueError(f"App entry point must return its named AppDefinition: {entry.name}")
        apps.append(InstalledApp(definition, entry.dist.name, entry.dist.version))
    return tuple(apps)
