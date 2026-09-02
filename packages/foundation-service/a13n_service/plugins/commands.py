"""Narrow authority boundaries for runner Plugin Runtime commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from a13n_harness import SafeFailure

from a13n_service.iam import AuthenticatedActor

from .domain import Plugin, PluginTaskReceipt, PluginVersion
from .runtime import PluginRuntimeLock

PluginRuntimeCommand = Literal["activate", "deactivate"]


@dataclass(frozen=True, slots=True)
class PluginRuntimeVersionSpec:
    plugin: Plugin
    version: PluginVersion
    requires_python: str | None
    wheel_tags: tuple[str, ...]
    root_is_purelib: bool
    entry_point_target: str


@dataclass(frozen=True, slots=True)
class PluginRuntimeCatalogSnapshot:
    runtime_version: int
    active_lock_digest: str | None
    active_versions: tuple[PluginRuntimeVersionSpec, ...]
    target_plugin: Plugin
    target_version: PluginRuntimeVersionSpec | None


class PluginRuntimeCommandFailure(Exception):
    """Bounded command failure safe to retain on a public receipt."""

    def __init__(self, failure: SafeFailure, *, retryable: bool = False) -> None:
        super().__init__(failure.code)
        self.failure = failure
        self.retryable = retryable


class PluginRuntimeCandidateResolver(Protocol):
    """Resolve and durably publish one exact candidate Runtime lock.

    Implementations may perform package-index and object-store I/O, but return
    only after the complete lock and every referenced artifact are durable.
    Repeating one operation ID must reconcile the same logical request.
    """

    async def resolve_candidate(
        self,
        *,
        operation_id: str,
        command: PluginRuntimeCommand,
        catalog: PluginRuntimeCatalogSnapshot,
    ) -> PluginRuntimeLock: ...

    async def require_candidate(self, *, runtime_lock_digest: str) -> PluginRuntimeLock: ...


class PluginRuntimeStagingAuthority(Protocol):
    """Coordinate idempotent all-serviceable-Worker staging and cutover."""

    async def stage_candidate(
        self,
        *,
        operation_id: str,
        runtime_lock: PluginRuntimeLock,
    ) -> str: ...

    async def activate_candidate(
        self,
        *,
        operation_id: str,
        runtime_lock: PluginRuntimeLock,
        staging_token: str,
        runtime_version: int,
    ) -> None: ...

    async def abort_candidate(
        self,
        *,
        operation_id: str,
        runtime_lock: PluginRuntimeLock,
        staging_token: str | None,
    ) -> None: ...


class PluginRuntimeCommandDispatcher(Protocol):
    """Accept commands only after durable Worker staging can be coordinated.

    Implementations own idempotent receipt persistence, final authorization and
    target revalidation, candidate resolution, all-serviceable-Worker staging,
    atomic catalog cutover, and caller-scoped receipt reads.
    """

    async def activate(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        plugin: Plugin,
        plugin_version: PluginVersion,
        idempotency_key: str,
    ) -> PluginTaskReceipt: ...

    async def deactivate(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        plugin: Plugin,
        idempotency_key: str,
    ) -> PluginTaskReceipt: ...

    async def get_receipt(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        operation_id: str,
    ) -> PluginTaskReceipt: ...
