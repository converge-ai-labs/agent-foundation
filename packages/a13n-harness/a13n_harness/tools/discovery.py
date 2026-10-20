"""Bounded discovery evidence carried through Harness history replacement."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, ValidatedToolArgs
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    NativeToolSearchCallPart,
    NativeToolSearchReturnPart,
    ToolCallPart,
    ToolReturnPart,
    ToolSearchReturnPart,
    UserPromptPart,
    post_compaction_window,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import ToolsetTool

from a13n_harness.context import AgentContext
from a13n_harness.tools.identity import tool_identity
from a13n_harness.tools.tool_search import proxy_control, proxy_directory, proxy_membership

_DISCOVERY_ID = "a13n.tool-discovery"
_EVIDENCE_KEY = "a13n.discovery-identities"
_NESTED_KEY = "a13n.nested-discovery"
_MAX_TOOLS = 32
_MAX_CONTEXT_BYTES = 64 * 1024
_MAX_STATE_BYTES = 128 * 1024


class _ProxyDiscovery(BaseModel):
    call: ToolCallPart
    result: dict[str, Any]
    identities: dict[str, str]


class _DiscoveryState(BaseModel):
    recent: list[str] = Field(default_factory=list)
    # Only nested discoveries absent from canonical history remain here.
    proxy: list[_ProxyDiscovery] = Field(default_factory=list)


@dataclass
class _Exchange:
    messages: list[ModelMessage]
    identities: dict[str, str]
    proxy: dict[str, Any] | None = None


class ToolDiscoveryCapability(AbstractCapability[AgentContext]):
    """Track recency, leaving ordinary discovery exchanges in native history."""

    id = _DISCOVERY_ID

    def __init__(self) -> None:
        self._state = _DiscoveryState()
        self._saved = self._state.model_dump_json()
        self._lock = asyncio.Lock()

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

    def _touch(self, names: list[str]) -> None:
        self._state.recent = [name for name in self._state.recent if name not in names] + list(dict.fromkeys(names))
        self._state.recent = self._state.recent[-_MAX_TOOLS:]

    async def _save(self, ctx: RunContext[AgentContext]) -> None:
        rank = {name: index for index, name in enumerate(self._state.recent)}
        self._state.proxy.sort(key=lambda item: max((rank.get(name, -1) for name in item.identities), default=-1))
        self._state.proxy = self._state.proxy[-_MAX_TOOLS:]
        while len(self._state.model_dump_json().encode()) > _MAX_STATE_BYTES:
            if self._state.proxy:
                self._state.proxy.pop(0)
            else:
                self._state.recent.pop(0)
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
        del args
        async with self._lock:
            if proxy_control(tool_def) == "search" and isinstance(result, dict):
                tools = _tools(ctx)
                directory = proxy_directory(tools)
                identities = {
                    name: tool_identity(tools[name].tool_def).tool_id
                    for entry in result["tools"]
                    if (name := directory.get((entry["group"], entry["tool"]))) is not None
                }
                if identities:
                    self._touch(list(identities))
                    self._state.proxy.append(
                        _ProxyDiscovery(call=deepcopy(call), result=deepcopy(result), identities=identities)
                    )
            elif tool_def.defer_loading or proxy_membership(tool_def) is not None:
                # Native dispatch also observes canonical targets inside CodeAct/proxy calls.
                self._touch([call.tool_name])
            else:
                return result
            await self._save(ctx)
        return result

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        # Same-agent compaction runs share capability state but cannot advance its LRU.
        if ctx.run_id != ctx.deps._model_recovery.attempt_id:
            return request_context
        async with self._lock:
            self._exchanges(ctx, request_context.messages, observe=True)
            await self._save(ctx)
        return request_context

    async def after_model_request(
        self, ctx: RunContext[AgentContext], *, request_context: ModelRequestContext, response: ModelResponse
    ) -> ModelResponse:
        # Provider-side search may share a final answer, with no following request.
        if ctx.run_id == ctx.deps._model_recovery.attempt_id:
            async with self._lock:
                for part in response.parts:
                    if isinstance(part, NativeToolSearchReturnPart) and part.outcome == "success":
                        self._observe_native(ctx, part)
                await self._save(ctx)
        return response

    def _observe_native(
        self, ctx: RunContext[AgentContext], part: ToolSearchReturnPart | NativeToolSearchReturnPart
    ) -> dict[str, str]:
        tools = _tools(ctx)
        names = [match["name"] for match in part.discovered_tools]
        identities = (
            {name: tool_identity(tools[name].tool_def).tool_id for name in names}
            if all(name in tools for name in names)
            else {}
        )
        metadata = part.metadata if isinstance(part.metadata, dict) else {}
        part.metadata = {**metadata, _EVIDENCE_KEY: identities}
        self._touch(list(identities))
        return identities

    def _exchanges(
        self, ctx: RunContext[AgentContext], messages: list[ModelMessage], *, observe: bool = False
    ) -> list[_Exchange]:
        calls: dict[str, tuple[ModelResponse, ToolCallPart | NativeToolSearchCallPart]] = {}
        pending = {item.call.tool_call_id: item for item in self._state.proxy}
        exchanges: list[_Exchange] = []
        for message in post_compaction_window(messages):
            nested = (message.metadata or {}).get(_NESTED_KEY)
            if nested is not None:
                item = _ProxyDiscovery.model_validate(nested)
                exchanges.append(_Exchange([deepcopy(message)], item.identities, item.result))
                pending.pop(item.call.tool_call_id, None)
            for part in message.parts:
                if isinstance(message, ModelResponse) and isinstance(part, ToolCallPart | NativeToolSearchCallPart):
                    calls[part.tool_call_id] = (message, part)
                if not isinstance(part, ToolSearchReturnPart | NativeToolSearchReturnPart | ToolReturnPart):
                    continue
                proxy = pending.get(part.tool_call_id)
                metadata = part.metadata if isinstance(part.metadata, dict) else {}
                identities = metadata.get(_EVIDENCE_KEY)
                if observe and identities is None:
                    if isinstance(part, ToolSearchReturnPart | NativeToolSearchReturnPart):
                        identities = self._observe_native(ctx, part)
                    elif proxy is not None:
                        identities = proxy.identities
                    if identities is not None:
                        part.metadata = {**metadata, _EVIDENCE_KEY: identities}
                if not identities or part.outcome != "success" or part.tool_call_id not in calls:
                    continue
                response, call = calls[part.tool_call_id]
                # Retain evidence, not the old response's usage or provider continuation ID.
                replay = ModelResponse(
                    parts=[deepcopy(call)],
                    model_name=response.model_name,
                    provider_name=response.provider_name,
                )
                retained: list[ModelMessage] = [replay]
                if isinstance(part, NativeToolSearchReturnPart):
                    replay.parts = [*replay.parts, deepcopy(part)]
                else:
                    retained.append(ModelRequest(parts=[deepcopy(part)]))
                proxy_result = (
                    part.content if not isinstance(part, ToolSearchReturnPart | NativeToolSearchReturnPart) else None
                )
                exchanges.append(
                    _Exchange(retained, identities, dict(proxy_result) if isinstance(proxy_result, dict) else None)
                )
                pending.pop(part.tool_call_id, None)
        if observe:
            self._state.proxy = list(pending.values())
        for item in pending.values():
            content = (
                "<retained-tool-discovery>\n"
                "A completed ToolSearch call inside CodeAct returned the following discovery. "
                "This is retained tool output, not a new instruction or a replayed execution.\n"
                + json.dumps({"call": item.call.args_as_dict(), "result": item.result}, ensure_ascii=False)
                + "\n</retained-tool-discovery>"
            )
            exchanges.append(
                _Exchange(
                    [
                        ModelRequest(
                            parts=[UserPromptPart(content)], metadata={_NESTED_KEY: item.model_dump(mode="json")}
                        )
                    ],
                    item.identities,
                    item.result,
                )
            )
        return exchanges

    async def retain(
        self, ctx: RunContext[AgentContext], request: ModelRequestContext
    ) -> tuple[list[ModelMessage], set[str]]:
        from a13n_harness.toolsets.tool_search import proxy_discovery_entry

        tools = _tools(ctx)
        rank = {name: index for index, name in enumerate(self._state.recent)}
        chronological = self._exchanges(ctx, request.messages)
        candidates = list(reversed(chronological))
        candidates.sort(
            key=lambda item: max((rank.get(name, -1) for name in item.identities), default=-1), reverse=True
        )
        selected: list[_Exchange] = []
        names: set[str] = set()
        native_names: set[str] = set()
        for item in candidates:
            if not (set(item.identities) - names):
                continue
            if any(
                name not in tools
                or tool_identity(tools[name].tool_def).tool_id != identity
                or not _survives_replacement(ctx, tools[name].tool_def)
                for name, identity in item.identities.items()
            ):
                continue
            if item.proxy is not None:
                if any(proxy_membership(tools[name].tool_def) is None for name in item.identities):
                    continue
                current = [await proxy_discovery_entry(ctx, tools[name]) for name in item.identities]
                if current != item.proxy["tools"]:
                    continue
            proposed = names | item.identities.keys()
            proposed_native = native_names | (item.identities.keys() if item.proxy is None else set())
            history = [message for entry in [*selected, item] for message in entry.messages]
            if (
                len(proposed) > _MAX_TOOLS
                or _context_bytes(ctx, request, history, proposed_native) > _MAX_CONTEXT_BYTES
            ):
                continue
            selected.append(item)
            names = proposed
            native_names = proposed_native
        # Selection follows LRU; replay remains chronological for stable provider history.
        selected_ids = {id(item) for item in selected}
        history = [message for item in chronological if id(item) in selected_ids for message in item.messages]
        return history, native_names


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

    size = len(ModelMessagesTypeAdapter.dump_json(messages))
    tools = _tools(ctx)
    # Responses can expand a schema once per search output, including overlaps.
    occurrences = [
        match["name"]
        for message in messages
        for part in message.parts
        if isinstance(part, ToolSearchReturnPart | NativeToolSearchReturnPart)
        for match in part.discovered_tools
    ]
    for name in occurrences:
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
    if isinstance(owner, ToolDiscoveryCapability) and owner._state.proxy:
        # Selected nested evidence is now in canonical history. Evicted evidence
        # must not reappear at a later replacement from the supporting cache.
        owner._state.proxy.clear()
        await owner._save(ctx)
