"""Provider-valid ordinary function tool-call/result history filtering."""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestContext

from a13n_harness.content import replace_request_parts, request_parts
from a13n_harness.context import AgentContext

MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID = "a13n.filter.message-integrity"


@dataclass
class MessageIntegrityFilterCapability(AbstractCapability[AgentContext]):
    """Drop orphan or duplicate ordinary tool results at the final request boundary."""

    id: str | None = MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID

    def __post_init__(self) -> None:
        if self.id != MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID:
            raise ValueError(f"MessageIntegrityFilterCapability.id must be {MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID!r}")

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost")

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        del ctx
        filtered, changed = _filter_tool_result_pairs(request_context.messages)
        if not changed:
            return request_context
        updated = copy(request_context)
        updated.messages = deepcopy(filtered)
        return updated


def _tool_result_id(part: ModelRequestPart) -> str | None:
    if type(part) is ToolReturnPart:
        return part.tool_call_id
    if isinstance(part, RetryPromptPart) and part.tool_name is not None:
        return part.tool_call_id
    return None


def _filter_tool_result_pairs(messages: list[ModelMessage]) -> tuple[list[ModelMessage], bool]:
    current_call_ids: set[str] = set()
    consumed_call_ids: set[str] = set()
    filtered: list[ModelMessage] = []
    changed = False

    for index, message in enumerate(messages):
        if isinstance(message, ModelResponse):
            current_call_ids = {
                part.tool_call_id
                for part in message.parts
                if isinstance(part, ToolCallPart) and part.tool_call_id is not None
            }
            consumed_call_ids.clear()
            filtered.append(message)
            continue

        if not isinstance(message, ModelRequest):
            filtered.append(message)
            continue

        if any(isinstance(part, RetryPromptPart) and part.tool_name is None for part in message.parts):
            current_call_ids.clear()
            consumed_call_ids.clear()

        kept = []
        for part, content_annotations in request_parts(message):
            tool_call_id = _tool_result_id(part)
            if tool_call_id is None:
                kept.append((part, content_annotations))
                continue
            if tool_call_id not in current_call_ids or tool_call_id in consumed_call_ids:
                changed = True
                continue
            consumed_call_ids.add(tool_call_id)
            kept.append((part, content_annotations))

        if kept or not message.parts or index == len(messages) - 1:
            filtered.append(message if len(kept) == len(message.parts) else replace_request_parts(message, kept))
        else:
            changed = True

    return filtered, changed


__all__ = [
    "MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID",
    "MessageIntegrityFilterCapability",
]
