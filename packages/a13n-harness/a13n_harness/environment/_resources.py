"""Canonical Environment resources and invocation-local publication fences."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass

from a13n_harness.context import AgentContext
from a13n_harness.environment.models import (
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentReadinessRequirement,
)
from a13n_harness.environment.providers import BoundEnvironment, FileScopeSelection
from a13n_harness.tools.metadata import CanonicalResource, ToolResourceResolver


def selection_resource(selection: FileScopeSelection, *, kind: str = "file") -> CanonicalResource:
    selected = selection.resolved_path
    incarnation = f"{selected.mount_id}:{selection.observed_generation}"
    path = selected.path if kind == "file" else ""
    # Approval binds the operation's path, not a connection or backing target.
    return CanonicalResource(
        namespace="environment",
        kind=kind,
        identifier=f"{incarnation}:{path}" if kind == "file" else incarnation,
        approval_revision=path or "/",
    )


@dataclass(frozen=True, slots=True)
class _MountFence:
    mount_id: str
    observed_generation: str


@dataclass(frozen=True, slots=True)
class _AuthorizationFence:
    change_sequence: int
    mounts: tuple[_MountFence, ...]
    unresolved: bool = False


class EnvironmentResources:
    """Project selected resources and fence dispatch against their mount publications.

    Toolsets supply argument parsing; this helper only owns Environment selection,
    readiness, and invocation-local fencing. Each Toolset owns one instance.
    """

    def __init__(self, environment: BoundEnvironment) -> None:
        self._environment = environment
        self._authorization_fence: ContextVar[_AuthorizationFence | None] = ContextVar(
            "environment_authorization_fence", default=None
        )
        self._fence_builder: ContextVar[list[_MountFence] | None] = ContextVar(
            "environment_fence_builder", default=None
        )

    def resolver(
        self, resolve_resources: ToolResourceResolver, *, allow_unresolved: bool = True
    ) -> ToolResourceResolver:
        async def resolve(
            arguments: Mapping[str, object],
            *,
            context: AgentContext,
        ) -> tuple[CanonicalResource, ...]:
            change_sequence = self._environment._change_sequence
            fences: list[_MountFence] = []
            token = self._fence_builder.set(fences)
            try:
                resources = await resolve_resources(arguments, context=context)
            except EnvironmentError as exc:
                if not allow_unresolved:
                    raise
                if exc.code not in {
                    "environment_reference_invalid",
                    "environment_reference_stale",
                    "environment_selection_invalid",
                    "environment_unavailable",
                }:
                    raise
                self._authorization_fence.set(
                    _AuthorizationFence(change_sequence=change_sequence, mounts=(), unresolved=True)
                )
                return ()
            finally:
                self._fence_builder.reset(token)
            self._authorization_fence.set(
                _AuthorizationFence(
                    change_sequence=change_sequence,
                    mounts=tuple(dict.fromkeys(fences)),
                    unresolved=False,
                )
            )
            return resources

        return resolve

    async def paths(
        self,
        paths: tuple[str, ...],
    ) -> tuple[CanonicalResource, ...]:
        return tuple(dict.fromkeys([await self.path(path) for path in paths]))

    async def path(
        self,
        path: str,
        *,
        alias: str | None = None,
    ) -> CanonicalResource:
        selection = await self._environment.resolve_files(path, alias=alias)
        selected = selection.resolved_path
        self._record_fence(selected.mount_id, selection.observed_generation)
        return selection_resource(selection)

    async def binding(
        self, alias: str | None, family: EnvironmentOperationFamily, *, path: str | None = None
    ) -> CanonicalResource:
        selection = self._environment.select_files(path or ".", alias=alias)
        mount = next(
            item
            for item in self._environment.snapshot.mounts
            if self._environment.select_files(".", alias=item.name).resolved_path.mount_id
            == selection.resolved_path.mount_id
        )
        if family in mount.descriptor.operation_families:
            await self._environment.ensure_ready(
                EnvironmentReadinessRequirement(mounts=frozenset({mount.name}), operations=frozenset({family}))
            )
        selection = self._environment.select_files(path or ".", alias=alias)
        selected = selection.resolved_path
        self._record_fence(selected.mount_id, selection.observed_generation)
        return selection_resource(selection, kind="file" if path is not None else "mount")

    def _record_fence(self, mount_id: str, generation: str) -> None:
        builder = self._fence_builder.get()
        if builder is not None:
            builder.append(_MountFence(mount_id, generation))

    def guard(self) -> None:
        fence = self._authorization_fence.get()
        if fence is None:
            return
        if fence.unresolved:
            if self._environment._change_sequence != fence.change_sequence:
                raise EnvironmentError(
                    "Environment mounts changed after managed resource authorization.",
                    code="environment_stale_mount",
                )
            return
        for expected in fence.mounts:
            if not self._environment._is_mount_current(expected.mount_id, expected.observed_generation):
                raise EnvironmentError(
                    "Environment mount changed after managed resource authorization.",
                    code="environment_stale_mount",
                )
