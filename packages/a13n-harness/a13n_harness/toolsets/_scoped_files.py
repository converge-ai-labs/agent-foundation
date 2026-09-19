"""Compound file-operation support shared by Environment-backed Toolsets."""

from __future__ import annotations

import posixpath
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar

from a13n_harness.context import AgentContext
from a13n_harness.environment._resources import selection_resource
from a13n_harness.environment.providers import FileScopeProvider, FileScopeSelection
from a13n_harness.environment.virtual_files import VirtualFileOperator
from a13n_harness.providers.environment.files import FileCopyResult, FileMutationResult, FileOperator
from a13n_harness.providers.environment.models import EnvironmentError, EnvironmentPath
from a13n_harness.tools.metadata import CanonicalResource, ToolResourceResolver


class ScopedFileAccess:
    """Keep compound execution on one selected file scope, separate from resource metadata."""

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

    def resource_resolver(self, argument_name: str, *, include_parent: bool = False) -> ToolResourceResolver | None:
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
            selection = await scopes.resolve_files(path)
            resources = [selection_resource(selection)]
            if include_parent:
                parent = scopes.select_files(posixpath.dirname(path) or ".")
                if (
                    parent.resolved_path.mount_id != selection.resolved_path.mount_id
                    or parent.observed_generation != selection.observed_generation
                ):
                    raise EnvironmentError(
                        "Document output parent changed during resource resolution.",
                        code="environment_stale_mount",
                    )
                resources.append(selection_resource(parent))
            return tuple(resources)

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
            source_selection.resolved_path.mount_id != destination_selection.resolved_path.mount_id
            or source_selection.observed_generation != destination_selection.observed_generation
        ):
            raise EnvironmentError(
                "Cross-mount move must be expressed as copy and separately authorized remove.",
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
        same_mount = (
            source_selection.resolved_path.mount_id == destination_selection.resolved_path.mount_id
            and source_selection.observed_generation == destination_selection.observed_generation
        )
        if not same_mount:
            raise EnvironmentError(
                "Cross-mount copy requires a provider-neutral virtual file router.",
                code="environment_unsupported",
            )
        async with scopes.open_files(source_selection) as source_files:
            return await source_files.copy(source, destination, replace=replace)

    def resolved_path(self, path: str) -> EnvironmentPath | None:
        """Return the execution-local path, or project the current route outside a scope."""
        if self._scopes is None:
            return None
        selection = self._selection.get()
        if selection is None or selection.logical_path != path:
            selection = self._scopes.select_files(path)
        return selection.resolved_path

    def has_mount_root_parent(self, path: str) -> bool:
        """Use the selected provider path, independent of aggregate root flavor or spelling."""
        selection = self._selection.get()
        return (
            selection is not None
            and selection.logical_path == path
            and posixpath.dirname(selection.resolved_path.path) == "/"
        )

    @asynccontextmanager
    async def scope(self, path: str) -> AsyncGenerator[FileOperator]:
        if self._scopes is None:
            yield self._files
            return
        selection = self._scopes.select_files(path)
        token = self._selection.set(selection)
        try:
            async with self._scopes.open_files(selection) as files:
                yield files
        finally:
            self._selection.reset(token)


__all__ = ["ScopedFileAccess"]
