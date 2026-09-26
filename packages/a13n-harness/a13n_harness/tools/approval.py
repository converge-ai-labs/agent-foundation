"""Native call-local approval and advisory Host presentation metadata."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired

if TYPE_CHECKING:
    from a13n_harness.context import AgentContext

APPROVAL_PRESENTATION_KEY = "a13n.harness.approval-presentation"
type ApprovalReasonKind = Literal["permission", "policy", "review", "review_error"]
type ApprovalReason = Literal[
    "Tool permission configuration requires approval.",
    "Tool policy requires approval.",
    "Tool reviewer requires approval.",
    "Tool review could not complete.",
]
_APPROVAL_REASONS: dict[ApprovalReasonKind, ApprovalReason] = {
    "permission": "Tool permission configuration requires approval.",
    "policy": "Tool policy requires approval.",
    "review": "Tool reviewer requires approval.",
    "review_error": "Tool review could not complete.",
}


class ApprovalPresentation(BaseModel):
    """Bounded, redacted details safe for a Host approval prompt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_id: str | None = None
    target: str = Field(default="", max_length=240)
    reason: ApprovalReason | None = None
    risk: Literal["low", "medium", "high", "extra_high"] | None = None


def approval_presentation(
    arguments: Mapping[str, Any],
    *,
    tool_id: str | None = None,
    reason: ApprovalReasonKind | None = None,
    risk: Literal["low", "medium", "high", "extra_high"] | None = None,
) -> dict[str, JsonValue]:
    """Project useful approval context without retaining complete arguments."""

    return ApprovalPresentation(
        tool_id=tool_id,
        target=approval_target(arguments),
        reason=_APPROVAL_REASONS[reason] if reason is not None else None,
        risk=risk,
    ).model_dump(mode="json", exclude_none=True)


def compact_target(arguments: Mapping[str, Any]) -> str:
    """Return one bounded locator or command preview with bearer values redacted."""

    from a13n_harness._json import redact_bearer

    for key in ("file_path", "path", "url", "command", "cwd", "name"):
        value = arguments.get(key)
        if isinstance(value, str):
            text = " ".join(redact_bearer(value).split())
            return f"{key}: {text[:200]}" + (" [truncated]" if len(text) > 200 else "")
    return ""


def approval_target(arguments: Mapping[str, Any]) -> str:
    """Show a locator without URL credentials or command arguments."""

    for key in ("file_path", "path", "cwd", "name"):
        if isinstance(arguments.get(key), str):
            return compact_target({key: arguments[key]})
    url = arguments.get("url")
    if isinstance(url, str):
        try:
            parts = urlsplit(url)
        except ValueError:
            return "url: [invalid URL]"
        host = parts.netloc.rsplit("@", 1)[-1]
        return compact_target({"url": urlunsplit((parts.scheme, host, parts.path, "", ""))})
    if isinstance(arguments.get("command"), str):
        return "command: [arguments hidden]"
    return ""


@dataclass(frozen=True, slots=True)
class ToolApprovalContext:
    """The Host native approval decision for this call, not model claims."""

    tool_id: str
    tool_call_id: str
    approved: bool = False


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


def resolve_tool_approval(ctx: RunContext[AgentContext], *, tool_id: str) -> ToolApprovalContext:
    """Trust structured native approval, without authenticating its history."""
    recovery = ctx.deps._tool_recovery
    retrying = recovery is not None and ctx.tool_call_id in recovery.pending
    # Recovery uses native approval to select replay, not as a Host decision.
    return ToolApprovalContext(tool_id, ctx.tool_call_id or "", ctx.tool_call_approved and not retrying)


def approval_required(
    ctx: RunContext[AgentContext],
    approval: ToolApprovalContext,
    *,
    metadata: dict[str, JsonValue] | None = None,
) -> ApprovalRequired:
    """Suspend with display context; metadata is not an approval grant."""
    request_metadata = dict(metadata or {})
    ctx.deps._tool_pending_approvals[approval.tool_call_id] = request_metadata
    return ApprovalRequired(metadata=request_metadata)
