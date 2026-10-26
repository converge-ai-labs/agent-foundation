"""Native execution adapter for direct and one-target forwarded continuations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic_ai import RunContext, ToolApproved
from pydantic_ai.capabilities import WrapperCapability
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests as NativeDeferredToolRequests
from pydantic_ai.tools import DeferredToolResults, ToolDefinition
from pydantic_ai.toolsets import ToolsetTool

from a13n_harness._review_context import record_approval_denials
from a13n_harness.errors import DefinitionError
from a13n_harness.tools.deferred import DeferredToolCall
from a13n_harness.tools.identity import TOOL_IDENTITY_KEY, tool_identity

if TYPE_CHECKING:
    from a13n_harness.context import AgentContext

FORWARDED_TOOL_KEY = "a13n.harness.forwarded-tool"


@dataclass(frozen=True, slots=True)
class ToolCallForwarder:
    """Resolve one native target; completed target values are the caller's result."""

    resolve: Callable[[Mapping[str, ToolsetTool[AgentContext]], Mapping[str, Any]], tuple[str, dict[str, Any]]]


def capture_tool_call(ctx: RunContext[AgentContext], call: ToolCallPart, definition: ToolDefinition) -> None:
    """Capture identity at the native validation boundary and adapt its deferred hook."""
    manager = ctx.tool_manager
    if manager is not None and manager.root_capability is not None:
        if not isinstance(manager.root_capability, _DeferredDispatch):
            manager.root_capability = _DeferredDispatch(manager.root_capability)
    state = ctx.deps._deferred_input
    if state is not None and TOOL_IDENTITY_KEY in (definition.metadata or {}):
        identity = tool_identity(definition).tool_id
        state.calls.setdefault(call.tool_call_id, DeferredToolCall(deepcopy(call), deepcopy(call), identity))
        if definition.kind in {"function", "unapproved"} and FORWARDED_TOOL_KEY not in (definition.metadata or {}):
            # Capture before validation: inline approval can defer there without
            # executing the tool or revisiting the native deferred handler.
            state.metadata.setdefault(call.tool_call_id, {})["a13n.harness.deferred-function-id"] = identity


@dataclass
class _DeferredDispatch(WrapperCapability["AgentContext"]):
    """Delegate native handlers once for the actual call, never again for its envelope."""

    async def handle_deferred_tool_calls(
        self, ctx: RunContext[AgentContext], *, requests: NativeDeferredToolRequests
    ) -> DeferredToolResults | None:
        state = ctx.deps._deferred_input
        if state is None:
            return await self.wrapped.handle_deferred_tool_calls(ctx, requests=requests)

        def actual(parts: list[ToolCallPart]) -> list[ToolCallPart]:
            return [
                call for call in parts if (item := state.calls.get(call.tool_call_id)) is None or not item.forwarded
            ]

        remaining = NativeDeferredToolRequests(
            calls=actual(requests.calls), approvals=actual(requests.approvals), metadata=requests.metadata
        )
        if not remaining.calls and not remaining.approvals:
            return None
        result = await self.wrapped.handle_deferred_tool_calls(ctx, requests=remaining)
        for call in (*remaining.calls, *remaining.approvals):
            state.record_metadata(call.tool_call_id, remaining.metadata.get(call.tool_call_id, {}))
        return result


def validate_forwarded_call(item: DeferredToolCall, tools: Mapping[str, ToolsetTool[AgentContext]]) -> None:
    """The current caller must still resolve exactly the captured target and arguments."""
    if not item.forwarded:
        return
    caller = tools.get(item.model_call.tool_name)
    forwarder = (caller.tool_def.metadata or {}).get(FORWARDED_TOOL_KEY) if caller is not None else None
    try:
        if not isinstance(forwarder, ToolCallForwarder):
            raise ValueError("Caller no longer forwards a tool")
        name, arguments = forwarder.resolve(tools, item.model_call.args_as_dict())
        if name != item.target_call.tool_name or arguments != item.target_call.args_as_dict():
            raise ValueError("Target association changed")
    except Exception as exc:
        raise DefinitionError(
            "The current caller no longer resolves the pending target.", code="deferred_surface_mismatch"
        ) from exc


async def forward_tool_call(ctx: RunContext[AgentContext], name: str, arguments: dict[str, Any]) -> Any:
    """Execute through the existing manager, retaining only a serializable call association."""
    manager, state = ctx.tool_manager, ctx.deps._deferred_input
    assert manager is not None and manager.tools is not None and state is not None
    assert ctx.tool_call_id is not None
    model_call = state.calls[ctx.tool_call_id].model_call
    target = ToolCallPart(name, arguments, tool_call_id=f"call-{uuid4().hex}")
    approved = False
    metadata = None
    override_args = None
    resume = state.pending
    if resume is not None and not resume.recovery:
        for item in resume.requests.approvals:
            if item.model_call.tool_call_id != model_call.tool_call_id or not item.forwarded:
                continue
            validate_forwarded_call(item, manager.tools)
            target = deepcopy(item.target_call)
            decision = resume.results.approvals[target.tool_call_id]
            approved = decision is True or isinstance(decision, ToolApproved)
            if isinstance(decision, ToolApproved) and decision.override_args is not None:
                override_args = deepcopy(decision.override_args)
            metadata = resume.results.metadata.get(target.tool_call_id)
            break
    state.calls[model_call.tool_call_id] = DeferredToolCall(
        deepcopy(model_call), deepcopy(target), tool_identity(manager.tools[name].tool_def).tool_id
    )
    try:
        # Like native approvals, overrides affect validation/execution without
        # rewriting the originally requested call or its history association.
        return await manager.handle_call(
            replace(target, args=override_args) if override_args is not None else target,
            approved=approved,
            metadata=metadata,
            on_inline_deferred=partial(record_approval_denials, context=ctx.deps),
        )
    except (ApprovalRequired, CallDeferred) as exc:
        # The native graph suspends the original call. The Host-facing batch uses
        # the captured target and the same native approval/external-result category.
        state.record_metadata(target.tool_call_id, exc.metadata or {})
        raise
