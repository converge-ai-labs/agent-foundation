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
            sources.append(_group_source(source, name, group.description))
        super().__init__([_ToolProxySurfaceCapability(config if config is not None else ToolProxyConfig()), *sources])


def _group_source(
    source: AbstractCapability[AgentContext], name: str, description: str, source_label: str | None = None
) -> AbstractCapability[AgentContext]:
    """Mark native sortable nodes, retaining containers' instruction composition.

    Native visit/rebind preserves custom CombinedCapability instructions. Existing
    wrappers remain atomic; independently sortable siblings never become one node.
    The single-child container keeps original types and instance references visible
    to native ordering instead of replacing a leaf's registered identity.
    """
    validate_group(name, description)

    def mark(node: AbstractCapability[AgentContext]) -> AbstractCapability[AgentContext]:
        if node.defer_loading:
            raise ValueError(f"ToolProxy group {name!r} uses deferred source {node.id or type(node).__name__!r}.")
        return _ToolProxyGroupCapability(
            CombinedCapability([node]), name, description, source_label or source.id or type(source).__name__
        )

    if isinstance(source, CombinedCapability):
        # Native flattening preserves instructions, but not container-owned hooks.
        # Keep those containers atomic, just like an explicit native wrapper; do
        # not replicate their lifecycle or invent ordering across their boundary.
        native_methods = {
            name
            for base in (AbstractCapability, CombinedCapability)
            for name, value in vars(base).items()
            if not name.startswith("_") and name != "get_instructions" and callable(value)
        }
        for cls in type(source).__mro__:
            if cls is CombinedCapability:
                break
            if native_methods.intersection(vars(cls)):
                return _ToolProxyGroupCapability(
                    source, name, description, source_label or source.id or type(source).__name__
                )

    grouped = source.visit_and_replace(mark)
    assert grouped is not None
    return grouped


@dataclass(frozen=True, slots=True)
class ToolProxySelection:
    """Build-time membership of concrete sources and exact Harness plugin IDs."""

    description: str
    capabilities: tuple[AbstractCapability[AgentContext], ...] = ()
    plugins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "plugins", tuple(self.plugins))
        if not all(isinstance(source, AbstractCapability) for source in self.capabilities):
            raise TypeError("ToolProxySelection.capabilities must contain Capability instances")
        if not all(isinstance(plugin, str) and plugin.strip() for plugin in self.plugins):
            raise TypeError("ToolProxySelection.plugins must contain nonblank plugin IDs")


@dataclass(frozen=True, slots=True)
class ToolProxyPlan:
    """Immutable per-Agent presentation plan; never activates or constructs sources."""

    groups: Mapping[str, ToolProxySelection]
    config: ToolProxyConfig = field(default_factory=ToolProxyConfig)

    def __post_init__(self) -> None:
        from types import MappingProxyType

        groups = dict(self.groups)
        for name, selection in groups.items():
            if not isinstance(selection, ToolProxySelection):
                raise TypeError(f"ToolProxyPlan.groups[{name!r}] must be ToolProxySelection")
            validate_group(name, selection.description)
        if not isinstance(self.config, ToolProxyConfig):
            raise TypeError("ToolProxyPlan.config must be ToolProxyConfig")
        object.__setattr__(self, "groups", MappingProxyType(groups))

    def _compose(
        self,
        capabilities: Sequence[AbstractCapability[AgentContext]],
        contributions: Mapping[str, tuple[AbstractCapability[AgentContext], ...]],
    ) -> tuple[AbstractCapability[AgentContext], ...]:
        memberships: dict[int, tuple[str, str, str]] = {}
        plugin_memberships: dict[str, str] = {}
        available = {id(source) for source in capabilities}
        labels = {id(source): plugin_id for plugin_id, sources in contributions.items() for source in sources}
        for name, selection in self.groups.items():
            sources = list(selection.capabilities)
            for plugin_id in selection.plugins:
                if plugin_id in plugin_memberships:
                    raise ValueError(
                        f"ToolProxy plugin {plugin_id!r} belongs to both {plugin_memberships[plugin_id]!r} and {name!r}"
                    )
                if plugin_id not in contributions:
                    raise ValueError(f"ToolProxy group {name!r} references unavailable plugin {plugin_id!r}")
                plugin_memberships[plugin_id] = name
                sources.extend(contributions[plugin_id])
            for source in sources:
                if id(source) not in available:
                    raise ValueError(f"ToolProxy group {name!r} references a Capability not selected by this Agent")
                if id(source) in memberships:
                    raise ValueError(
                        f"ToolProxy source {source.id or type(source).__name__!r} belongs to multiple groups"
                    )
                memberships[id(source)] = (
                    name,
                    selection.description,
                    labels.get(id(source), source.id or type(source).__name__),
                )
        if not memberships:
            return tuple(capabilities)
        return (
            _ToolProxySurfaceCapability(self.config),
            *(
                _group_source(source, *memberships[id(source)]) if id(source) in memberships else source
                for source in capabilities
            ),
        )


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
    source_label: str

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
        return _GroupedToolset(toolset, self.group, self.group_description, self.source_label)

    def get_native_tools(self) -> Sequence[AgentNativeTool[AgentContext]]:
        if self.wrapped.get_native_tools():
            raise ValueError(
                f"ToolProxy group {self.group!r} includes provider-native tools; use local=True, native=False for MCP."
            )
        return []


__all__ = [
    "TOOL_PROXY_CAPABILITY_ID",
    "ToolProxyCapability",
    "ToolProxyConfig",
    "ToolProxyGroup",
    "ToolProxyPlan",
    "ToolProxySelection",
]
