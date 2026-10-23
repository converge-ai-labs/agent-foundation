"""Retain called tools through Harness history replacement."""

from __future__ import annotations

import asyncio
import json
from secrets import token_urlsafe
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, ValidatedToolArgs
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextContent,
    ToolCallPart,
    ToolReturnPart,
    ToolSearchCallPart,
    ToolSearchReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import ToolsetTool

from a13n_harness.context import AgentContext
from a13n_harness.tools.identity import tool_identity
from a13n_harness.tools.tool_search import proxy_control, proxy_membership

_DISCOVERY_ID = "a13n.tool-discovery"
_MAX_TOOLS = 32
_MAX_CONTEXT_BYTES = 64 * 1024
_MAX_STATE_BYTES = 128 * 1024
_OMITTED_QUERY = "[query omitted during context replacement]"
_NOTICE = (
    "<retained-tool-discovery>\n"
    "Tool discovery was pruned during context replacement. The following synthetic search results were "
    "rebuilt from current definitions for a bounded subset of previously called tools; no search was executed. "
    "Original search queries were not retained and are replaced with an omission marker. "
    "Only the listed tools were retained. Search again if you need another tool. Each retained tool's schema "
    "is complete. Ordinary tool permissions and approvals still apply.\n"
    "</retained-tool-discovery>"
)


class _CalledTool(BaseModel):
    name: str
    tool_id: str


class _DiscoveryState(BaseModel):
    # Oldest call first. Search results and tool schemas stay out of portable state.
    called: list[_CalledTool] = Field(default_factory=list)


