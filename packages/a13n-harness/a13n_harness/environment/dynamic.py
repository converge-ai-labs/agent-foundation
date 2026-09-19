"""Dynamic Environment context, lifecycle, and model-tool composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.agent import ModelRequestNode
from pydantic_ai.capabilities import AbstractCapability, AgentNode
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset, DynamicToolset

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.model_context import AbstractModelContextCapability
from a13n_harness.providers.environment.models import EnvironmentAction
from a13n_harness.toolsets.file_media import (
    AgentMediaUnderstandingProvider,
    MediaUnderstandingProvider,
    NativeInputMediaKind,
)
from a13n_harness.toolsets.files import FileToolset
from a13n_harness.toolsets.shell import ShellToolset

from ._dynamic_context import _DynamicEnvironmentContext
from .configuration import DynamicEnvironmentConfiguration
from .providers import BoundEnvironment

DYNAMIC_ENVIRONMENT_CAPABILITY_ID = "a13n.dynamic-environment"


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
        self._shell_toolset = ShellToolset(environment)
        self._dynamic_context = _DynamicEnvironmentContext()
        self._file_toolset = FileToolset(
            environment.files,
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
        file_names = (
            self._file_toolset.available_names([mount.permission_ceiling.operations for mount in mounts])
            if self.configuration.files_enabled
            else frozenset()
        )
        if self.configuration.file_tools is not None:
            file_names &= self.configuration.file_tools
        has_full_access = self.configuration.shell_enabled and EnvironmentAction.SHELL_EXEC in operations
        shell_supersedes_mutations = (
            self.configuration.shell_enabled
            and len(mounts) == 1
            and EnvironmentAction.SHELL_EXEC in mounts[0].permission_ceiling.operations
        )

        toolsets: list[AbstractToolset[AgentContext]] = []
        if file_names:
            toolsets.append(
                self._file_toolset.get_toolset(
                    shell_active=shell_supersedes_mutations,
                    allowed_names=file_names,
                )
            )
        if self.configuration.shell_enabled and (
            has_full_access or any(action.value.startswith("environment.process.") for action in operations)
        ):
            shell = self._shell_toolset.get_toolset(allowed_names=self.configuration.shell_tools)
            if shell.tools:
                toolsets.append(shell)
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

    async def before_node_run(
        self, ctx: RunContext[AgentContext], *, node: AgentNode[AgentContext]
    ) -> AgentNode[AgentContext]:
        if isinstance(node, ModelRequestNode):
            self._dynamic_context.before_model_node(ctx)
        return node

    async def _close_processes(self) -> None:
        await self._shell_toolset.close()


def _resolve_file_media_understanding(
    ctx: RunContext[AgentContext],
    kind: NativeInputMediaKind,
) -> MediaUnderstandingProvider | None:
    provider = ctx.deps.file_media_understanding
    return provider if provider is not None else AgentMediaUnderstandingProvider.from_environment(kind=kind)


__all__ = [
    "DynamicEnvironmentCapability",
    "DynamicEnvironmentConfiguration",
]
