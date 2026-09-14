"""Bind deferred approvals to the mutable resource facts used at authorization."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import JsonValue
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired

if TYPE_CHECKING:
    from a13n_harness.context import AgentContext

from pydantic_ai.exceptions import ToolFailed

from .policy import ToolInvocationContext

RESOURCE_APPROVAL_KEY = "a13n.resource-approval"


def approval_facts(invocation: ToolInvocationContext) -> dict[str, JsonValue] | None:
    revisions: list[JsonValue] = [
        {"namespace": resource.namespace, "kind": resource.kind, "revision": resource.approval_revision}
        for resource in invocation.resources
        if resource.approval_revision is not None
    ]
    if not revisions:
        return None
    return {"tool_id": invocation.tool_id, "arguments": invocation.arguments_digest, "resources": revisions}


def verify_approval_facts(invocation: ToolInvocationContext, metadata: object) -> None:
    expected = metadata.get(RESOURCE_APPROVAL_KEY) if isinstance(metadata, Mapping) else None
    current = approval_facts(invocation)
    if expected != current:
        raise ToolFailed("Approved resources changed. Inspect the current environment and request approval again.")


TOOL_APPROVAL_KEY = "a13n.harness.tool-approval"
# Advisory provenance for native declarative approval, not an authority envelope.
NATIVE_TOOL_APPROVAL_KEY = "a13n.harness.native-tool-approval"
type ApprovalSource = Literal["permission", "reviewer", "tool"]
_APPROVAL_SOURCES = frozenset({"permission", "reviewer", "tool"})


@dataclass(frozen=True, slots=True)
class ToolApprovalContext:
    """Verified human approvals for this call, not automatic policy decisions."""

    tool_id: str
    tool_call_id: str
    approved_sources: frozenset[ApprovalSource] = frozenset()


_APPROVAL_CONTEXT: ContextVar[tuple[AgentContext, ToolApprovalContext] | None] = ContextVar(
    "a13n.tool-approval", default=None
)


def current_tool_approval(context: AgentContext) -> ToolApprovalContext | None:
    current = _APPROVAL_CONTEXT.get()
    return current[1] if current is not None and current[0] is context else None


@contextmanager
def tool_approval_scope(context: AgentContext, approval: ToolApprovalContext) -> Iterator[None]:
    token = _APPROVAL_CONTEXT.set((context, approval))
    try:
        yield
    finally:
        _APPROVAL_CONTEXT.reset(token)


def resolve_tool_approval(ctx: RunContext[AgentContext], *, tool_id: str, binding: str) -> ToolApprovalContext:
    """Use Host-captured pending metadata, never model arguments or feedback claims."""
    call_id = ctx.tool_call_id or ""
    metadata = pending_approval_metadata(ctx).get(TOOL_APPROVAL_KEY)
    if metadata is None:
        # An upstream approval predating provenance is a native tool approval.
        return ToolApprovalContext(tool_id, call_id, frozenset({"tool"}) if ctx.tool_call_approved else frozenset())
    if not isinstance(metadata, dict) or metadata.get("binding") != binding or metadata.get("tool_id") != tool_id:
        raise ToolFailed("Approved tool or arguments changed; request approval again.")
    approved = _approval_sources(metadata.get("approved_sources"))
    requested = _approval_sources(metadata.get("requested_sources"))
    if not requested:
        raise ToolFailed("Tool approval provenance is invalid.")
    if ctx.tool_call_approved:
        approved |= requested
    return ToolApprovalContext(tool_id, call_id, approved)


def pending_approval_metadata(ctx: RunContext[AgentContext]) -> dict[str, JsonValue]:
    """Read pending evidence captured by Harness, never handler result metadata."""
    call_id = ctx.tool_call_id or ""
    if call_id in ctx.deps._tool_pending_approvals:
        return ctx.deps._tool_pending_approvals[call_id]
    if ctx.deps.deferred_resume is not None:
        return ctx.deps.deferred_resume.requests.metadata.get(call_id, {})
    return {}


def approval_required(
    ctx: RunContext[AgentContext],
    approval: ToolApprovalContext,
    *,
    binding: str,
    sources: frozenset[ApprovalSource],
    metadata: dict[str, JsonValue] | None = None,
) -> ApprovalRequired:
    """Carry earlier approvals through another native suspension of the same call."""
    envelope: dict[str, JsonValue] = {
        "tool_id": approval.tool_id,
        "binding": binding,
        "approved_sources": [source for source in sorted(approval.approved_sources)],
        "requested_sources": [source for source in sorted(sources)],
    }
    request_metadata: dict[str, JsonValue] = {
        **pending_approval_metadata(ctx),
        **(metadata or {}),
        TOOL_APPROVAL_KEY: envelope,
    }
    ctx.deps._tool_pending_approvals[approval.tool_call_id] = request_metadata
    return ApprovalRequired(metadata=request_metadata)


def _approval_sources(value: object) -> frozenset[ApprovalSource]:
    if not isinstance(value, list) or any(not isinstance(item, str) or item not in _APPROVAL_SOURCES for item in value):
        raise ToolFailed("Tool approval provenance is invalid.")
    return frozenset(value)
