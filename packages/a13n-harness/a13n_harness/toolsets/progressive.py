"""Bounded discovery and invocation over the native prepared tool directory."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from functools import partial
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
    UserError,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDenied
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset

from a13n_harness._review_context import record_approval_denials
from a13n_harness.context import AgentContext
from a13n_harness.tools.exposure import (
    CONTROL_NAMES,
    DISCOVERY_CONTROL_KEY,
    EXPOSURE_CAPABILITY_ID,
    INVOCATION_TARGET_KEY,
    PROGRESSIVE_KEY,
    TARGET_VALIDATION,
    TargetValidation,
    ToolExposure,
    active_tools,
    discovery_control,
    executable_target,
    is_progressive,
    resolve_tool,
    toolset_exposure,
)
from a13n_harness.tools.search import keyword_rank, parameter_search_text, words

PAGE_SIZE = 20
MAX_RESPONSE_BYTES = 32768
MAX_DIRECTORY_BYTES = 8192
MAX_NAME_PREVIEW = 5


@dataclass
class ProgressiveToolset(WrapperToolset[AgentContext]):
    policy: ToolExposure

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = dict(await self.wrapped.get_tools(ctx))
        for name, tool in tuple(tools.items()):
            definition = tool.tool_def
            setting = toolset_exposure(definition, self.policy)
            if setting.resolve(name) == "direct":
                continue
            if not executable_target(definition):
                raise UserError(
                    f"Tool {name!r} cannot use progressive exposure; keep control and provider-native tools direct."
                )
            if setting.description is None:
                raise UserError(
                    f"Toolset {definition.toolset_id!r} needs a purpose description for progressive exposure."
                )
            tools[name] = replace(
                tool,
                tool_def=replace(
                    definition,
                    defer_loading=self.policy.progressive_search == "native",
                    with_native="tool_search" if self.policy.progressive_search == "native" else definition.with_native,
                    metadata={**(definition.metadata or {}), PROGRESSIVE_KEY: True},
                ),
            )
        if self.policy.progressive_search == "native" or not any(is_progressive(t.tool_def) for t in tools.values()):
            return tools
        conflicts = set(CONTROL_NAMES).intersection(tools)
        if conflicts:
            raise UserError(
                f"Progressive discovery control names conflict with source tools: {', '.join(sorted(conflicts))}"
            )
        from a13n_harness.toolsets.codeact import CodeActPolicyToolset, CodeActToolPolicy

        controls = [
            Tool(self.list_toolsets, metadata={DISCOVERY_CONTROL_KEY: "list_toolsets"}),
            Tool(self.list_tools, metadata={DISCOVERY_CONTROL_KEY: "list_tools"}),
            Tool(self.search_tools, metadata={DISCOVERY_CONTROL_KEY: "search_tools"}),
            Tool(self.describe_tools, metadata={DISCOVERY_CONTROL_KEY: "describe_tools"}),
            Tool(self.execute_tool, sequential=True, metadata={DISCOVERY_CONTROL_KEY: "execute_tool"}),
        ]
        prepared = await CodeActPolicyToolset(
            FunctionToolset(controls, id="a13n.discovery-controls"), CodeActToolPolicy(default=True)
        ).get_tools(ctx)
        return {**tools, **prepared}

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        if discovery_control(tool.tool_def) is not None:
            return await tool.toolset.call_tool(name, tool_args, ctx, tool)
        return await self.wrapped.call_tool(name, tool_args, ctx, tool)

    async def list_toolsets(
        self, ctx: RunContext[AgentContext], offset: Annotated[int, Field(ge=0)] = 0
    ) -> dict[str, Any]:
        """Browse a page of available Toolsets, their purposes, and progressive-tool counts. Continue with next_offset."""
        return page(toolset_entries(active_tools(ctx), self.policy), "toolsets", offset)

    async def list_tools(
        self, ctx: RunContext[AgentContext], toolset: str, offset: Annotated[int, Field(ge=0)] = 0
    ) -> dict[str, Any]:
        """Browse a page of progressive tools in one Toolset. Read definitions with describe_tools before invocation when needed."""
        tools = progressive_tools(active_tools(ctx), toolset)
        if not tools:
            raise ModelRetry(f"Toolset {toolset!r} has no available progressive tools. Use list_toolsets.")
        return page([summary(tool) for tool in tools], "tools", offset)

    async def search_tools(
        self,
        ctx: RunContext[AgentContext],
        query: Annotated[str, Field(min_length=1, max_length=2048)],
        toolset: str | None = None,
    ) -> dict[str, Any]:
        """Find tools by keywords or exact name, optionally within a Toolset. Returns ranked summaries. If keywords miss, browse with list_toolsets/list_tools."""
        tokens = set(words(query))
        matches: list[tuple[int, int, tuple[int, ...], str, ToolsetTool[AgentContext]]] = []
        for tool in progressive_tools(active_tools(ctx), toolset):
            definition = tool.tool_def
            exact = definition.name.casefold() == query.strip().casefold()
            fields = (
                definition.name,
                definition.description or "",
                definition.toolset_id or "",
                *parameter_search_text(definition.parameters_json_schema),
            )
            coverage, relevance = keyword_rank(tokens, fields)
            if exact or coverage:
                matches.append((-int(exact), -coverage, relevance, definition.name, tool))
        matches.sort(key=lambda entry: entry[:4])
        result = page([summary(entry[4]) for entry in matches], "tools", 0)
        result.pop("next_offset")
        result["partial"] = len(result["tools"]) < result["total"]
        return result

    async def describe_tools(
        self, ctx: RunContext[AgentContext], names: Annotated[list[str], Field(min_length=1, max_length=20)]
    ) -> dict[str, Any]:
        """Read complete current definitions for exact tool names. Returns argument schemas and needed source instructions; never executes tools."""
        entries = [await discovery_entry(ctx, resolve_tool(active_tools(ctx), name)) for name in dict.fromkeys(names)]
        result = {"tools": entries}
        if json_bytes(result) > MAX_RESPONSE_BYTES:
            raise ToolFailed(
                "Complete definitions exceed the response budget. Request fewer tools or ask the Host to reduce an oversized definition. No schema was truncated."
            )
        return result

    async def execute_tool(self, ctx: RunContext[AgentContext], name: str, arguments: dict[str, Any]) -> Any:
        """Invoke an available tool by exact name and arguments. Known names need no prior discovery. Preserves validation, permissions, approval, and client execution; this can have side effects."""
        tool = resolve_tool(active_tools(ctx), name)
        manager = ctx.tool_manager
        assert manager is not None
        if ctx.usage_limits is not None:
            projected = deepcopy(ctx.usage)
            projected.tool_calls += ctx.deps._tool_executions.usage_pending + 1
            ctx.usage_limits.check_before_tool_call(projected)
        call = ToolCallPart(name, arguments, tool_call_id=f"target-{uuid4().hex}")
        valid = False
        assert ctx.tool_call_id is not None
        capture = TargetValidation(call.tool_call_id, ctx.tool_call_id)
        token = TARGET_VALIDATION.set(capture)

        async def on_validate(success: bool) -> None:
            nonlocal valid
            valid = success

        try:
            result = await manager.handle_call(
                call,
                approved=ctx.tool_call_approved,
                metadata=ctx.tool_call_metadata,
                on_validate=on_validate,
                on_inline_deferred=partial(record_approval_denials, context=ctx.deps),
            )
        except ToolRetryError as exc:
            retry = exc.tool_retry
            if not valid:
                definition = await discovery_entry(ctx, tool)
                if json_bytes(definition) <= MAX_RESPONSE_BYTES:
                    retry = replace(
                        retry,
                        content=f"{retry.model_response()}\nCurrent definition: {json.dumps(definition, ensure_ascii=False)}",
                    )
            raise ToolRetryError(replace(retry, tool_name=ctx.tool_name, tool_call_id=ctx.tool_call_id)) from exc
        except ToolFailedError as exc:
            raise ToolFailedError(
                replace(exc.tool_failed, tool_name=ctx.tool_name or "execute_tool", tool_call_id=ctx.tool_call_id)
            ) from exc
        except (ApprovalRequired, CallDeferred) as exc:
            if capture.arguments is None:
                raise RuntimeError("Deferred invocation did not complete native argument validation") from exc
            metadata = {
                **ctx.deps._tool_pending_approvals.get(call.tool_call_id, {}),
                **(exc.metadata or {}),
                INVOCATION_TARGET_KEY: {
                    "name": name,
                    "arguments": capture.arguments,
                    "kind": tool.tool_def.kind,
                },
            }
            raise type(exc)(metadata=metadata) from exc
        finally:
            TARGET_VALIDATION.reset(token)
        if isinstance(result, ToolDenied):
            raise ToolFailed(f"Tool {name!r} was denied: {result.message}")
        return result


def progressive_tools(
    tools: Mapping[str, ToolsetTool[AgentContext]], toolset: str | None = None
) -> list[ToolsetTool[AgentContext]]:
    return [
        tool
        for name, tool in sorted(tools.items())
        if is_progressive(tool.tool_def) and (toolset is None or tool.tool_def.toolset_id == toolset)
    ]


def toolset_entries(tools: Mapping[str, ToolsetTool[AgentContext]], policy: ToolExposure) -> list[dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for tool in progressive_tools(tools):
        toolset = tool.tool_def.toolset_id
        assert toolset is not None
        entry = entries.setdefault(
            toolset,
            {
                "toolset": toolset,
                "description": toolset_exposure(tool.tool_def, policy).description,
                "count": 0,
            },
        )
        entry["count"] += 1
    return [entry for _, entry in sorted(entries.items())]


def directory(tools: Mapping[str, ToolsetTool[AgentContext]], policy: ToolExposure) -> str:
    entries = toolset_entries(tools, policy)
    lines = [
        f"Additional tools available on demand: {len(entries)} Toolsets, {sum(e['count'] for e in entries)} progressive tools.",
        "This directory replaces earlier tool directories.",
    ]
    route = "Browse list_toolsets/list_tools or search_tools; read describe_tools when needed, then execute_tool."
    if policy.progressive_search == "native":
        route = "Use provider tool search to discover definitions, then call the tools directly."
    budget = MAX_DIRECTORY_BYTES - len(route.encode()) - 200
    shown = 0
    for entry in entries:
        names = [tool.tool_def.name for tool in progressive_tools(tools, entry["toolset"])]
        preview = ", ".join(names[:MAX_NAME_PREVIEW])
        partial = " (partial preview)" if len(names) > MAX_NAME_PREVIEW else ""
        line = f"- {entry['toolset']} — {entry['description']}\n  {entry['count']} tools: {preview}{partial}."
        if len(("\n".join(lines) + line).encode()) > budget:
            break
        lines.append(line)
        shown += 1
    if shown < len(entries):
        lines.append(f"Partial directory: showing {shown} of {len(entries)} Toolsets.")
    lines.append(route)
    return "\n".join(lines)


def summary(tool: ToolsetTool[AgentContext]) -> dict[str, Any]:
    description = " ".join((tool.tool_def.description or "").split())
    if len(description) > 256:
        description = description[:255] + "…"
    return {"name": tool.tool_def.name, "toolset": tool.tool_def.toolset_id, "description": description}


def json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode())


def page(entries: list[dict[str, Any]], key: str, offset: int) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    result = {key: selected, "total": len(entries), "next_offset": None}
    for index, entry in enumerate(entries[offset : offset + PAGE_SIZE], offset):
        selected.append(entry)
        result["next_offset"] = index + 1 if index + 1 < len(entries) else None
        if json_bytes(result) > MAX_RESPONSE_BYTES:
            selected.pop()
            if not selected:
                raise ToolFailed("One complete entry exceeds the discovery response budget.")
            result["next_offset"] = index
            break
    return result


async def discovery_entry(ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]) -> dict[str, Any]:
    from a13n_harness.capabilities.exposure import ToolExposureCapability
    from a13n_harness.toolsets.codeact import is_codeact_tool_eligible

    definition = tool.tool_def
    owner = ctx.deps._run_capability(EXPOSURE_CAPABILITY_ID)
    instructions = (
        owner.instructions.get(definition.toolset_id or "", []) if isinstance(owner, ToolExposureCapability) else []
    )
    return {
        "name": definition.name,
        "description": definition.description,
        "parameters_json_schema": deepcopy(definition.parameters_json_schema),
        "return_schema": deepcopy(definition.return_schema),
        "codeact_eligible": is_codeact_tool_eligible(definition.name, tool),
        "instructions": instructions,
    }
