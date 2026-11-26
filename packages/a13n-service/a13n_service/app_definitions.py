"""Trusted installed App declarations. Only assembly imports callbacks; business layers use metadata."""

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from types import MappingProxyType
from typing import Literal

from fastapi import APIRouter

from a13n_service.app_database import AppDatabase
from a13n_service.app_plugins import AppManifest, Frontend
from a13n_service.app_sdk.hooks import EnvironmentLifecycleHook, EnvironmentRunHook, HookScope, validate_hook
from a13n_service.runs.environments.events import EnvironmentEvent, EventKind
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Sealed
from a13n_service.runs.seal_events import RunSealEvent
from a13n_service.settings import Section


@dataclass(frozen=True)
class OrganizationSweep:
    """One organization callback with its own timeout; the host selects tenants in bounded passes."""

    name: str
    every: float
    timeout: float
    run: Callable[[Runtime, str], Awaitable[object]]


@dataclass(frozen=True)
class RunSealSubscription:
    """A bounded best-effort callback for committed Run seal hints."""

    run: Callable[[Runtime, RunSealEvent], Awaitable[object]]
    timeout: float = 5.0
    scope: Literal["app", "organization"] = "app"
    statuses: frozenset[Sealed] = frozenset({"completed", "failed", "cancelled"})

    def __post_init__(self) -> None:
        if (
            self.scope not in ("app", "organization")
            or not 0 < self.timeout <= 30
            or not self.statuses
            or not self.statuses <= {"completed", "failed", "cancelled", "waiting"}
        ):
            raise ValueError("Invalid Run seal subscription")
        object.__setattr__(self, "statuses", frozenset(self.statuses))


@dataclass(frozen=True)
class EnvironmentEventSubscription:
    """A bounded best-effort callback for committed Environment transitions."""

    run: Callable[[Runtime, EnvironmentEvent], Awaitable[object]]
    timeout: float = 5.0
    scope: HookScope = "app"
    events: frozenset[EventKind] = frozenset({"ready", "stopped", "deleted", "lost", "recreated"})

    def __post_init__(self) -> None:
        validate_hook(self.scope, self.timeout)
        if not self.events or not self.events <= {"ready", "stopped", "deleted", "lost", "recreated"}:
            raise ValueError("Invalid Environment event subscription")
        object.__setattr__(self, "events", frozenset(self.events))


@dataclass(frozen=True)
class AppDefinition:
    app_id: str
    name: str
    factory_keys: frozenset[str] = frozenset()
    routers: tuple[APIRouter, ...] = ()
    webhook_routers: tuple[APIRouter, ...] = ()
    settings: Mapping[str, type[Section]] = field(default_factory=dict)
    sweeps: tuple[OrganizationSweep, ...] = ()
    run_seals: tuple[RunSealSubscription, ...] = ()
    environment_hooks: tuple[EnvironmentLifecycleHook, ...] = ()
    environment_run_hooks: tuple[EnvironmentRunHook, ...] = ()
    environment_events: tuple[EnvironmentEventSubscription, ...] = ()
    startup: Callable[[Runtime, AsyncExitStack], Awaitable[None]] | None = None
    frontend: Frontend | None = None
    database: AppDatabase | None = None

    def __post_init__(self) -> None:
        phases = {hook.phase for hook in self.environment_hooks}
        if len(phases) != len(self.environment_hooks):
            raise ValueError("An App can declare at most one hook per Environment lifecycle phase")
        if self.database is not None:
            if self.database.app_id != self.app_id:
                raise ValueError("App database identity must match its AppDefinition")
            self.database.validate()
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
            has_run_seal_subscribers=bool(self.definition.run_seals),
        )


def app_databases(apps: tuple[InstalledApp, ...]) -> tuple[AppDatabase, ...]:
    databases = tuple(app.definition.database for app in apps if app.definition.database is not None)
    prefixes: set[str | None] = set()
    for database in databases:
        database.validate()
        if database.prefix in prefixes:
            raise ValueError(f"Duplicate App database prefix: {database.prefix}")
        prefixes.add(database.prefix)
    return databases


def discover_apps() -> tuple[InstalledApp, ...]:
    """Load each installed definition once per explicit build, including disabled Apps."""
    apps = []
    for entry in sorted(entry_points(group="a13n_service.app_plugins"), key=lambda item: item.name):
        definition = entry.load()()
        if not isinstance(definition, AppDefinition) or definition.app_id != entry.name or entry.dist is None:
            raise ValueError(f"App entry point must return its named AppDefinition: {entry.name}")
        apps.append(InstalledApp(definition, entry.dist.name, entry.dist.version))
    return tuple(apps)
