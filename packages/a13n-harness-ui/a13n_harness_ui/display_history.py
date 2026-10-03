"""UI-owned compact display snapshots, independent of native model context."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal, Self

from a13n_stream_protocol.display import DisplayFold, Item, StreamPosition, Tail
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)


class DisplayHistory(BaseModel):
    """One complete inspection snapshot selected atomically with HarnessState.

    Item ordinals are stable transcript addresses. Completion is a Host fact,
    recorded only by successful terminal selection, not inferred from text end.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal["1"] = "1"
    run_id: str | None = None
    items: tuple[Item, ...] = ()
    position: StreamPosition = Field(default_factory=lambda: StreamPosition(attempt=0, sequence=0))
    completed: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _addresses(self) -> Self:
        if any(item.ordinal != index for index, item in enumerate(self.items, 1)):
            raise ValueError("Display item ordinals must be dense and ordered")
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Display item identities must be unique")
        if not set(self.completed) <= {item.id for item in self.items if assistant_text(item)}:
            raise ValueError("Completion must address saved root assistant text")
        return self

    def start(self, run_id: str, *, resume: bool) -> DisplayFold:
        return DisplayFold(
            self.run_id if resume and self.run_id is not None else run_id,
            Tail(items=[item.model_copy(deep=True) for item in self.items], position=self.position),
            attempt=self.position.attempt + 1,
            page_items=128,
            page_bytes=262144,
            retain_complete=True,
        )

    def capture(self, fold: DisplayFold, *, completed: bool = False) -> DisplayHistory:
        items = tuple(item.model_copy(deep=True) for item in fold.items.values())
        # A continuation of a turn invalidates its former completion, even when
        # it has not emitted another text item. Earlier turns keep their marks.
        last_input = next((item.ordinal for item in reversed(items) if ordinary_input(item)), 0)
        previous_input = next((item.ordinal for item in reversed(self.items) if ordinary_input(item)), 0)
        continuing = fold.run_id == self.run_id or last_input > previous_input
        marks = {
            item.id for item in items if item.id in self.completed and (not continuing or item.ordinal < last_input)
        }
        if completed:
            final = next((item for item in reversed(items) if assistant_text(item) and item.ordinal > last_input), None)
            if (
                final is not None
                and final.last_stream_id.startswith(f"{fold.attempt}-")
                and (
                    fold.response_groups.get("root") is None
                    or final.content.get("responseGroup") == fold.response_groups["root"]
                )
            ):
                marks.update(item.id for item in response_parts(items, final))
        return DisplayHistory(run_id=fold.run_id, items=items, position=fold.position, completed=tuple(sorted(marks)))


def response_parts(items: Sequence[Item], final: Item) -> tuple[Item, ...]:
    """All root text parts in one response, preserving their stable addresses."""
    group = final.content.get("responseGroup")
    return tuple(
        item
        for item in items
        if assistant_text(item)
        and (item.id == final.id or (group is not None and item.content.get("responseGroup") == group))
    )


def ordinary_input(item: Item) -> bool:
    return (
        item.kind == "text_message"
        and item.content.get("role") == "user"
        and item.content.get("input_source", "user") == "user"
        and not item.content.get("subagentRunId")
        and not (isinstance(metadata := item.content.get("metadata"), dict) and metadata.get("display") is False)
    )


def assistant_text(item: Item) -> bool:
    return (
        item.kind == "text_message"
        and item.content.get("role", "assistant") == "assistant"
        and not item.content.get("subagentRunId")
        and isinstance(item.content.get("text"), str)
        and not (isinstance(metadata := item.content.get("metadata"), dict) and metadata.get("display") is False)
    )


