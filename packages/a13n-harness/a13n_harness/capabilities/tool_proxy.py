"""Native Capability composition for grouped ToolProxy discovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from pydantic_ai import RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    Capability,
    CapabilityOrdering,
    CombinedCapability,
    WrapperCapability,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import AgentNativeTool
from pydantic_ai.toolsets import AbstractToolset

from a13n_harness.context import AgentContext
from a13n_harness.tools.invocation import ToolExecutionBoundaryCapability
from a13n_harness.tools.surface import ToolSurfaceCapability
from a13n_harness.tools.tool_proxy import ToolProxyConfig, proxy_membership, validate_group
from a13n_harness.toolsets.tool_proxy import ToolProxySurfaceToolset, _GroupedToolset

TOOL_PROXY_CAPABILITY_ID = "a13n.tool-proxy"


@dataclass(frozen=True, slots=True)
class ToolProxyGroup:
    """One explicit source and its model-facing domain description."""

    source: AbstractToolset[AgentContext] | AbstractCapability[AgentContext]
    description: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, AbstractToolset | AbstractCapability):
            raise TypeError(
                "ToolProxyGroup.source must be a Toolset or Capability instance; resolve Host configuration before constructing the group."
            )


class ToolProxyCapability(CombinedCapability[AgentContext]):
    """Compose grouped sources and one discovery surface using native Capabilities.

    The Host selects and constructs sources once. Native composition owns their
    Agent/Run binding, hooks, and toolsets; this container adds no lifecycle.
    """

    def __init__(self, *, groups: Mapping[str, ToolProxyGroup], config: ToolProxyConfig | None = None) -> None:
        sources: list[AbstractCapability[AgentContext]] = []
        for name, group in groups.items():
            if not isinstance(group, ToolProxyGroup):
                raise TypeError(
                    f"ToolProxyCapability.groups[{name!r}] must be ToolProxyGroup(source=..., description=...)."
                )
            source = group.source
            if isinstance(source, AbstractToolset):
                source = Capability(toolsets=[source])
            sources.append(_ToolProxyGroupCapability(source, name, group.description))
        super().__init__([_ToolProxySurfaceCapability(config if config is not None else ToolProxyConfig()), *sources])


@dataclass
class _ToolProxySurfaceCapability(AbstractCapability[AgentContext]):
    """Expose one grouped search/call surface without replacing ToolManager."""

    config: ToolProxyConfig = field(default_factory=ToolProxyConfig)
    id: str | None = field(default=TOOL_PROXY_CAPABILITY_ID, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.config, ToolProxyConfig):
            raise TypeError("ToolProxyCapability config must be ToolProxyConfig")
        if self.id != TOOL_PROXY_CAPABILITY_ID:
            raise ValueError(f"ToolProxyCapability.id must be {TOOL_PROXY_CAPABILITY_ID!r}")

    def get_ordering(self) -> CapabilityOrdering:
        from a13n_harness.capabilities.codeact import CodeActCapability

        return CapabilityOrdering(
            position="outermost",
            wraps=(ToolSurfaceCapability,),
            wrapped_by=(ToolExecutionBoundaryCapability, CodeActCapability),
        )

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        return ToolProxySurfaceToolset(toolset, self.config)

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        # Hiding a presentation is not removal from the current execution directory.
        parameters = request_context.model_request_parameters
        request_context.model_request_parameters = replace(
            parameters,
            function_tools=[tool for tool in parameters.function_tools if proxy_membership(tool) is None],
        )
        return request_context


@dataclass
class _ToolProxyGroupCapability(WrapperCapability[AgentContext]):
    """Assign a Capability's run-bound local Toolset to one proxy group.

    WrapperCapability owns for_agent/for_run rebinding, including ContextualMCP's
    fresh authenticated MCP replacement. No client or toolset is captured early.
    """

    group: str
    group_description: str

    def __post_init__(self) -> None:
        super().__post_init__()
        validate_group(self.group, self.group_description)
        if self.defer_loading:
            raise ValueError(
                f"ToolProxy group {self.group!r} uses a deferred-loading source; remove defer_loading=True or leave it directly exposed."
            )

    def get_toolset(self) -> AbstractToolset[AgentContext] | None:
        toolset = self.wrapped.get_toolset()
        if toolset is None:
            return None
        if not isinstance(toolset, AbstractToolset):
            raise TypeError(f"ToolProxy group {self.group!r} requires a concrete local Toolset contribution.")
        return _GroupedToolset(toolset, self.group, self.group_description)

    def get_native_tools(self) -> Sequence[AgentNativeTool[AgentContext]]:
        if self.wrapped.get_native_tools():
            raise ValueError(
                f"ToolProxy group {self.group!r} includes provider-native tools; use local=True, native=False for MCP."
            )
        return []


__all__ = ["TOOL_PROXY_CAPABILITY_ID", "ToolProxyCapability", "ToolProxyConfig", "ToolProxyGroup"]
