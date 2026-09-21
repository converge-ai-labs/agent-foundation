"""Central resolution of the prepared model-facing tool surface."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import cast

from pydantic import JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, ToolSearch
from pydantic_ai.capabilities._deferred_capability_loader import DeferredCapabilityLoader
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool, WrapperToolset

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.tools.invocation import ToolExecutionBoundaryCapability
from a13n_harness.tools.metadata import (
    HARNESS_TOOL_METADATA_KEY,
    HarnessToolMetadata,
    normalize_harness_tool_metadata,
)

TOOL_SURFACE_CAPABILITY_ID = "a13n.tool-surface"


@dataclass
class ToolSurfaceCapability(AbstractCapability[AgentContext]):
    """Mandatory wrapper that resolves one complete prepared candidate surface."""

    id: str | None = TOOL_SURFACE_CAPABILITY_ID

    def __post_init__(self) -> None:
        if self.id != TOOL_SURFACE_CAPABILITY_ID:
            raise ValueError(f"ToolSurfaceCapability.id must be {TOOL_SURFACE_CAPABILITY_ID!r}")

    def get_ordering(self) -> CapabilityOrdering:
        from a13n_harness.capabilities.codeact import CodeActCapability

        return CapabilityOrdering(
            position="outermost",
            wraps=(ToolSearch, DeferredCapabilityLoader),
            wrapped_by=(ToolExecutionBoundaryCapability, CodeActCapability),
        )

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        return ToolSurfaceToolset(toolset)


@dataclass
class ToolSurfaceToolset(WrapperToolset[AgentContext]):
    """Reduce prepared candidates before later wrappers consume the surface."""

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        candidates = await self.wrapped.get_tools(ctx)
        return resolve_tool_surface(
            candidates,
            allow_deferred=ctx.deps.deferred_tools_supported,
        )


def resolve_tool_surface(
    candidates: Mapping[str, ToolsetTool[AgentContext]],
    *,
    allow_deferred: bool = True,
) -> dict[str, ToolsetTool[AgentContext]]:
    """Return the effective surface after child filtering and managed-tool supersession."""
    normalized_tools: dict[str, ToolsetTool[AgentContext]] = {}
    metadata_by_name: dict[str, HarnessToolMetadata] = {}
    name_by_tool_id: dict[str, str] = {}

    for name, tool in candidates.items():
        tool_def = tool.tool_def
        if not allow_deferred and tool_def.defer:
            continue
        metadata_values = tool_def.metadata or {}
        raw_metadata = metadata_values.get(HARNESS_TOOL_METADATA_KEY)
        if raw_metadata is None:
            normalized_tools[name] = tool
            continue
        if tool_def.kind not in {"function", "unapproved"}:
            raise DefinitionError(
                "Managed tool metadata is valid only for function tools.",
                code="tool_metadata_kind_invalid",
                details={"tool_name": tool_def.name, "tool_kind": tool_def.kind},
            )

        metadata = normalize_harness_tool_metadata(raw_metadata)
        if previous := name_by_tool_id.get(metadata.tool_id):
            raise DefinitionError(
                "Managed tool_id values must be unique in the prepared candidate surface.",
                code="managed_tool_id_duplicate",
                details={
                    "tool_id": metadata.tool_id,
                    "tool_name": tool_def.name,
                    "other_tool_name": previous,
                },
            )
        name_by_tool_id[metadata.tool_id] = tool_def.name
        metadata_by_name[name] = metadata
        copied_metadata = dict(metadata_values)
        copied_metadata[HARNESS_TOOL_METADATA_KEY] = metadata
        normalized_tools[name] = replace(tool, tool_def=replace(tool_def, metadata=copied_metadata))

    present_tool_ids = frozenset(name_by_tool_id)
    _validate_acyclic_supersession(metadata_by_name.values(), present_tool_ids)

    return {
        name: tool
        for name, tool in normalized_tools.items()
        if (metadata := metadata_by_name.get(name)) is None
        or not metadata.superseded_by_tool_ids.intersection(present_tool_ids)
    }


def _validate_acyclic_supersession(
    metadata_values: Iterable[HarnessToolMetadata],
    present_tool_ids: frozenset[str],
) -> None:
    edges = {value.tool_id: value.superseded_by_tool_ids.intersection(present_tool_ids) for value in metadata_values}
    incoming = dict.fromkeys(edges, 0)
    for targets in edges.values():
        for target in targets:
            incoming[target] += 1
    ready = sorted(tool_id for tool_id, count in incoming.items() if count == 0)
    visited = 0
    while ready:
        tool_id = ready.pop()
        visited += 1
        for target in edges[tool_id]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    if visited != len(edges):
        remaining = cast(list[JsonValue], sorted(tool_id for tool_id, count in incoming.items() if count > 0))
        raise DefinitionError(
            "Managed tool supersession declarations must be acyclic.",
            code="tool_supersession_cycle",
            details={"tool_ids": remaining},
        )


__all__ = [
    "TOOL_SURFACE_CAPABILITY_ID",
    "ToolSurfaceCapability",
    "ToolSurfaceToolset",
    "resolve_tool_surface",
]
