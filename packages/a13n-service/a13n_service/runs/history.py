"""Service admission rules for native Pydantic AI messages; no parallel message schema."""

import json
from dataclasses import replace
from typing import Annotated

from pydantic import AfterValidator, Field, JsonValue, TypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextContent,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

_JSON = TypeAdapter(JsonValue)


def _validate(value: list[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 262144:
        raise ValueError("Model history exceeds its byte limit")
    messages = ModelMessagesTypeAdapter.validate_python(value)
    pending: dict[str, str] = {}
    seen: set[str] = set()
    for message in messages:
        if message.state != "complete" or (isinstance(message, ModelRequest) and message.instructions):
            raise ValueError("History imports completed conversation content, not instructions or suspended execution")
        if isinstance(message, ModelResponse) and pending:
            raise ValueError("Tool calls must be answered before the next response")
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                if not part.tool_call_id.strip() or not part.tool_name.strip() or part.tool_call_id in seen:
                    raise ValueError("History tool calls require unique nonblank IDs and names")
                part.args_as_dict()
                seen.add(part.tool_call_id)
                pending[part.tool_call_id] = part.tool_name
            elif isinstance(part, ToolReturnPart):
                _JSON.validate_python(part.content)
                if pending.pop(part.tool_call_id, None) != part.tool_name:
                    raise ValueError("Tool returns must match an unanswered call by ID and name")
            elif isinstance(part, UserPromptPart):
                if pending:
                    raise ValueError("Tool calls must be answered before new user content")
                if not isinstance(part.content, str) and any(
                    not isinstance(item, str | TextContent) for item in part.content
                ):
                    raise ValueError("Import text history; submit media through the current payload")
            elif not isinstance(part, TextPart):
                raise ValueError("History accepts user text, model text, tool calls and JSON tool returns")
    if pending:
        raise ValueError("Model history cannot contain unanswered tool calls")
    # Retain submitted JSON for immutable readback and idempotency, not time-dependent native defaults.
    return value


# The wire format belongs to Pydantic AI, not a second family of generated Service models.
# Other-language clients carry JSON; Python callers can use ModelMessagesTypeAdapter directly.
type MessageHistory = Annotated[
    list[dict[str, JsonValue]],
    Field(
        max_length=256,
        description=(
            "Pydantic AI ModelMessage JSON objects, validated by the Service. Imports completed user text, "
            "model text and closed tool-call/JSON-result exchanges; no instructions, media or suspended execution. "
            "At most 256 messages and 256 KiB of normalized JSON."
        ),
    ),
    AfterValidator(_validate),
]
HISTORY = TypeAdapter(MessageHistory)


def initial(messages: MessageHistory) -> list[ModelMessage]:
    """Detach the seed and clear foreign application provenance before Harness can interpret it."""
    result = ModelMessagesTypeAdapter.validate_python(messages)
    for message in result:
        message.run_id = message.conversation_id = None
        message.metadata = None
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                part.metadata = None
            elif isinstance(part, UserPromptPart) and not isinstance(part.content, str):
                part.content = [
                    replace(item, metadata=None) if isinstance(item, TextContent) else item for item in part.content
                ]
    return result
