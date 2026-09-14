"""Grouped discovery over ordinary Toolsets, with native nested execution."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Annotated, Any
from uuid import uuid4

from pydantic import Field
from pydantic_ai import FunctionToolset, ModelRetry, RunContext, Tool
from pydantic_ai.exceptions import (
    ApprovalRequired,
    CallDeferred,
    ToolFailed,
    ToolFailedError,
    ToolRetryError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UserError,
)
from pydantic_ai.messages import InstructionPart, ToolCallPart
from pydantic_ai.tools import ToolDenied
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool, WrapperToolset

from a13n_harness.context import AgentContext
from a13n_harness.tools.tool_proxy import (
    PROXY_CONTROL_KEY,
    PROXY_MEMBERSHIP_KEY,
    ToolProxyConfig,
    ToolProxyMembership,
    active_proxy_tools,
    proxy_control,
    proxy_directory,
    proxy_membership,
    resolve_proxy_call,
    validate_group,
    validate_proxy_target,
)
from a13n_harness.toolsets._instructions import tool_instruction
from a13n_harness.toolsets.codeact import (
    CodeActPolicyToolset,
    CodeActSourceToolCarrier,
    CodeActToolPolicy,
    is_codeact_tool_eligible,
    resolve_codeact_eligibility,
)


@dataclass(kw_only=True)
class _GroupTool(ToolsetTool[AgentContext]):
    source_tool: ToolsetTool[AgentContext]
    source_toolset: AbstractToolset[AgentContext]
    owner_local_name: str
    codeact_eligible: bool


@dataclass
class _GroupedToolset(WrapperToolset[AgentContext]):
    """Assign an arbitrary local Toolset to a flat ToolProxy group.

    Compose with ToolProxyCapability. Native lifecycle and dispatch stay on the
    wrapped Toolset; only names and model-facing discovery are changed.
    """

    group: str
    group_description: str
    source_label: str

    @property
    def label(self) -> str:
        return f"ToolProxy group {self.group!r} source {self.source_label!r}"

    @property
    def tool_name_conflict_hint(self) -> str:
        return "Give the source tools distinct names or select separate ToolProxy groups."

    def __post_init__(self) -> None:
        validate_group(self.group, self.group_description)

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        result: dict[str, ToolsetTool[AgentContext]] = {}
        for name, tool in (await self.wrapped.get_tools(ctx)).items():
            definition = tool.tool_def
            validate_proxy_target(definition)
            if proxy_membership(definition) is not None:
                raise UserError(f"Tool {name!r} already belongs to a ToolProxy group; wrap each source only once.")
            canonical = f"{self.group}__{name}"
            result[canonical] = _GroupTool(
                toolset=self,
                tool_def=replace(
                    definition,
                    name=canonical,
                    metadata={
                        **(definition.metadata or {}),
                        PROXY_MEMBERSHIP_KEY: ToolProxyMembership(self.group, self.group_description, name),
                    },
                ),
                max_retries=tool.max_retries,
                args_validator=tool.args_validator,
                args_validator_func=tool.args_validator_func,
                source_tool=tool,
                source_toolset=self.wrapped,
                owner_local_name=name,
                codeact_eligible=resolve_codeact_eligibility(name, tool),
            )
        return result

    async def get_instructions(self, ctx: RunContext[AgentContext]) -> None:
        # Source guidance is returned with discovery, not eagerly for every tool.
        return None

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        if not isinstance(tool, _GroupTool):
            raise TypeError("_GroupedToolset received a tool it does not own")
        source = tool.source_tool
        local_ctx = replace(ctx, tool_name=tool.owner_local_name)
        return await self.wrapped.call_tool(tool.owner_local_name, tool_args, local_ctx, source)


@dataclass
class ToolProxySurfaceToolset(WrapperToolset[AgentContext]):
    """Contribute the proxy controls after mandatory surface resolution."""

    config: ToolProxyConfig

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        directory = proxy_directory(tools)
        if not directory:
            return tools
        for name in (self.config.search_name, self.config.call_name):
            if name in tools:
                raise UserError(
                    f"ToolProxy entry name {name!r} conflicts with an existing tool; configure another name"
                )
        summary = _group_summary(tools)
        search = Tool(
            self._search,
            name=self.config.search_name,
            description=(
                f"Discover tools and exact argument schemas without executing them. Then use {self.config.call_name}. "
                "Omit group to search all groups; use an empty query to browse. Pagination is step-local.\n" + summary
            ),
            metadata={PROXY_CONTROL_KEY: "search"},
        )
        call = Tool(
            self._call,
            name=self.config.call_name,
            description=(
                f"Execute one tool discovered with {self.config.search_name}. Copy its exact group and tool name, "
                "and supply an arguments object matching its schema. Already-known current schemas can be reused. "
                "This call can have side effects; after uncertain failure inspect the outcome before retrying.\n"
                + summary
            ),
            sequential=True,
            metadata={PROXY_CONTROL_KEY: "call"},
        )
        controls = await CodeActPolicyToolset(
            FunctionToolset([search, call], id="tool-proxy-controls"), CodeActToolPolicy(default=True)
        ).get_tools(ctx)
        groups = sorted({group for group, _tool in directory})
        for name, prepared in controls.items():
            schema = deepcopy(prepared.tool_def.parameters_json_schema)
            if name == self.config.search_name:
                schema["properties"]["group"] = {
                    "anyOf": [{"type": "string", "enum": groups}, {"type": "null"}],
                    "default": None,
                    "description": "Limit discovery to one current group; omit to search all groups.",
                }
                schema["properties"]["limit"]["anyOf"][0]["maximum"] = self.config.max_results
            else:
                schema["properties"]["group"]["enum"] = groups
            controls[name] = replace(prepared, tool_def=replace(prepared.tool_def, parameters_json_schema=schema))
        return {**tools, **controls}

    async def get_instructions(
        self, ctx: RunContext[AgentContext]
    ) -> str | InstructionPart | Sequence[str | InstructionPart] | None:
        wrapped = await self.wrapped.get_instructions(ctx)
        if not ctx.deps.toolset_instructions or ctx.tool_manager is None or not ctx.tool_manager.tools:
            return wrapped
        if not proxy_directory(ctx.tool_manager.tools):
            return wrapped
        parts: list[str | InstructionPart] = []
        if isinstance(wrapped, str | InstructionPart):
            parts.append(wrapped)
        elif wrapped is not None:
            parts.extend(wrapped)
        parts.append(
            tool_instruction("tool_proxy").format(search_name=self.config.search_name, call_name=self.config.call_name)
        )
        return parts

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        role = proxy_control(tool.tool_def)
        if role == "search":
            return await self._search(ctx, **tool_args)
        if role == "call":
            return await self._call(ctx, **tool_args)
        return await self.wrapped.call_tool(name, tool_args, ctx, tool)

    async def _search(
        self,
        ctx: RunContext[AgentContext],
        query: Annotated[
            str, Field(max_length=2048, description="Keywords describing the operation; empty to browse.")
        ],
        group: Annotated[str | None, Field(description="Optional group; omit to search across groups.")] = None,
        limit: Annotated[
            int | None, Field(ge=1, le=100, description="Maximum matches; null uses the configured default.")
        ] = None,
        offset: Annotated[int, Field(ge=0, description="Offset into this query's current-step matches.")] = 0,
    ) -> dict[str, Any]:
        tools = active_proxy_tools(ctx)
        directory = proxy_directory(tools)
        if group is not None and not any(key[0] == group for key in directory):
            raise ModelRetry(
                f"Group {group!r} is unavailable in this step. Choose a current group or omit group to search all.\n{_group_summary(tools)}"
            )
        if limit is None:
            limit = min(5, self.config.max_results)
        if limit > self.config.max_results:
            raise ModelRetry(f"limit must not exceed {self.config.max_results}")
        tokens = re.findall(r"\w+", query.casefold())
        matches: list[tuple[int, str, ToolsetTool[AgentContext]]] = []
        for (group_name, local_name), canonical in directory.items():
            if group is not None and group_name != group:
                continue
            prepared = tools[canonical]
            haystack = f"{group_name} {local_name} {prepared.tool_def.description or ''}".casefold()
            score = sum(token in haystack for token in tokens)
            if tokens and not score:
                continue
            matches.append((-score, canonical, prepared))
        matches.sort(key=lambda item: (item[0], item[1]))
        entries: list[dict[str, Any]] = []
        result: dict[str, Any] = {"tools": entries, "total": len(matches), "next_offset": None}
        for index, (_, canonical, prepared) in enumerate(matches[offset : offset + limit], offset):
            member = proxy_membership(prepared.tool_def)
            assert member is not None
            entry = {
                "group": member.group,
                "tool": member.tool,
                "description": prepared.tool_def.description,
                "parameters_json_schema": deepcopy(prepared.tool_def.parameters_json_schema),
                "return_schema": deepcopy(prepared.tool_def.return_schema),
                "codeact_eligible": is_codeact_tool_eligible(canonical, prepared),
                "instructions": await _source_instructions(ctx, prepared),
            }
            entries.append(entry)
            next_offset = index + 1
            result["next_offset"] = next_offset if next_offset < len(matches) else None
            if len(json.dumps(result, ensure_ascii=False).encode()) > self.config.max_search_bytes:
                entries.pop()
                if not entries:
                    raise ToolFailed(
                        f"The full schema and instructions for {member.group}/{member.tool} exceed the proxy search "
                        "budget. Ask the Host to expose this tool directly or reduce its schema; no partial schema was returned."
                    )
                result["next_offset"] = index
                break
        return result

    async def _call(
        self,
        ctx: RunContext[AgentContext],
        group: Annotated[str, Field(description="Exact group from proxy discovery.")],
        tool: Annotated[str, Field(description="Exact tool name returned by proxy discovery, not a top-level tool.")],
        arguments: Annotated[
            dict[str, Any], Field(description="Arguments matching the discovered tool's current schema.")
        ],
    ) -> Any:
        tools = active_proxy_tools(ctx)
        name, values = resolve_proxy_call(tools, {"group": group, "tool": tool, "arguments": arguments})
        manager = ctx.tool_manager
        assert manager is not None
        if ctx.usage_limits is not None:
            projected = deepcopy(ctx.usage)
            projected.tool_calls += 2  # This outer envelope and one native target call.
            ctx.usage_limits.check_before_tool_call(projected)
        call = ToolCallPart(name, values, tool_call_id=f"proxy-{uuid4().hex}")
        try:
            result = await manager.handle_call(call)
        except ToolRetryError as exc:
            # Native target retries remain keyed by the original prepared name.
            # Re-correlate only the model-facing message; rethrowing ModelRetry
            # here would also consume the shared envelope's unrelated budget.
            raise ToolRetryError(
                replace(exc.tool_retry, tool_name=ctx.tool_name, tool_call_id=ctx.tool_call_id)
            ) from exc
        except ToolFailedError as exc:
            raise ToolFailedError(
                replace(
                    exc.tool_failed, tool_name=ctx.tool_name or self.config.call_name, tool_call_id=ctx.tool_call_id
                )
            ) from exc
        except (ApprovalRequired, CallDeferred):
            raise ToolFailed(
                f"Tool {group}/{tool} requires unresolved Host interaction. Expose it directly for cross-turn approval "
                "or external execution. Do not assume completion or blindly retry."
            ) from None
        except (ModelRetry, ToolFailed, UsageLimitExceeded, UnexpectedModelBehavior):
            raise
        except Exception as exc:
            raise ToolFailed(
                f"Tool {group}/{tool} failed with {type(exc).__name__}; its outcome may be uncertain. "
                "Inspect external state before retrying a mutation."
            ) from None
        if isinstance(result, ToolDenied):
            raise ToolFailed(f"Tool {group}/{tool} was denied: {result.message}")
        return result


def _group_summary(tools: dict[str, ToolsetTool[AgentContext]]) -> str:
    groups = {
        member.group: member.description
        for tool in tools.values()
        if (member := proxy_membership(tool.tool_def)) is not None
    }
    return "Available groups:\n" + "\n".join(f"- {name}: {description}" for name, description in sorted(groups.items()))


async def _source_instructions(ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]) -> list[str]:
    if not ctx.deps.toolset_instructions:
        return []
    current = tool
    while isinstance(current, CodeActSourceToolCarrier):
        if isinstance(current, _GroupTool):
            value = await current.source_toolset.get_instructions(ctx)
            if value is None:
                return []
            if isinstance(value, str | InstructionPart):
                return [value.content if isinstance(value, InstructionPart) else value]
            return [part.content if isinstance(part, InstructionPart) else part for part in value]
        current = current.source_tool
    return []
