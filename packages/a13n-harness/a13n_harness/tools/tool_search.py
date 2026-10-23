"""Passive ToolSearch membership and resolution against the native tool directory."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import ModelRetry, RunContext, ToolDefinition
from pydantic_ai.exceptions import UserError
from pydantic_ai.toolsets import ToolsetTool

from a13n_harness.context import AgentContext

PROXY_MEMBERSHIP_KEY = "a13n.tool_search.member"
PROXY_CONTROL_KEY = "a13n.tool_search.control"


@dataclass(frozen=True, slots=True)
class ProxyToolSearchConfig:
    """Names and bounded discovery settings for one Agent's proxy surface."""

    # The mode tag remains accepted for existing proxy configurations.
    __pydantic_config__ = ConfigDict(extra="forbid")

    mode: Literal["proxy"] = field(default="proxy", kw_only=True)
    search_name: str = "discover_tools"
    call_name: str = "call_tool"
    max_results: Annotated[int, Field(strict=True)] = 10
    max_search_bytes: Annotated[int, Field(strict=True)] = 32_768
    describe_name: str = "describe_tool"
    list_name: str = "list_tools"
    groups_name: str = "list_tool_groups"

    def __post_init__(self) -> None:
        if self.mode != "proxy":
            raise ValueError("ProxyToolSearchConfig.mode must be 'proxy'")
        names = (self.search_name, self.describe_name, self.call_name, self.list_name, self.groups_name)
        for name in names:
            if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name) is None:
                raise ValueError(
                    "ToolSearch entry names must start with a letter or underscore and contain 1-64 letters, digits, underscores, or hyphens."
                )
        if len(set(names)) != len(names):
            raise ValueError("ToolSearch control names must differ")
        if (
            isinstance(self.max_results, bool)
            or not isinstance(self.max_results, int)
            or not 1 <= self.max_results <= 100
        ):
            raise ValueError("ToolSearch max_results must be an integer between 1 and 100")
        if (
            isinstance(self.max_search_bytes, bool)
            or not isinstance(self.max_search_bytes, int)
            or not 1024 <= self.max_search_bytes <= 32_768
        ):
            raise ValueError("ToolSearch max_search_bytes must be an integer between 1024 and 32768")


type ToolSearchConfig = ProxyToolSearchConfig


class ToolSearchConfiguration(BaseModel):
    """Shared settings for host tool search configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    config: ToolSearchConfig = Field(default_factory=ProxyToolSearchConfig)


@dataclass(frozen=True, slots=True)
class ToolSearchMembership:
    """One prepared tool's presentation identity, not a dispatch binding."""

    group: str
    description: str
    tool: str


def validate_group(group: str, description: str) -> None:
    if not isinstance(group, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", group) is None or "__" in group:
        raise ValueError(
            "ToolSearch group must start with a letter and contain 1-32 letters, digits, underscores, or hyphens, without '__'."
        )
    if not isinstance(description, str) or not description.strip() or len(description) > 512:
        raise ValueError("ToolSearchGroup.description must be non-blank and at most 512 characters.")


def proxy_membership(definition: ToolDefinition) -> ToolSearchMembership | None:
    value = (definition.metadata or {}).get(PROXY_MEMBERSHIP_KEY)
    if value is None:
        return None
    if not isinstance(value, ToolSearchMembership):
        raise TypeError(
            f"Invalid ToolSearch membership on {definition.name!r}; configure ToolSearchCapability.groups instead of writing reserved metadata."
        )
    return value


def proxy_control(definition: ToolDefinition) -> str | None:
    value = (definition.metadata or {}).get(PROXY_CONTROL_KEY)
    return value if isinstance(value, str) else None


def grouped_tool_name(group: str, name: str) -> str:
    """Keep the group delimiter unambiguous even at underscore boundaries."""
    return f"{group.replace('_', '_u')}__{name}"


def validate_proxy_target(definition: ToolDefinition) -> None:
    """Validate after preparation too: native wrappers can add deferral flags later."""
    if definition.defer_loading:
        raise UserError(
            f"Tool {definition.name!r} uses deferred loading; remove defer_loading=True from the grouped source or leave it outside ToolSearch."
        )
    if definition.unless_native:
        raise UserError(
            f"Tool {definition.name!r} is a provider-native fallback; select native=False, local=True for grouped MCP tools."
        )
    if (
        definition.kind not in {"function", "unapproved", "external"}
        or definition.tool_kind is not None
        or proxy_control(definition) is not None
        or (definition.metadata or {}).get("a13n.codeact.runner") is True
    ):
        raise UserError(
            f"Tool {definition.name!r} is not a function or external tool supported by ToolSearch. Leave control, output, and provider-native tools directly exposed."
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
                f"Duplicate ToolSearch tool {member.group}/{member.tool}; rename one source tool or assign it to another group."
            )
        if member.group in descriptions and descriptions[member.group] != member.description:
            raise ValueError(
                f"Conflicting descriptions for ToolSearch group {member.group!r}; use the same group_description for all sources in this group."
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
        raise ModelRetry("Supply group, tool, and an arguments object matching the tool's complete current schema.")
    if set(arguments) != {"group", "tool", "arguments"}:
        raise ModelRetry("Tool calls accept only group, tool, and arguments.")
    return resolve_proxy_tool(tools, group, tool), values


def resolve_proxy_tool(tools: Mapping[str, ToolsetTool[AgentContext]], group: str, tool: str) -> str:
    """Resolve an exact current identity for either description or execution."""
    directory = proxy_directory(tools)
    name = directory.get((group, tool))
    if name is None:
        groups = sorted({key[0] for key in directory})
        if group not in groups:
            raise ModelRetry(
                f"Group {group!r} is unavailable in this step. Choose a current group. Available groups: {', '.join(groups) or '(none)'}."
            )
        raise ModelRetry(
            f"Tool {group}/{tool} is unavailable in this step. Rediscover available tools and use an exact current group and local tool name."
        )
    return name


def active_proxy_tools(ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
    manager = ctx.tool_manager
    if manager is None or manager.tools is None:
        raise RuntimeError("ToolSearch requires the current prepared ToolManager")
    return manager.tools
