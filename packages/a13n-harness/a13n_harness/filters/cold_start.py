"""Cold-start reduction of already-consumed ordinary tool-return history."""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolReturnPart
from pydantic_ai.models import ModelRequestContext

from a13n_harness.context import AgentContext

COLD_START_FILTER_CAPABILITY_ID = "a13n.filter.cold-start"


class ColdStartFilterConfiguration(BaseModel):
    """Finite history-reduction policy after a long model-idle interval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    idle_seconds: float = Field(default=3_600, gt=0, le=365 * 24 * 60 * 60)
    max_string_chars: int = Field(default=2_000, ge=128, le=256 * 1024)
    keep_head_chars: int = Field(default=800, ge=0, le=128 * 1024)
    keep_tail_chars: int = Field(default=800, ge=0, le=128 * 1024)

    @model_validator(mode="after")
    def _validate_retained_text(self) -> ColdStartFilterConfiguration:
        if self.keep_head_chars + self.keep_tail_chars >= self.max_string_chars:
            raise ValueError("cold-start retained head and tail must be smaller than max_string_chars")
        return self


@dataclass(init=False)
class ColdStartFilterCapability(AbstractCapability[AgentContext]):
    """Trim old textual tool-return leaves only after provider cache expiry is likely."""

    id = COLD_START_FILTER_CAPABILITY_ID

    def __init__(self, configuration: ColdStartFilterConfiguration | None = None) -> None:
        if configuration is None:
            configuration = ColdStartFilterConfiguration()
        elif not isinstance(configuration, ColdStartFilterConfiguration):
            configuration = ColdStartFilterConfiguration.model_validate(configuration, strict=True)
        self.configuration = configuration.model_copy(deep=True)

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        del ctx
        last_response = _last_response(request_context.messages)
        if last_response is None or _idle_seconds(last_response[1].timestamp) < self.configuration.idle_seconds:
            return request_context

        messages = deepcopy(request_context.messages)
        changed = _trim_consumed_tool_returns(messages, last_response[0], self.configuration)
        if not changed:
            return request_context
        updated = copy(request_context)
        updated.messages = messages
        return updated


def _last_response(messages: list[ModelMessage]) -> tuple[int, ModelResponse] | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, ModelResponse):
            return index, message
    return None


def _idle_seconds(timestamp: datetime) -> float:
    now = datetime.now(timestamp.tzinfo or UTC)
    normalized = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
    return max(0.0, (now - normalized).total_seconds())


def _trim_consumed_tool_returns(
    messages: list[ModelMessage],
    last_response_index: int,
    configuration: ColdStartFilterConfiguration,
) -> bool:
    changed = False
    for message_index in range(last_response_index):
        message = messages[message_index]
        if not isinstance(message, ModelRequest):
            continue
        parts = list(message.parts)
        part_changed = False
        for part_index, part in enumerate(parts):
            if type(part) is not ToolReturnPart:
                continue
            content, content_changed = _trim_value(part.content, configuration)
            if not content_changed:
                continue
            parts[part_index] = replace(part, content=content)
            part_changed = True
        if part_changed:
            messages[message_index] = replace(message, parts=tuple(parts))
            changed = True
    return changed


def _trim_value(value: Any, configuration: ColdStartFilterConfiguration) -> tuple[Any, bool]:
    if isinstance(value, str):
        if len(value) <= configuration.max_string_chars:
            return value, False
        omitted = len(value) - configuration.keep_head_chars - configuration.keep_tail_chars
        tail = value[-configuration.keep_tail_chars :] if configuration.keep_tail_chars else ""
        return (
            f"{value[: configuration.keep_head_chars]}\n[... {omitted} chars removed after cold start ...]\n{tail}",
            True,
        )
    if isinstance(value, list):
        items = [_trim_value(item, configuration) for item in value]
        return [item for item, _ in items], any(changed for _, changed in items)
    if isinstance(value, tuple):
        items = [_trim_value(item, configuration) for item in value]
        return tuple(item for item, _ in items), any(changed for _, changed in items)
    if isinstance(value, dict):
        items = {key: _trim_value(item, configuration) for key, item in value.items()}
        return {key: item for key, (item, _) in items.items()}, any(changed for _, changed in items.values())
    return value, False


__all__ = [
    "COLD_START_FILTER_CAPABILITY_ID",
    "ColdStartFilterCapability",
    "ColdStartFilterConfiguration",
]
