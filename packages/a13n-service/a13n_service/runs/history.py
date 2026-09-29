"""Portable model context, not execution state.

The wire fields are a subset of public ModelMessages. Only content is imported: no provider state,
execution IDs, instructions, usage or Harness metadata. The native adapter owns message construction.
"""

import json
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

MAX_HISTORY_BYTES = 262144


class _Content(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


type CallId = Annotated[str, Field(min_length=1, max_length=1024, pattern=r"\S")]
type ToolName = Annotated[str, Field(min_length=1, max_length=128, pattern=r"\S")]


class HistoryUserPrompt(_Content):
    part_kind: Literal["user-prompt"]
    content: str = Field(min_length=1, max_length=65536)


class HistoryText(_Content):
    part_kind: Literal["text"]
    content: str = Field(min_length=1, max_length=65536)


class HistoryToolCall(_Content):
    part_kind: Literal["tool-call"]
    tool_name: ToolName
    tool_call_id: CallId
    args: dict[str, JsonValue]


class HistoryToolReturn(_Content):
    part_kind: Literal["tool-return"]
    tool_name: ToolName
    tool_call_id: CallId
    content: JsonValue
    outcome: Literal["success", "failed", "denied", "interrupted"] = "success"


class HistoryRequest(_Content):
    kind: Literal["request"]
    parts: tuple[Annotated[HistoryUserPrompt | HistoryToolReturn, Field(discriminator="part_kind")], ...] = Field(
        min_length=1, max_length=128
    )


class HistoryResponse(_Content):
    kind: Literal["response"]
    parts: tuple[Annotated[HistoryText | HistoryToolCall, Field(discriminator="part_kind")], ...] = Field(
        min_length=1, max_length=128
    )


type HistoryMessage = Annotated[HistoryRequest | HistoryResponse, Field(discriminator="kind")]
_MESSAGES = TypeAdapter(tuple[HistoryMessage, ...])


def _validate(messages: tuple[HistoryMessage, ...]) -> tuple[HistoryMessage, ...]:
    data = _MESSAGES.dump_python(messages, mode="json")
    if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode()) > MAX_HISTORY_BYTES:
        raise ValueError("Model history exceeds its byte limit")
    pending: dict[str, str] = {}
    seen: set[str] = set()
    for message in messages:
        if isinstance(message, HistoryResponse) and pending:
            raise ValueError("Tool calls must be answered before the next response")
        for part in message.parts:
            if isinstance(part, HistoryToolCall):
                if part.tool_call_id in seen:
                    raise ValueError("History tool call IDs must be unique")
                seen.add(part.tool_call_id)
                pending[part.tool_call_id] = part.tool_name
            elif isinstance(part, HistoryToolReturn):
                if pending.pop(part.tool_call_id, None) != part.tool_name:
                    raise ValueError("Tool returns must match an unanswered call by ID and name")
            elif isinstance(part, HistoryUserPrompt) and pending:
                raise ValueError("Tool calls must be answered before new user content")
    if pending:
        raise ValueError("Model history cannot contain unanswered tool calls")
    return messages


type MessageHistory = Annotated[tuple[HistoryMessage, ...], Field(max_length=256), AfterValidator(_validate)]
HISTORY = TypeAdapter(MessageHistory)


def native(messages: MessageHistory) -> list[ModelMessage]:
    """Construct detached public messages using their owning adapter, never a prompt transcript."""
    return ModelMessagesTypeAdapter.validate_python(HISTORY.dump_python(messages, mode="json"))
