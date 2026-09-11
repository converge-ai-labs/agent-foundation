"""Passive ToolProxy membership and resolution against the native tool directory."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic_ai import ModelRetry, RunContext, ToolDefinition
from pydantic_ai.exceptions import UserError
from pydantic_ai.toolsets import ToolsetTool

from a13n_harness.context import AgentContext

PROXY_MEMBERSHIP_KEY = "a13n.tool_proxy.member"
PROXY_CONTROL_KEY = "a13n.tool_proxy.control"


@dataclass(frozen=True, slots=True)
class ToolProxyConfig:
    """Names and bounded discovery settings for one Agent's proxy surface."""

    search_name: str = "search_proxy_tools"
    call_name: str = "call_proxy_tool"
    max_results: int = 10
    max_search_bytes: int = 32_768

    def __post_init__(self) -> None:
        for name in (self.search_name, self.call_name):
            if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name) is None:
                raise ValueError(
                    "ToolProxy entry names must start with a letter or underscore and contain 1-64 letters, digits, underscores, or hyphens."
                )
        if self.search_name == self.call_name:
            raise ValueError("ToolProxy search and call names must differ")
        if (
            isinstance(self.max_results, bool)
            or not isinstance(self.max_results, int)
            or not 1 <= self.max_results <= 100
        ):
            raise ValueError("ToolProxy max_results must be an integer between 1 and 100")
        if (
            isinstance(self.max_search_bytes, bool)
            or not isinstance(self.max_search_bytes, int)
            or not 1024 <= self.max_search_bytes <= 32_768
        ):
            raise ValueError("ToolProxy max_search_bytes must be an integer between 1024 and 32768")


@dataclass(frozen=True, slots=True)
class ToolProxyMembership:
    """One prepared tool's presentation identity, not a dispatch binding."""

    group: str
    description: str
    tool: str


def validate_group(group: str, description: str) -> None:
    if not isinstance(group, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", group) is None or "__" in group:
        raise ValueError(
            "ToolProxy group must start with a letter and contain 1-32 letters, digits, underscores, or hyphens, without '__'."
        )
    if not isinstance(description, str) or not description.strip() or len(description) > 512:
        raise ValueError("ToolProxy group_description must be non-blank and at most 512 characters.")


def proxy_membership(definition: ToolDefinition) -> ToolProxyMembership | None:
    value = (definition.metadata or {}).get(PROXY_MEMBERSHIP_KEY)
    if value is None:
        return None
    if not isinstance(value, ToolProxyMembership):
        raise TypeError(
            f"Invalid ToolProxy membership on {definition.name!r}; use ToolProxyToolset to assign groups instead of writing reserved metadata."
        )
    return value


def proxy_control(definition: ToolDefinition) -> str | None:
    value = (definition.metadata or {}).get(PROXY_CONTROL_KEY)
    return value if isinstance(value, str) else None


def validate_proxy_target(definition: ToolDefinition) -> None:
    """Validate after preparation too: native wrappers can add deferral flags later."""
    if definition.defer_loading:
        raise UserError(
            f"Tool {definition.name!r} uses deferred loading; remove defer_loading=True from the grouped source or leave it outside ToolProxy."
        )
    if definition.unless_native:
        raise UserError(
            f"Tool {definition.name!r} is a provider-native fallback; select native=False, local=True for grouped MCP tools."
        )
    if (
        definition.kind not in {"function", "unapproved"}
        or definition.tool_kind is not None
        or proxy_control(definition) is not None
        or (definition.metadata or {}).get("a13n.codeact.runner") is True
    ):
        raise UserError(
            f"Tool {definition.name!r} is not a local function tool supported by ToolProxy. Leave control, output, external, and provider-native tools directly exposed."
        )


def proxy_directory(
    tools: Mapping[str, ToolsetTool[AgentContext]],
) -> dict[tuple[str, str], str]:
    """Index only current prepared entries; never own an independent tool registry."""
    directory: dict[tuple[str, str], str] = {}
    descriptions: dict[str, str] = {}
    for name, prepared in tools.items():
        member = proxy_membership(prepared.tool_def)
        if member is None:
            continue
        validate_proxy_target(prepared.tool_def)
        key = (member.group, member.tool)
        if key in directory:
            raise ValueError(
                f"Duplicate ToolProxy tool {member.group}/{member.tool}; rename one source tool or assign it to another group."
            )
        if member.group in descriptions and descriptions[member.group] != member.description:
            raise ValueError(
                f"Conflicting descriptions for ToolProxy group {member.group!r}; use the same group_description for all sources in this group."
            )
        descriptions[member.group] = member.description
        directory[key] = name
    return directory


def resolve_proxy_call(
    tools: Mapping[str, ToolsetTool[AgentContext]],
    arguments: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Resolve a call envelope, leaving target validation/execution to ToolManager."""
    group, tool, values = arguments.get("group"), arguments.get("tool"), arguments.get("arguments")
    if not isinstance(group, str) or not isinstance(tool, str) or not isinstance(values, dict):
        raise ModelRetry("Supply group, tool, and an arguments object using the schema returned by proxy search.")
    if set(arguments) != {"group", "tool", "arguments"}:
        raise ModelRetry("Proxy calls accept only group, tool, and arguments.")
    directory = proxy_directory(tools)
    name = directory.get((group, tool))
    if name is None:
        groups = sorted({key[0] for key in directory})
        if group not in groups:
            raise ModelRetry(
                f"Group {group!r} is unavailable in this step. Search a current group before calling. Available groups: {', '.join(groups) or '(none)'}."
            )
        raise ModelRetry(f"Tool {group}/{tool} is unavailable in this step. Search again for its current schema.")
    return name, values


def active_proxy_tools(ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
    manager = ctx.tool_manager
    if manager is None or manager.tools is None:
        raise RuntimeError("ToolProxy requires the current prepared ToolManager")
    return manager.tools
