"""Harness-to-AG-UI projection into the shared Run Stream."""

from __future__ import annotations

from enum import Enum

from a13n_harness import HarnessEvent
from a13n_stream_protocol import HarnessAguiObserver
from ag_ui.core import Event
from pydantic import JsonValue, TypeAdapter

from .domain import RunStreamEvent, deterministic_item_id, deterministic_run_stream_event_id
from .redis import RedisRunStream

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_MESSAGE_EVENTS = frozenset(
    {
        "text_message_start",
        "text_message_content",
        "text_message_end",
        "reasoning_message_start",
        "reasoning_message_content",
        "reasoning_message_end",
        "reasoning_encrypted_value",
    }
)
_TOOL_EVENTS = frozenset({"tool_call_start", "tool_call_args", "tool_call_end", "tool_call_result"})


class RunStreamHarnessProjector:
    """Convert each canonical Harness observation exactly once at the executor root."""

    def __init__(
        self,
        stream: RedisRunStream,
        *,
        tenant_id: str,
        run_id: str,
        thread_id: str,
        run_attempt_id: str,
        harness_run_id: str,
        observer: HarnessAguiObserver | None = None,
    ) -> None:
        self._stream = stream
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._thread_id = thread_id
        self._run_attempt_id = run_attempt_id
        self._harness_run_id = harness_run_id
        self._observer = HarnessAguiObserver() if observer is None else observer

    async def project(self, event: HarnessEvent) -> None:
        if event.thread_id != self._thread_id or event.run_id != self._harness_run_id:
            raise ValueError("Harness observation does not match the selected RunAttempt")
        for index, observation in enumerate(self._observer.observe(event)):
            event_type = _event_type(observation)
            payload = _payload(observation)
            item_id, item_fields = _item_fields(self._run_id, event_type, payload)
            payload.update(item_fields)
            await self._stream.append(
                self._tenant_id,
                RunStreamEvent(
                    event_id=deterministic_run_stream_event_id(
                        "harness",
                        self._run_attempt_id,
                        self._harness_run_id,
                        str(event.sequence),
                        str(index),
                    ),
                    event_type=f"agui.{event_type}",
                    run_id=self._run_id,
                    thread_id=self._thread_id,
                    run_attempt_id=self._run_attempt_id,
                    harness_run_id=self._harness_run_id,
                    item_id=item_id,
                    occurred_at=event.occurred_at,
                    payload=payload,
                ),
            )


def _event_type(event: Event) -> str:
    value = event.type.value if isinstance(event.type, Enum) else event.type
    if not isinstance(value, str) or not value:
        raise ValueError("AG-UI observation type is invalid")
    return value.lower()


def _payload(event: Event) -> dict[str, JsonValue]:
    return _JSON_OBJECT.validate_python(
        event.model_dump(mode="json", by_alias=True, exclude={"raw_event", "type"}),
        strict=True,
    )


def _item_fields(
    run_id: str,
    event_type: str,
    payload: dict[str, JsonValue],
) -> tuple[str | None, dict[str, JsonValue]]:
    if event_type in _MESSAGE_EVENTS:
        source_id = payload.get("messageId")
        kind = "reasoning_message" if event_type.startswith("reasoning_") else "text_message"
    elif event_type in _TOOL_EVENTS:
        source_id = payload.get("toolCallId")
        kind = "tool_call"
    else:
        return None, {}
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("item-producing AG-UI observation omitted its source identity")
    fields: dict[str, JsonValue] = {"item_kind": kind}
    if event_type.endswith("_end") or event_type == "tool_call_result":
        fields["item_state"] = "completed"
    parent_message_id = payload.get("parentMessageId")
    if isinstance(parent_message_id, str) and parent_message_id:
        fields["parent_item_id"] = deterministic_item_id(run_id, "text_message", parent_message_id)
    return deterministic_item_id(run_id, kind, source_id), fields


__all__ = ["RunStreamHarnessProjector"]
