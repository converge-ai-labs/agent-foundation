"""Harness-to-AG-UI projection into the shared Run Stream."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

import rfc8785
from a13n_harness import HarnessEvent, HarnessRunResultEvent
from a13n_stream_protocol import HarnessAguiObserver
from ag_ui.core import Event
from pydantic import JsonValue, TypeAdapter
from pydantic_ai.messages import FunctionToolResultEvent, OutputToolResultEvent, ToolReturnPart

from .domain import (
    MAX_RUN_STREAM_PAYLOAD_BYTES,
    RunStreamEvent,
    deterministic_item_id,
    deterministic_run_stream_event_id,
)
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


@dataclass(frozen=True, slots=True)
class _ItemProjection:
    item_id: str
    fields: dict[str, JsonValue]
    terminal_state: Literal["completed", "failed"] | None = None


class HarnessAguiRunStreamWriter:
    """Convert one ordered Harness observation and append its presentation events."""

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
        self._item_first_stream_ids: dict[str, str] = {}

    async def write(self, event: HarnessEvent | HarnessRunResultEvent[Any]) -> None:
        if event.thread_id != self._thread_id or event.run_id != self._harness_run_id:
            raise ValueError("Harness observation does not match the selected RunAttempt")
        for index, observation in enumerate(self._observer.observe(event)):
            event_type = _event_type(observation)
            payload = _payload(observation)
            item = _item_projection(self._run_id, event, event_type, payload)
            if item is not None:
                payload.update(item.fields)
            payload = _bounded_payload(event_type, payload)
            stream_id = await self._stream.append(
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
                    item_id=None if item is None else item.item_id,
                    occurred_at=event.occurred_at,
                    payload=payload,
                ),
            )
            if item is None:
                continue
            self._item_first_stream_ids.setdefault(item.item_id, stream_id)
            terminal_state = item.terminal_state
            if terminal_state is not None:
                await self._project_item_terminal(
                    event,
                    index=index,
                    item=item,
                    terminal_state=terminal_state,
                    last_content_stream_id=stream_id,
                    source_event_type=event_type,
                )

    async def _project_item_terminal(
        self,
        event: HarnessEvent | HarnessRunResultEvent[Any],
        *,
        index: int,
        item: _ItemProjection,
        terminal_state: Literal["completed", "failed"],
        last_content_stream_id: str,
        source_event_type: str,
    ) -> None:
        first_stream_id = self._item_first_stream_ids[item.item_id]
        payload: dict[str, JsonValue] = {
            **item.fields,
            "item_state": terminal_state,
            "first_stream_id": first_stream_id,
            "last_content_stream_id": last_content_stream_id,
            "content": {"terminal_event_type": f"agui.{source_event_type}"},
        }
        if terminal_state == "failed":
            payload["failure"] = {
                "code": "tool_result_failed",
                "message": "The tool returned a failed presentation outcome.",
            }
        await self._stream.append(
            self._tenant_id,
            RunStreamEvent(
                event_id=deterministic_run_stream_event_id(
                    "item",
                    self._run_attempt_id,
                    self._harness_run_id,
                    str(event.sequence),
                    str(index),
                    item.item_id,
                    terminal_state,
                ),
                event_type=f"item.{terminal_state}",
                run_id=self._run_id,
                thread_id=self._thread_id,
                run_attempt_id=self._run_attempt_id,
                harness_run_id=self._harness_run_id,
                item_id=item.item_id,
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


def _bounded_payload(event_type: str, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if len(rfc8785.dumps(payload)) <= MAX_RUN_STREAM_PAYLOAD_BYTES:
        return payload
    if event_type != "run_finished":
        return payload
    bounded = {**payload, "result": None, "result_omitted": True}
    return bounded


def _item_projection(
    run_id: str,
    source: HarnessEvent | HarnessRunResultEvent[Any],
    event_type: str,
    payload: dict[str, JsonValue],
) -> _ItemProjection | None:
    if event_type in _MESSAGE_EVENTS:
        source_id = payload.get("messageId")
        kind = "reasoning_message" if event_type.startswith("reasoning_") else "text_message"
        terminal_state: Literal["completed", "failed"] | None = (
            "completed" if event_type in {"text_message_end", "reasoning_message_end"} else None
        )
    elif event_type in _TOOL_EVENTS:
        source_id = payload.get("toolCallId")
        kind = "tool_call"
        terminal_state = "completed" if event_type == "tool_call_result" else None
    elif isinstance(source, HarnessEvent) and (part := _failed_tool_result(source)) is not None:
        source_id = part.tool_call_id
        kind = "tool_call"
        terminal_state = "failed"
    else:
        return None
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("item-producing AG-UI observation omitted its source identity")
    fields: dict[str, JsonValue] = {"item_kind": kind}
    if terminal_state is not None:
        fields["item_state"] = terminal_state
    parent_message_id = payload.get("parentMessageId")
    if isinstance(parent_message_id, str) and parent_message_id:
        fields["parent_item_id"] = deterministic_item_id(run_id, "text_message", parent_message_id)
    return _ItemProjection(
        item_id=deterministic_item_id(run_id, kind, source_id),
        fields=fields,
        terminal_state=terminal_state,
    )


def _failed_tool_result(source: HarnessEvent) -> ToolReturnPart | None:
    event = source.event
    if (
        isinstance(event, FunctionToolResultEvent | OutputToolResultEvent)
        and isinstance(event.part, ToolReturnPart)
        and event.part.outcome == "failed"
    ):
        return event.part
    return None


__all__ = ["HarnessAguiRunStreamWriter"]
