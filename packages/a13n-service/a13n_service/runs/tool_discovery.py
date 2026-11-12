"""Project Service source selections onto native deferred tool definitions."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from a13n_harness import AgentContext
from a13n_harness.tools.identity import identify_tool, source_tool_prefix, tool_identity
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, CombinedCapability, ToolSearch
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool, WrapperToolset

from a13n_service.resources.agents import toolsets
from a13n_service.resources.agents.schemas import AgentConfig, NativeToolSearch


def native_discovery(
    config: AgentConfig, connections: Mapping[str, toolsets.ConnectionInfo]
) -> AbstractCapability[AgentContext] | None:
    if not isinstance(config.tool_search, NativeToolSearch):
        return None
    selected = config.tool_search.sources
    prefixes = tuple(
        source_tool_prefix(item.connection_id, kind="mcp" if connections[item.connection_id].type == "mcp" else "tool")
        for item in config.connection_tools
        if selected.connections and item.connection_id not in selected.exclude_connections and item.tools != ()
    )
    return CombinedCapability(
        [
            ToolSearch(),
            _NativeDiscovery(tool_ids=toolsets.discovery_tool_ids(selected), source_prefixes=prefixes),
        ]
    )


@dataclass(kw_only=True)
class _NativeDiscovery(AbstractCapability[AgentContext]):
    tool_ids: frozenset[str]
    source_prefixes: tuple[str, ...]
    id: str | None = "a13n-service.native-tool-discovery"

    def get_ordering(self) -> CapabilityOrdering:
        # The upstream search wrapper must receive the deferred definitions first.
        return CapabilityOrdering(wrapped_by=(ToolSearch,))

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        return _DeferredServiceTools(toolset, self.tool_ids, self.source_prefixes)


@dataclass
class _DeferredServiceTools(WrapperToolset[AgentContext]):
    tool_ids: frozenset[str]
    source_prefixes: tuple[str, ...]

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        result: dict[str, ToolsetTool[AgentContext]] = {}
        for name, tool in tools.items():
            definition = tool.tool_def
            # Provider-native fallbacks, external calls and control tools retain their presentation.
            if definition.kind == "function" and definition.with_native is None:
                identity = tool_identity(identify_tool(tool).tool_def).tool_id
                if identity in self.tool_ids or identity.startswith(self.source_prefixes):
                    tool = replace(tool, tool_def=replace(definition, defer_loading=True))
            result[name] = tool
        return result