class ToolDiscoveryCapability(AbstractCapability[AgentContext]):
    """Remember completed target calls, not searches or runner envelopes."""

    id = _DISCOVERY_ID

    def __init__(self) -> None:
        self._state = _DiscoveryState()
        self._saved = self._state.model_dump_json()
        self._lock = asyncio.Lock()
        self._retained: list[_CalledTool] | None = None
        self._unprepared_completions: list[_CalledTool] = []

    def get_ordering(self) -> CapabilityOrdering:
        from a13n_harness.capabilities.context import CompactionCapability, HandoffCapability
        from a13n_harness.tools.invocation import ToolExecutionBoundaryCapability

        return CapabilityOrdering(
            position="outermost",
            wraps=(HandoffCapability, CompactionCapability),
            wrapped_by=(ToolExecutionBoundaryCapability,),
        )

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(_DISCOVERY_ID)
        if existing is not None:
            return existing
        owner = ToolDiscoveryCapability()
        owner._state = await ctx.deps.state.read(_DISCOVERY_ID, _DiscoveryState, version="1") or _DiscoveryState()
        owner._saved = owner._state.model_dump_json()
        ctx.deps._record_run_capability(_DISCOVERY_ID, owner)
        return owner

    async def _save(self, ctx: RunContext[AgentContext]) -> None:
        self._state.called = self._state.called[-_MAX_TOOLS:]
        while len(self._state.model_dump_json().encode()) > _MAX_STATE_BYTES:
            self._state.called.pop(0)
        serialized = self._state.model_dump_json()
        if serialized != self._saved:
            await ctx.deps.state.write(_DISCOVERY_ID, self._state, version="1")
            self._saved = serialized

    async def after_tool_execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        result: Any,
    ) -> Any:
        del call, args
        await self._record_call(ctx, tool_def)
        return result

    async def on_event(self, ctx: RunContext[AgentContext], *, event: Any) -> None:
        # Successful deferred results bypass the target's execution-completed hook.
        # Resolve their actual target before the boundary retires call associations.
        if (
            isinstance(event, FunctionToolResultEvent)
            and isinstance(event.part, ToolReturnPart)
            and event.part.outcome == "success"
        ):
            completed = _completed_deferred_tool(ctx, event.part)
            if completed is None:
                return
            definition = ctx.tools.get(completed.name)
            if definition is None:
                # Resumed results can settle before tools are prepared. Retain
                # the captured identity, not just a name that might be remounted.
                self._unprepared_completions.append(completed)
            elif tool_identity(definition).tool_id == completed.tool_id:
                await self._record_call(ctx, definition)

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        for completed in self._unprepared_completions:
            definition = ctx.tools.get(completed.name)
            if definition is not None and tool_identity(definition).tool_id == completed.tool_id:
                await self._record_call(ctx, definition)
        self._unprepared_completions.clear()
        return request_context

    async def _record_call(self, ctx: RunContext[AgentContext], tool_def: ToolDefinition) -> None:
        if not (
            tool_def.defer_loading or tool_def.with_native == "tool_search" or proxy_membership(tool_def) is not None
        ) or not _survives_replacement(ctx, tool_def):
            return
        async with self._lock:
            self._state.called = [item for item in self._state.called if item.name != tool_def.name]
            self._state.called.append(_CalledTool(name=tool_def.name, tool_id=tool_identity(tool_def).tool_id))
            await self._save(ctx)

    async def retain(
        self, ctx: RunContext[AgentContext], request: ModelRequestContext
    ) -> tuple[list[ModelMessage], set[str]]:
        from a13n_harness.toolsets.tool_search import proxy_discovery_entry

        tools = _tools(ctx)
        proxy_search = next((name for name, tool in tools.items() if proxy_control(tool.tool_def) == "search"), None)
        call_id = f"retained-{token_urlsafe(9)}"
        selected: list[_CalledTool] = []
        native: set[str] = set()
        proxy: list[dict[str, Any]] = []
        for item in reversed(self._state.called):
            tool = tools.get(item.name)
            if (
                tool is None
                or tool_identity(tool.tool_def).tool_id != item.tool_id
                or not _survives_replacement(ctx, tool.tool_def)
            ):
                continue
            if proxy_membership(tool.tool_def) is not None:
                if proxy_search is None:
                    continue
                proposed_native = native
                proposed_proxy = [*proxy, await proxy_discovery_entry(ctx, tool)]
            else:
                proposed_native = native | {item.name}
                proposed_proxy = proxy
            history = _discovery_messages(proposed_native, proposed_proxy, proxy_search, call_id)
            if _context_bytes(ctx, request, history, proposed_native) > _MAX_CONTEXT_BYTES:
                continue
            selected.append(item)
            native, proxy = proposed_native, proposed_proxy
        # Commit eviction only after the owning context capability installs history.
        self._retained = list(reversed(selected))
        return _discovery_messages(native, proxy, proxy_search, call_id), native


def _completed_deferred_tool(ctx: RunContext[AgentContext], part: ToolReturnPart) -> _CalledTool | None:
    """Resolve accepted and inline deferred completions without re-recording normal execution."""
    state = ctx.deps._deferred_input
    if state is not None and state.pending is not None:
        for item in state.pending.requests.calls:
            if item.model_call.tool_call_id == part.tool_call_id and item.model_call.tool_name == part.tool_name:
                return _CalledTool(name=item.target_call.tool_name, tool_id=item.target_identity)

    item = state.calls.get(part.tool_call_id) if state is not None else None
    if item is not None and item.model_call.tool_name != part.tool_name:
        return None
    name = item.target_call.tool_name if item is not None else part.tool_name
    definition = ctx.tools.get(name)
    if definition is None:
        return None
    identity = tool_identity(definition).tool_id
    if item is not None and item.target_identity != identity:
        return None
    metadata = state.metadata.get(item.target_call.tool_call_id, {}) if state is not None and item is not None else {}
    if definition.kind == "external" or metadata.get("a13n.harness.deferred-function-id") == identity:
        return _CalledTool(name=name, tool_id=identity)
    return None


