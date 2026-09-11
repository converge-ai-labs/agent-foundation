"""Native Capability composition for grouped ToolProxy discovery."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, WrapperCapability
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import AgentNativeTool
from pydantic_ai.toolsets import AbstractToolset

from a13n_harness.context import AgentContext
from a13n_harness.tools.invocation import ToolExecutionBoundaryCapability
from a13n_harness.tools.surface import ToolSurfaceCapability
from a13n_harness.tools.tool_proxy import ToolProxyConfig, proxy_membership, validate_group
from a13n_harness.toolsets.tool_proxy import ToolProxySurfaceToolset, ToolProxyToolset

TOOL_PROXY_CAPABILITY_ID = "a13n.tool-proxy"


@dataclass
class ToolProxyCapability(AbstractCapability[AgentContext]):
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
class ToolProxyGroup(WrapperCapability[AgentContext]):
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
            raise ValueError("ToolProxyGroup uses proxy discovery; do not defer-load its Capability")

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(requires=(ToolProxyCapability,))

    def get_toolset(self) -> AbstractToolset[AgentContext] | None:
        toolset = self.wrapped.get_toolset()
        if toolset is None:
            return None
        if not isinstance(toolset, AbstractToolset):
            raise TypeError("ToolProxyGroup requires a Capability contributing a concrete local Toolset")
        return ToolProxyToolset(toolset, self.group, self.group_description)

    def get_native_tools(self) -> Sequence[AgentNativeTool[AgentContext]]:
        if self.wrapped.get_native_tools():
            raise ValueError("ToolProxyGroup cannot proxy provider-native tools; use local=True, native=False for MCP")
        return []


__all__ = ["TOOL_PROXY_CAPABILITY_ID", "ToolProxyCapability", "ToolProxyConfig", "ToolProxyGroup"]
