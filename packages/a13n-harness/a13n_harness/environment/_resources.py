"""Canonical Environment resource metadata for invocation policy and observation."""

from __future__ import annotations

from collections.abc import Mapping

from a13n_environment.models import EnvironmentError, EnvironmentOperationFamily

from a13n_harness.context import AgentContext
from a13n_harness.environment.models import EnvironmentReadinessRequirement
from a13n_harness.environment.providers import BoundEnvironment, FileScopeSelection
from a13n_harness.tools.metadata import CanonicalResource, ToolResourceResolver


def selection_resource(selection: FileScopeSelection, *, kind: str = "file") -> CanonicalResource:
    selected = selection.resolved_path
    incarnation = f"{selected.mount_id}:{selection.observed_generation}"
    path = selected.path if kind == "file" else ""
    return CanonicalResource(
        namespace="environment",
        kind=kind,
        identifier=f"{incarnation}:{path}" if kind == "file" else incarnation,
    )


class EnvironmentResources:
    """Project current resources without retaining execution authority.

    Toolsets own argument parsing. Metadata describes selection at resolution time;
    operation admission independently selects and checks the current Environment.
    """

    def __init__(self, environment: BoundEnvironment) -> None:
        self._environment = environment

    def resolver(
        self, resolve_resources: ToolResourceResolver, *, allow_unresolved: bool = True
    ) -> ToolResourceResolver:
        async def resolve(
            arguments: Mapping[str, object],
            *,
            context: AgentContext,
        ) -> tuple[CanonicalResource, ...]:
            try:
                return await resolve_resources(arguments, context=context)
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
                # Ordinary tools report unavailable routes at execution. Batch
                # resolvers opt out so invalid endpoints cannot be hidden.
                return ()

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
        return selection_resource(selection, kind="file" if path is not None else "mount")