def _discovery_messages(
    native: set[str], proxy: list[dict[str, Any]], proxy_search: str | None, call_id: str
) -> list[ModelMessage]:
    messages: list[ModelMessage] = [
        ModelRequest(
            parts=[UserPromptPart([TextContent(_NOTICE, metadata={"display": False, "source_id": _DISCOVERY_ID})])]
        )
    ]
    if native:
        names = sorted(native)
        messages.extend(
            [
                ModelResponse(parts=[ToolSearchCallPart(args={"queries": [_OMITTED_QUERY]}, tool_call_id=call_id)]),
                ModelRequest(
                    parts=[
                        ToolSearchReturnPart(
                            content={"discovered_tools": [{"name": name} for name in names]},
                            tool_call_id=call_id,
                        )
                    ]
                ),
            ]
        )
    if proxy:
        assert proxy_search is not None
        entries = sorted(proxy, key=lambda entry: (entry["group"], entry["tool"]))
        proxy_id = f"{call_id}-proxy"
        messages.extend(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(proxy_search, {"query": _OMITTED_QUERY, "detail": "full"}, tool_call_id=proxy_id)
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            proxy_search,
                            {"detail": "full", "tools": entries, "retained": True, "partial": True},
                            tool_call_id=proxy_id,
                        )
                    ]
                ),
            ]
        )
    return messages


def _tools(ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
    return ctx.tool_manager.tools if ctx.tool_manager is not None and ctx.tool_manager.tools is not None else {}


def _survives_replacement(ctx: RunContext[AgentContext], definition: ToolDefinition) -> bool:
    owner = ctx.capabilities.get(definition.capability_id) if definition.capability_id else None
    # load_capability exchanges have their own lifecycle and are not retained here.
    return not (owner is not None and owner.defer_loading) and ctx.is_tool_available(definition)


def replacement_tools(ctx: RunContext[AgentContext], names: set[str]) -> dict[str, ToolsetTool[AgentContext]]:
    return {
        name: tool
        for name, tool in _tools(ctx).items()
        if _survives_replacement(ctx, tool.tool_def)
        and (not (tool.tool_def.defer_loading or tool.tool_def.with_native == "tool_search") or name in names)
    }


def _context_bytes(
    ctx: RunContext[AgentContext], request: ModelRequestContext, messages: list[ModelMessage], names: set[str]
) -> int:
    from a13n_harness.toolsets.codeact import render_codeact_runner_description

    size = len(
        ModelMessagesTypeAdapter.dump_json(
            messages, exclude={"__all__": {"metadata": True, "parts": {"__all__": {"metadata": True}}}}
        )
    )
    tools = _tools(ctx)
    # One aggregated native search expands each selected schema once.
    for name in names:
        definition = tools[name].tool_def
        size += len(
            json.dumps(
                {
                    "name": name,
                    "description": definition.description,
                    "parameters": definition.parameters_json_schema,
                    "return": definition.return_schema,
                },
                ensure_ascii=False,
            ).encode()
        )
    baseline = replacement_tools(ctx, set())
    selected = replacement_tools(ctx, names)
    for definition in request.model_request_parameters.function_tools:
        metadata = definition.metadata or {}
        if metadata.get("a13n.codeact.runner") is True:
            program = metadata.get("a13n.codeact.program") is True
            size += max(
                0,
                len(render_codeact_runner_description(selected, ctx=ctx, program=program).encode())
                - len(render_codeact_runner_description(baseline, ctx=ctx, program=program).encode()),
            )
    return size


async def retain_discovery(
    ctx: RunContext[AgentContext], request: ModelRequestContext
) -> tuple[list[ModelMessage], set[str]]:
    owner = ctx.capabilities.get(_DISCOVERY_ID)
    if not isinstance(owner, ToolDiscoveryCapability):
        return [], set()
    return await owner.retain(ctx, request)


async def finish_discovery_replacement(ctx: RunContext[AgentContext]) -> None:
    owner = ctx.capabilities.get(_DISCOVERY_ID)
    if isinstance(owner, ToolDiscoveryCapability) and owner._retained is not None:
        owner._state.called = owner._retained
        owner._retained = None
        await owner._save(ctx)
