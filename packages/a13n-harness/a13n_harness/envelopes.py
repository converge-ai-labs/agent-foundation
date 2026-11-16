"""Shared input vocabulary; Hosts own delivery, persistence and authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError
from pydantic_ai import RunContext

from a13n_harness.content import ContentItem, ContentMetadata
from a13n_harness.execution_identity import format_execution_header

if TYPE_CHECKING:
    from a13n_harness.context import AgentContext


type ExecutionKind = Literal["agent", "tool"]
type ExecutionState = Literal["queued", "running", "succeeded", "failed", "cancelled", "lost"]
type EnvelopeKind = Literal["message", "agent_message", "result", "event"]
type SenderKind = Literal["user", "agent", "system", "connector"]
type ThreadCondition = Literal["active", "preparing", "idle", "waiting", "paused", "archived"]
type Disposition = Literal["steer", "start", "hold", "reject"]
INPUT_METADATA_KEY = "a13n.input"


class Sender(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: SenderKind
    thread_id: str | None = None
    name: str | None = None


class ExecutionRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    execution_id: str
    kind: ExecutionKind
    name: str
    state: ExecutionState


class InputOrigin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str | None = None
    kind: EnvelopeKind
    sender: Sender
    execution: ExecutionRef | None = None


class Envelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    kind: EnvelopeKind
    sender: Sender
    execution: ExecutionRef | None = None
    text: str
    delivery: Literal["steer", "next_run"] = "steer"


@runtime_checkable
class MessageRouter(Protocol):
    """Host authorization and delivery for named targets and Harness Thread IDs."""

    async def send(self, ctx: RunContext[AgentContext], to: str, message: str) -> dict[str, JsonValue]: ...


def disposition(condition: ThreadCondition, delivery: Literal["steer", "next_run"]) -> Disposition:
    if condition == "archived":
        return "reject"
    if condition == "idle":
        return "start"
    if condition == "active" and delivery == "steer":
        return "steer"
    return "hold"


def render_envelope(envelope: Envelope) -> tuple[ContentItem, ...]:
    """Render one text part, keeping its identity and origin in canonical annotations."""
    execution = envelope.execution
    lines = []
    if execution is not None and envelope.kind in {"result", "agent_message"}:
        label = (
            "Subagent message"
            if envelope.kind == "agent_message"
            else "Subagent result"
            if execution.kind == "agent"
            else "Tool notification"
        )
        lines.append(format_execution_header(label, execution.name, execution.execution_id, execution.state))
    elif envelope.kind == "agent_message" and envelope.sender.thread_id is not None:
        lines.append(
            f'Message · from thread "{envelope.sender.name or envelope.sender.thread_id}" ({envelope.sender.thread_id})'
        )
    if envelope.text and not (envelope.kind == "result" and execution is not None and execution.state == "cancelled"):
        lines.append(envelope.text)
    if envelope.kind == "result" and execution is not None:
        lines.append(
            f'Reply with send_message(to="{execution.execution_id}", message=...).'
            if execution.kind == "agent"
            else f'Retrieve the retained result with execution_result(execution_id="{execution.execution_id}").'
        )
    marker = envelope.model_dump(mode="json", exclude_none=True, exclude={"text", "delivery"})
    return (ContentItem("\n\n".join(lines), ContentMetadata.model_validate({INPUT_METADATA_KEY: marker})),)


def input_source(metadata: ContentMetadata | Mapping[str, object] | None) -> InputOrigin | None:
    """Read current or stored legacy annotations, never treating a malformed marker as user input."""
    values = metadata.model_dump() if isinstance(metadata, ContentMetadata) else metadata or {}
    if INPUT_METADATA_KEY in values:
        try:
            # Stored metadata can come from a newer release, including nested fields.
            return InputOrigin.model_validate(values[INPUT_METADATA_KEY], extra="ignore")
        except ValidationError:
            return InputOrigin(kind="event", sender=Sender(kind="system", name="unknown"))
    source = values.get("a13n.steering-source")
    if source in ("async_subagent", "tool_execution"):
        return InputOrigin(
            kind="result", sender=Sender(kind="agent" if source == "async_subagent" else "system", name=source)
        )
    if source == "parent_agent":
        return InputOrigin(kind="agent_message", sender=Sender(kind="agent", name="parent"))
    if source in ("background_process", "envd_session"):
        return InputOrigin(kind="event", sender=Sender(kind="system", name=source))
    ui = values.get("harness_ui")
    message = ui.get("thread_message") if isinstance(ui, dict) else None
    if isinstance(message, dict):
        thread_id, name = message.get("source_thread_id"), message.get("source_thread_title")
        return InputOrigin(
            kind="agent_message",
            sender=Sender(
                kind="agent",
                thread_id=thread_id if isinstance(thread_id, str) else None,
                name=name if isinstance(name, str) else None,
            ),
        )
    if values.get("source_id") == "a13n-harness-ui.thread-lifecycle":
        return InputOrigin(kind="event", sender=Sender(kind="system", name="thread-lifecycle"))
    return None


__all__ = [
    "INPUT_METADATA_KEY",
    "Disposition",
    "Envelope",
    "EnvelopeKind",
    "ExecutionKind",
    "ExecutionRef",
    "ExecutionState",
    "InputOrigin",
    "MessageRouter",
    "Sender",
    "SenderKind",
    "ThreadCondition",
    "disposition",
    "input_source",
    "render_envelope",
]
