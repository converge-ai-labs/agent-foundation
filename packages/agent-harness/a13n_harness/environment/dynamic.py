"""Dynamic Environment context, lifecycle, and model-tool composition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset, DynamicToolset

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextNext,
    ModelContextProjection,
    ModelContextProjectionRequest,
)
from a13n_harness.tools.metadata import ToolResourceResolver
from a13n_harness.toolsets.file_media import (
    AgentMediaUnderstandingProvider,
    MediaUnderstandingProvider,
    NativeInputMediaKind,
)
from a13n_harness.toolsets.files import FileToolset
from a13n_harness.toolsets.process_manager import _RUN_PROCESS_ACTIONS
from a13n_harness.toolsets.shell import ShellToolset

from ._dynamic_context import _DynamicEnvironmentContext
from .configuration import DynamicEnvironmentConfiguration
from .models import EnvironmentAction
from .providers import BoundEnvironment

DYNAMIC_ENVIRONMENT_CAPABILITY_ID = "a13n.dynamic-environment"
FILE_MEDIA_UNDERSTANDING_RUN_CAPABILITY_ID = "a13n.dynamic-environment.file-media-understanding.run"

_FILE_READ_ACTIONS = frozenset(
    {
        EnvironmentAction.FILE_STAT,
        EnvironmentAction.FILE_READ_TEXT,
        EnvironmentAction.FILE_READ_BYTES,
        EnvironmentAction.FILE_LIST,
        EnvironmentAction.FILE_QUERY,
        EnvironmentAction.FILE_SEARCH_TEXT,
        EnvironmentAction.FILE_COPY_SOURCE,
    }
)
_FILE_MUTATION_ACTIONS = frozenset(
    action
    for action in EnvironmentAction
    if action.value.startswith("environment.file.") and action not in _FILE_READ_ACTIONS
)


@dataclass(kw_only=True)
class FileMediaUnderstandingRunCapability(AbstractCapability[AgentContext]):
    """Fresh run attachment carrying file media-understanding authority."""

    id: str | None = FILE_MEDIA_UNDERSTANDING_RUN_CAPABILITY_ID
    provider: MediaUnderstandingProvider = field()

    def __post_init__(self) -> None:
        if self.id != FILE_MEDIA_UNDERSTANDING_RUN_CAPABILITY_ID:
            raise ValueError(
                f"FileMediaUnderstandingRunCapability.id must be {FILE_MEDIA_UNDERSTANDING_RUN_CAPABILITY_ID!r}"
            )
        if not isinstance(self.provider, MediaUnderstandingProvider):
            raise TypeError("provider must implement MediaUnderstandingProvider")


@dataclass(init=False)
class DynamicEnvironmentCapability(AbstractModelContextCapability):
    """Own dynamic Environment context and run-scoped Toolset composition."""

    id = DYNAMIC_ENVIRONMENT_CAPABILITY_ID

    def __init__(self, configuration: DynamicEnvironmentConfiguration) -> None:
        if not isinstance(configuration, DynamicEnvironmentConfiguration):
            configuration = DynamicEnvironmentConfiguration.model_validate(configuration, strict=True)
        self.configuration = configuration.model_copy(deep=True)

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(DYNAMIC_ENVIRONMENT_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, _DynamicEnvironmentRunCapability):
                raise DefinitionError(
                    "Dynamic Environment Capability has an incompatible logical-run replacement.",
                    code="capability_type_mismatch",
                )
            return existing
        replacement = _DynamicEnvironmentRunCapability(
            self.configuration,
            run_id=ctx.deps.run_id,
            environment=ctx.deps.environment,
        )
        ctx.deps._record_run_capability(DYNAMIC_ENVIRONMENT_CAPABILITY_ID, replacement)
        ctx.deps._register_run_cleanup(
            "a13n.dynamic-environment.shell-processes",
            replacement._close_processes,
        )
        return replacement


@dataclass(init=False)
class _DynamicEnvironmentRunCapability(DynamicEnvironmentCapability):
    """Run-scoped dynamic context and owner of reusable File and Shell Toolsets."""

    def __init__(
        self,
        configuration: DynamicEnvironmentConfiguration,
        *,
        run_id: str,
        environment: BoundEnvironment,
    ) -> None:
        super().__init__(configuration)
        self._run_id = run_id
        self._environment = environment
        self._process_capable = any(
            _RUN_PROCESS_ACTIONS <= mount.permission_ceiling.operations for mount in environment.snapshot.mounts
        )
        self._shell_toolset = ShellToolset(
            environment,
            process_capable=self._process_capable,
            resource_resolver=lambda tool_id: self._dynamic_context._resource_resolver(tool_id),
            execution_guard=lambda: self._dynamic_context._assert_authorized_fence(),
        )
        self._dynamic_context = _DynamicEnvironmentContext(
            configuration,
            run_id=run_id,
            environment=environment,
            resolve_process_resource=self._shell_toolset.resolve_process_resource,
        )
        self._file_toolset = FileToolset(
            environment.files,
            resource_resolver=self._dynamic_context._resource_resolver,
            execution_guard=self._dynamic_context._assert_authorized_fence,
            file_scopes=environment,
            media_understanding=_resolve_file_media_understanding,
        )

        self._toolset = DynamicToolset(
            self._toolset_for_current_environment,
            per_run_step=True,
            id="a13n-dynamic-environment-tools",
        )

    def _toolset_for_current_environment(
        self,
        ctx: RunContext[AgentContext],
    ) -> AbstractToolset[AgentContext] | None:
        del ctx
        mounts = self._environment.snapshot.mounts
        operations = frozenset(action for mount in mounts for action in mount.permission_ceiling.operations)
        has_file_reads = self.configuration.files_enabled and bool(operations & _FILE_READ_ACTIONS)
        has_file_mutations = self.configuration.files_enabled and bool(operations & _FILE_MUTATION_ACTIONS)
        has_full_access = self.configuration.shell_enabled and EnvironmentAction.SHELL_EXEC in operations
        shell_supersedes_mutations = (
            self.configuration.shell_enabled
            and len(mounts) == 1
            and has_file_mutations
            and EnvironmentAction.SHELL_EXEC in mounts[0].permission_ceiling.operations
        )

        toolsets: list[AbstractToolset[AgentContext]] = []
        if has_file_reads:
            toolsets.append(
                self._file_toolset.get_toolset(
                    shell_active=shell_supersedes_mutations,
                    include_mutations=has_file_mutations,
                )
            )
        if has_full_access:
            toolsets.append(self._shell_toolset.get_toolset())
        if not toolsets:
            return None
        return CombinedToolset(toolsets)

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps.run_id != self._run_id:
            raise DefinitionError(
                "Dynamic Environment run replacement cannot cross logical runs.",
                code="capability_scope_invalid",
            )
        return self

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return self._toolset

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Any) -> Any:
        async def run_with_dynamic_context() -> Any:
            return await self._dynamic_context.wrap_run(ctx, handler=handler)

        return await self._shell_toolset.wrap_run(ctx, handler=run_with_dynamic_context)

    async def wrap_model_context(
        self,
        ctx: RunContext[AgentContext],
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        return await self._dynamic_context.wrap_model_context(ctx, request, handler)

    # Package-private seams used by focused authorization tests.
    def _resource_resolver(self, tool_id: str) -> ToolResourceResolver:
        return self._dynamic_context._resource_resolver(tool_id)

    def _assert_authorized_fence(self) -> None:
        self._dynamic_context._assert_authorized_fence()

    async def _close_processes(self) -> None:
        await self._shell_toolset.close()


def _resolve_file_media_understanding(
    ctx: RunContext[AgentContext],
    kind: NativeInputMediaKind,
) -> MediaUnderstandingProvider | None:
    attachment = ctx.capabilities.get(FILE_MEDIA_UNDERSTANDING_RUN_CAPABILITY_ID)
    if attachment is None:
        return AgentMediaUnderstandingProvider.from_environment(kind=kind)
    if type(attachment) is not FileMediaUnderstandingRunCapability:
        raise DefinitionError(
            "File media understanding has an incompatible run attachment.",
            code="capability_type_mismatch",
        )
    if FILE_MEDIA_UNDERSTANDING_RUN_CAPABILITY_ID not in ctx.deps._capability_provenance.run_ids:
        raise DefinitionError(
            "FileMediaUnderstandingRunCapability must originate from RunBindings.",
            code="capability_scope_invalid",
        )
    return attachment.provider


__all__ = [
    "DynamicEnvironmentCapability",
    "DynamicEnvironmentConfiguration",
    "FileMediaUnderstandingRunCapability",
]
