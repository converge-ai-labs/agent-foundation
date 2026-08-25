"""Dynamic Environment context, lifecycle, and model-tool composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset

from converge_agent_harness.context import AgentContext
from converge_agent_harness.errors import DefinitionError
from converge_agent_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextNext,
    ModelContextProjection,
    ModelContextProjectionRequest,
)
from converge_agent_harness.tools.metadata import ToolResourceResolver
from converge_agent_harness.toolsets.files import FileToolset
from converge_agent_harness.toolsets.shell import ShellProcessProjector, ShellToolset

from ._dynamic_context import (
    _DYNAMIC_ENVIRONMENT_INSTRUCTIONS,
    _DynamicEnvironmentContext,
)
from .configuration import DynamicEnvironmentConfiguration
from .providers import BoundEnvironment

DYNAMIC_ENVIRONMENT_CAPABILITY_ID = "converge.dynamic-environment"


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
        return replacement

    def get_instructions(self) -> str:
        surfaces = [
            name
            for name, enabled in (
                ("files", self.configuration.file_tools),
                ("shell", self.configuration.shell_tools),
                ("processes", self.configuration.process_tools),
                ("ports", self.configuration.port_tools),
            )
            if enabled
        ]
        return f"{_DYNAMIC_ENVIRONMENT_INSTRUCTIONS}\nEnabled tool surfaces: {', '.join(surfaces)}."


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
        self._shell_toolset = ShellToolset(
            shell=environment.shell,
            processes=environment.processes,
            outputs=environment.outputs,
            ports=environment.ports,
            max_reference_entries=configuration.max_reference_entries,
            resource_resolver=lambda tool_id: self._dynamic_context._resource_resolver(tool_id),
            execution_guard=lambda: self._dynamic_context._assert_authorized_fence(),
            shell_tools=configuration.shell_tools,
            process_tools=configuration.process_tools,
            port_tools=configuration.port_tools,
        )
        self._dynamic_context = _DynamicEnvironmentContext(
            configuration,
            run_id=run_id,
            environment=environment,
            resolve_process=self._shell_toolset.resolve_process,
        )
        self._file_toolset = FileToolset(
            environment.files,
            resource_resolver=self._dynamic_context._resource_resolver,
            execution_guard=self._dynamic_context._assert_authorized_fence,
            file_scopes=environment,
        )

        toolsets: list[AbstractToolset[AgentContext]] = []
        if configuration.file_tools:
            toolsets.append(self._file_toolset.get_toolset())
        if configuration.shell_tools or configuration.process_tools or configuration.port_tools:
            toolsets.append(self._shell_toolset.get_toolset())
        self._toolset = CombinedToolset(toolsets)

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
        return await self._dynamic_context.wrap_run(ctx, handler=handler)

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


def _resolve_dynamic_environment_process_projector(
    ctx: RunContext[AgentContext],
) -> ShellProcessProjector | None:
    value = ctx.capabilities.get(DYNAMIC_ENVIRONMENT_CAPABILITY_ID)
    if value is None:
        return None
    if not isinstance(value, _DynamicEnvironmentRunCapability):
        raise DefinitionError(
            "Dynamic Environment Capability has an incompatible finalized run value.",
            code="capability_type_mismatch",
        )
    return value._shell_toolset


__all__ = ["DynamicEnvironmentCapability", "DynamicEnvironmentConfiguration"]
