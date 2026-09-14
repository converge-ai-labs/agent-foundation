"""Stable permission identities, independent of native tool presentation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal
from urllib.parse import quote

from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset
from pydantic_ai.toolsets.prefixed import PrefixedToolset
from pydantic_ai.toolsets.renamed import RenamedToolset

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.tools.metadata import HARNESS_TOOL_METADATA_KEY, normalize_harness_tool_metadata

TOOL_IDENTITY_KEY = "a13n.harness.tool-identity"
type ToolPermissionMode = Literal["allow", "deny", "ask", "review"]


@dataclass(frozen=True, slots=True)
class ToolIdentity:
    """Trusted policy identity; native definitions still own visible names and schemas."""

    tool_id: str
    default_mode: ToolPermissionMode = "allow"

    def __post_init__(self) -> None:
        if not self.tool_id or self.tool_id != self.tool_id.strip() or len(self.tool_id) > 1024 or "*" in self.tool_id:
            raise ValueError("tool_id must be a bounded exact identity without wildcards")
        if self.default_mode not in {"allow", "deny", "ask", "review"}:
            raise ValueError("Unsupported default tool permission mode")


def source_tool_id(source: str, name: str, *, kind: Literal["tool", "mcp"] = "tool") -> str:
    """Encode a source-local name without ambiguous separators or wildcard expansion."""
    if not source.strip() or not name.strip():
        raise ValueError("Tool source and original name must be non-blank")
    return f"{kind}/{quote(source, safe='')}/{quote(name, safe='')}"


def tool_identity(tool_def: ToolDefinition) -> ToolIdentity:
    value = (tool_def.metadata or {}).get(TOOL_IDENTITY_KEY)
    if not isinstance(value, ToolIdentity):
        raise DefinitionError("Tool has no prepared permission identity.", code="tool_identity_missing")
    return value


def identify_tool(tool: ToolsetTool[AgentContext]) -> ToolsetTool[AgentContext]:
    """Preserve explicit identities or derive one from standard native source wrappers."""
    metadata = dict(tool.tool_def.metadata or {})
    if TOOL_IDENTITY_KEY in metadata:
        tool_identity(tool.tool_def)
        return tool
    if HARNESS_TOOL_METADATA_KEY in metadata:
        managed = normalize_harness_tool_metadata(metadata[HARNESS_TOOL_METADATA_KEY])
        identity = ToolIdentity(managed.tool_id)
    else:
        source = tool.toolset
        name = tool.tool_def.name
        while isinstance(source, WrapperToolset):
            if isinstance(source, PrefixedToolset):
                name = name.removeprefix(source.prefix + "_")
            elif isinstance(source, RenamedToolset):
                name = source.name_map.get(name, name)
            source = source.wrapped
        if isinstance(source, MCPToolset) and source.id is None:
            raise DefinitionError("MCP sources require a stable id.", code="tool_source_id_required")
        identity = ToolIdentity(
            source_tool_id(source.id or "native", name, kind="mcp" if isinstance(source, MCPToolset) else "tool"),
            "allow",
        )
    metadata[TOOL_IDENTITY_KEY] = identity
    return replace(tool, tool_def=replace(tool.tool_def, metadata=metadata))


@dataclass
class ToolIdentityToolset(WrapperToolset[AgentContext]):
    """Attach source identities before custom renaming/grouping wrappers.

    Hosts need this adapter only when a custom presentation cannot preserve the
    identity metadata or be expressed using native prefix/rename wrappers.
    """

    source_id: str
    kind: Literal["tool", "mcp"] = "tool"
    default_mode: ToolPermissionMode | None = None

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        return {
            name: identify_tool(tool)
            if TOOL_IDENTITY_KEY in (tool.tool_def.metadata or {})
            or HARNESS_TOOL_METADATA_KEY in (tool.tool_def.metadata or {})
            else replace(
                tool,
                tool_def=replace(
                    tool.tool_def,
                    metadata={
                        **(tool.tool_def.metadata or {}),
                        TOOL_IDENTITY_KEY: ToolIdentity(
                            source_tool_id(self.source_id, name, kind=self.kind),
                            self.default_mode or "allow",
                        ),
                    },
                ),
            )
            for name, tool in tools.items()
        }