def import_display_history(thread_id: str, messages: Sequence[ModelMessage]) -> DisplayHistory:
    """Project an explicit native initial-state import once, never a continuation fallback."""
    if not messages:
        return DisplayHistory()

    from a13n_harness.content import request_input_content
    from a13n_harness.tools._output import tool_execution_value
    from a13n_stream_protocol import tool_result_content
    from a13n_stream_protocol.content import public_tool_value
    from a13n_stream_protocol.messages import project_input_content
    from pydantic import TypeAdapter
    from pydantic_ai.messages import ModelResponsePart

    from a13n_harness_ui.mcp_apps.snapshots import app_references
    from a13n_harness_ui.tool_evidence import applied_edit
    from a13n_harness_ui.tool_images import tool_image_unavailable, tool_images

    response_part = TypeAdapter(ModelResponsePart)

    fold = DisplayHistory().start(thread_id, resume=False)
    for position, message in enumerate(messages):
        timestamp = int((message.timestamp or datetime.now(UTC)).timestamp() * 1000)
        message_metadata = message.metadata or {}
        context = message_metadata.get("a13n.context") or (
            "compaction" if message_metadata.get("keep") == "compact" else None
        )
        if isinstance(message, ModelRequest):
            for index, value in enumerate(request_input_content(message)):
                projected = project_input_content(value)
                if projected is None:
                    continue
                content, metadata = projected
                if context:
                    if index == 0:
                        fold.fold(
                            [
                                {
                                    "type": "CUSTOM",
                                    "name": f"a13n.context.{context}_summary",
                                    "timestamp": timestamp,
                                    "value": {
                                        "event": {
                                            "summary": content,
                                            "operation_id": message_metadata.get("operation_id"),
                                        }
                                    },
                                }
                            ]
                        )
                    continue
                source = str(
                    message_metadata.get("a13n.steering-source")
                    or (metadata.model_extra or {}).get("a13n.steering-source")
                    or ("steering" if "a13n.steering-run" in message_metadata else "user")
                )
                if "a13n.steering-run" in message_metadata and source not in {"async_subagent", "background_process"}:
                    source = "steering"
                fold.fold(
                    [
                        {
                            "type": "CUSTOM",
                            "name": "a13n.input.media" if metadata.media else f"a13n.input.{source}",
                            "timestamp": timestamp,
                            "metadata": metadata.model_dump(mode="json"),
                            "value": {
                                "event": {
                                    "message_id": f"import:{position}:{index}",
                                    "input_id": message_metadata.get("a13n.steering-input")
                                    or metadata.source_id
                                    or f"import:{position}",
                                    "source": source,
                                    "content": content,
                                }
                            },
                        }
                    ]
                )
        for index, part in enumerate(message.parts):
            identity = f"import:{position}:{index}"
            events = []
            if isinstance(message, ModelResponse) and isinstance(part, (TextPart, ThinkingPart)):
                if context:
                    fold.fold(
                        [
                            {
                                "type": "CUSTOM",
                                "name": f"a13n.context.{context}_summary",
                                "timestamp": timestamp,
                                "value": {
                                    "event": {
                                        "summary": part.content,
                                        "operation_id": message_metadata.get("operation_id"),
                                    }
                                },
                            }
                        ]
                    )
                    continue
                kind = "TEXT_MESSAGE" if isinstance(part, TextPart) else "REASONING_MESSAGE"
                events = [
                    {
                        "type": f"{kind}_START",
                        "messageId": identity,
                        "role": "assistant",
                        "responseGroup": f"import:{position}",
                        "metadata": {
                            "closing": message.state == "complete"
                            and not any(
                                isinstance(value, (ToolCallPart, NativeToolCallPart)) for value in message.parts
                            ),
                        },
                    },
                    {"type": f"{kind}_CONTENT", "messageId": identity, "delta": part.content},
                    {"type": f"{kind}_END", "messageId": identity},
                ]
            elif isinstance(part, ToolCallPart):
                events = [
                    {"type": "TOOL_CALL_START", "toolCallId": part.tool_call_id, "toolCallName": part.tool_name},
                    {"type": "TOOL_CALL_ARGS", "toolCallId": part.tool_call_id, "delta": part.args_as_json_str()},
                    {"type": "TOOL_CALL_END", "toolCallId": part.tool_call_id},
                ]
            elif isinstance(part, (NativeToolCallPart, NativeToolReturnPart)):
                events = [
                    {
                        "type": "CUSTOM",
                        "name": "a13n.pydantic_ai.part_end",
                        "value": {"event": {"part": response_part.dump_python(part, mode="json")}},
                    }
                ]
            elif isinstance(part, (SystemPromptPart, RetryPromptPart)):
                events = [
                    {
                        "type": "CUSTOM",
                        "name": "a13n.input.system"
                        if isinstance(part, SystemPromptPart)
                        else "a13n.pydantic_ai.function_tool_result",
                        "value": {
                            "event": {
                                "part": {
                                    "part_kind": part.part_kind,
                                    "content": part.content,
                                    **(
                                        {"tool_call_id": part.tool_call_id, "tool_name": part.tool_name}
                                        if isinstance(part, RetryPromptPart)
                                        else {}
                                    ),
                                }
                            }
                        },
                    }
                ]
            elif isinstance(part, ToolReturnPart):
                value = tool_execution_value(part.content, part.metadata)
                content = tool_result_content(value)
                events = [
                    {
                        "type": "TOOL_CALL_RESULT",
                        "toolCallId": part.tool_call_id,
                        "content": content
                        if isinstance(content, str)
                        else [value.model_dump(mode="json", by_alias=True) for value in content],
                    }
                ]
                events.append(
                    {
                        "type": "CUSTOM",
                        "name": "a13n.pydantic_ai.function_tool_result",
                        "value": {
                            "event": {
                                "part": {
                                    "part_kind": part.part_kind,
                                    "tool_call_id": part.tool_call_id,
                                    "tool_name": part.tool_name,
                                    "content": public_tool_value(value),
                                    "outcome": part.outcome,
                                }
                            }
                        },
                    }
                )
                if edit := applied_edit(part):
                    events.append(
                        {
                            "type": "CUSTOM",
                            "name": "a13n.filesystem.edit_applied",
                            "value": {"event": {"tool_call_id": part.tool_call_id, **edit.model_dump(mode="json")}},
                        }
                    )
                images = tool_images(part)
                if images or tool_image_unavailable(part):
                    events.append(
                        {
                            "type": "CUSTOM",
                            "name": "a13n.harness-ui.tool_images",
                            "value": {
                                "event": {
                                    "tool_call_id": part.tool_call_id,
                                    "images": [image.model_dump(mode="json") for image in images],
                                    "unavailable": tool_image_unavailable(part),
                                }
                            },
                        }
                    )
                if apps := app_references(part):
                    events.append(
                        {
                            "type": "CUSTOM",
                            "name": "a13n.harness-ui.mcp_apps",
                            "value": {
                                "event": {
                                    "tool_call_id": part.tool_call_id,
                                    "apps": [app.model_dump(mode="json") for app in apps],
                                }
                            },
                        }
                    )
            fold.fold([{**event, "timestamp": timestamp} for event in events])
    return DisplayHistory().capture(fold)
