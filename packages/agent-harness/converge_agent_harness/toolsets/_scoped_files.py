"""Compound file-operation support shared by Environment-backed Toolsets."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar

from converge_agent_harness.context import AgentContext
from converge_agent_harness.environment.files import FileCopyResult, FileMutationResult, FileOperator
from converge_agent_harness.environment.models import EnvironmentError
from converge_agent_harness.environment.providers import FileScopeProvider, FileScopeSelection
from converge_agent_harness.environment.virtual_files import VirtualFileOperator
from converge_agent_harness.tools.metadata import CanonicalResource, ToolResourceResolver


class ScopedFileAccess:
    """Bind managed resource authorization to one revision-pinned file scope."""

    def __init__(
        self,
        files: FileOperator,
        scopes: FileScopeProvider | None,
    ) -> None:
        self._files = files
        self._scopes = scopes
        self._selection: ContextVar[FileScopeSelection | None] = ContextVar(
            f"scoped_file_selection_{id(self)}",
            default=None,
        )

    def resource_resolver(self, argument_name: str) -> ToolResourceResolver | None:
        scopes = self._scopes
        if scopes is None:
            return None

        async def resolve(
            arguments: Mapping[str, object],
            *,
            context: AgentContext,
        ) -> tuple[CanonicalResource, ...]:
            del context
            path = arguments.get(argument_name)
            if not isinstance(path, str) or not path:
                raise EnvironmentError(
                    f"File resource argument {argument_name!r} is invalid.",
                    code="environment_request_invalid",
                )
            selection = scopes.select_files(path)
            self._selection.set(selection)
            selected = selection.resolved_path
            return (
                CanonicalResource(
                    namespace="environment",
                    kind="file",
                    identifier=(
                        f"{selected.binding_id}:{selected.binding_revision}:"
                        f"{selection.observed_generation}:{selected.path}"
                    ),
                ),
            )

        return resolve

    async def move(
        self,
        source: str,
        destination: str,
        *,
        replace: bool,
        guard: Callable[[], None] | None = None,
    ) -> FileMutationResult:
        scopes = self._scopes
        if scopes is None:
            if guard is not None:
                guard()
            return await self._files.move(source, destination, replace=replace)
        source_selection = scopes.select_files(source)
        destination_selection = scopes.select_files(destination)
        if (
            source_selection.resolved_path.binding_id != destination_selection.resolved_path.binding_id
            or source_selection.resolved_path.binding_revision != destination_selection.resolved_path.binding_revision
            or source_selection.observed_generation != destination_selection.observed_generation
        ):
            raise EnvironmentError(
                "Cross-binding move must be expressed as copy and separately authorized remove.",
                code="environment_unsupported",
            )
        if guard is not None:
            guard()
        async with scopes.open_files(source_selection) as files:
            return await files.move(source, destination, replace=replace)

    async def copy(
        self,
        source: str,
        destination: str,
        *,
        replace: bool,
        guard: Callable[[], None] | None = None,
    ) -> FileCopyResult:
        scopes = self._scopes
        if scopes is None:
            if guard is not None:
                guard()
            return await self._files.copy(source, destination, replace=replace)
        source_selection = scopes.select_files(source)
        destination_selection = scopes.select_files(destination)
        if guard is not None:
            guard()
        if isinstance(self._files, VirtualFileOperator):
            return await self._files._copy_resolved(
                source,
                destination,
                source_selected=source_selection.resolved_path,
                destination_selected=destination_selection.resolved_path,
                replace=replace,
            )
        same_binding = (
            source_selection.resolved_path.binding_id == destination_selection.resolved_path.binding_id
            and source_selection.resolved_path.binding_revision == destination_selection.resolved_path.binding_revision
            and source_selection.observed_generation == destination_selection.observed_generation
        )
        if not same_binding:
            raise EnvironmentError(
                "Cross-binding copy requires a provider-neutral virtual file router.",
                code="environment_unsupported",
            )
        async with scopes.open_files(source_selection) as source_files:
            return await source_files.copy(source, destination, replace=replace)

    def guard(self, path: str) -> None:
        selection = self._selection.get()
        if selection is None or self._scopes is None:
            return
        current = self._scopes.select_files(path)
        if (
            current.resolved_path != selection.resolved_path
            or current.observed_generation != selection.observed_generation
        ):
            raise EnvironmentError(
                "Environment binding changed after managed resource authorization.",
                code="environment_stale_binding",
            )

    @asynccontextmanager
    async def scope(
        self,
        path: str,
        *,
        prefer_authorized_selection: bool = True,
    ) -> AsyncIterator[FileOperator]:
        if self._scopes is None:
            yield self._files
            return
        selection = self._selection.get() if prefer_authorized_selection else None
        if selection is None or selection.logical_path != path:
            selection = self._scopes.select_files(path)
            self._selection.set(selection)
        self.guard(path)
        async with self._scopes.open_files(selection) as files:
            yield files


__all__ = ["ScopedFileAccess"]
