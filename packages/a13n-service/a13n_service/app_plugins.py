"""Installed App metadata and immutable accepted activation; discovery is explicit at assembly.

Apps are trusted installed code. Business database histories remain independent of the Core graph.
Tenant work enters through host-owned dispatch; process resources live on the host exit stack.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from a13n_service.infra.errors import ServiceError, not_found


@dataclass(frozen=True)
class Frontend:
    """A complete prebuilt ESM asset tree inside an importable Python package."""

    package: str
    directory: str
    entry: str = "index.js"
    styles: tuple[str, ...] = ()
    bridge: int = 1

    def __post_init__(self) -> None:
        for path in (self.directory, self.entry, *self.styles):
            if not path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
                raise ValueError(f"App asset paths must be package-relative: {path}")
        if self.bridge != 1:
            raise ValueError(f"Unsupported Console App bridge: {self.bridge}")


@dataclass(frozen=True)
class AppManifest:
    """Detached process inventory: no executable callbacks or configuration values."""

    app_id: str
    name: str
    distribution: str
    version: str
    factory_keys: frozenset[str] = frozenset()
    frontend: Frontend | None = None
    has_run_seal_subscribers: bool = False

    def __post_init__(self) -> None:
        if len(self.app_id) > 128 or not re.fullmatch(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*", self.app_id):
            raise ValueError(f"Invalid App ID: {self.app_id}")
        object.__setattr__(self, "factory_keys", frozenset(self.factory_keys))

    def asset_url(self, path: str) -> str:
        return f"/api/v1/app-plugins/{self.app_id}/assets/{quote(self.version, safe='')}/{path}"


class AppPolicy(BaseModel):
    """The `app_plugins` namespace of Organization.settings. Missing entries are disabled."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: tuple[str, ...] = ()


class AcceptedApp(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    app_id: str
    distribution: str
    version: str
    factory_keys: tuple[str, ...]
    enabled: bool


class AppActivation(BaseModel):
    """Run-owned data, including disabled ownership. Empty legacy rows authorize no App factories."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    organization_version: int | None = None
    apps: tuple[AcceptedApp, ...] = ()

    def factory_enabled(self, key: str, installed: AppCatalog) -> bool:
        for app in self.apps:
            if key in app.factory_keys:
                return app.enabled
        # A newly installed/owned factory cannot acquire authority from an older accepted selection.
        return installed.owner(key) is None

    def require_installed(self, installed: AppCatalog) -> None:
        for accepted in self.apps:
            if not accepted.enabled:
                continue
            current = installed.find(accepted.app_id)
            if current is None or (
                current.distribution,
                current.version,
                tuple(sorted(current.factory_keys)),
            ) != (accepted.distribution, accepted.version, accepted.factory_keys):
                raise ServiceError(
                    "conflict",
                    "The worker's installed App does not match the accepted Run selection",
                    {"reason": "app_inventory_mismatch", "app_id": accepted.app_id},
                )


@dataclass(frozen=True)
class AppCatalog:
    apps: tuple[AppManifest, ...] = ()

    def __post_init__(self) -> None:
        ids: set[str] = set()
        keys: set[str] = set()
        for app in self.apps:
            if app.app_id in ids:
                raise ValueError(f"Duplicate App ID: {app.app_id}")
            if duplicate := keys & app.factory_keys:
                raise ValueError(f"Duplicate App factory ownership: {sorted(duplicate)}")
            ids.add(app.app_id)
            keys.update(app.factory_keys)

    def find(self, app_id: str) -> AppManifest | None:
        return next((app for app in self.apps if app.app_id == app_id), None)

    def require(self, app_id: str) -> AppManifest:
        if (app := self.find(app_id)) is None:
            raise not_found("app_plugin", app_id)
        return app

    def owner(self, factory_key: str) -> str | None:
        return next((app.app_id for app in self.apps if factory_key in app.factory_keys), None)

    @property
    def factory_keys(self) -> tuple[str, ...]:
        return tuple(sorted(key for app in self.apps for key in app.factory_keys))

    def capture(self, policy: AppPolicy, organization_version: int) -> AppActivation:
        return AppActivation(
            organization_version=organization_version,
            apps=tuple(
                AcceptedApp(
                    app_id=app.app_id,
                    distribution=app.distribution,
                    version=app.version,
                    factory_keys=tuple(sorted(app.factory_keys)),
                    enabled=app.app_id in policy.enabled,
                )
                for app in self.apps
            ),
        )
