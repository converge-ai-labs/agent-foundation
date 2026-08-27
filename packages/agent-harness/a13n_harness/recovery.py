"""Bounded model-attempt recovery and interrupted-history repair."""

from __future__ import annotations

import asyncio
import inspect
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace

from pydantic_ai.exceptions import (
    ModelAPIError,
    RunCancelled,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    AgentStreamEvent,
    BaseToolCallPart,
    BaseToolReturnPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponsePart,
    NativeToolCallPart,
    NativeToolReturnPart,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)

from a13n_harness.errors import HarnessError
from a13n_harness.input import RunInputValue

RecoveryPromptFactory = Callable[
    [BaseException, int, Sequence[ModelMessage]],
    RunInputValue | Awaitable[RunInputValue],
]

DEFAULT_RECOVERY_PROMPT = (
    "The previous model stream ended before the task finished. Continue from the available "
    "history and avoid repeating completed work. A tool operation may have partially or fully "
    "completed even when no result was recorded, so check the current state before retrying "
    "side-effecting work such as shell commands."
)

INTERRUPTED_TOOL_RESULT = (
    "No tool result was recorded because execution was interrupted. The operation may have "
    "partially or fully completed. Check the current state before deciding whether to retry it."
)


@dataclass(frozen=True, slots=True)
class ModelRecoveryPolicy:
    """Optional total attempt budget for interrupted model execution."""

    enabled: bool = False
    max_attempts: int = 5
    continuation_prompt: RunInputValue = DEFAULT_RECOVERY_PROMPT
    prompt_factory: RecoveryPromptFactory | None = None
    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.backoff_initial_seconds < 0 or self.backoff_max_seconds < 0:
            raise ValueError("recovery backoff values must not be negative")

    async def build_prompt(
        self,
        error: BaseException,
        attempt_index: int,
        messages: Sequence[ModelMessage],
    ) -> RunInputValue:
        if self.prompt_factory is None:
            return self.continuation_prompt
        value = self.prompt_factory(error, attempt_index, messages)
        if inspect.isawaitable(value):
            value = await value
        return value

    def delay(self, attempt_index: int) -> float:
        if self.backoff_initial_seconds == 0 or self.backoff_max_seconds == 0:
            return 0
        ceiling = min(
            self.backoff_initial_seconds * (2 ** max(0, attempt_index - 1)),
            self.backoff_max_seconds,
        )
        return random.uniform(0, ceiling)


@dataclass(slots=True)
class InterruptedResponseTracker:
    """Track response parts that are safe to retain after a stream interruption."""

    _parts: dict[int, ModelResponsePart] = field(default_factory=dict)
    _finalized_indices: set[int] = field(default_factory=set)
    _response_history_count: int | None = None
    _observed: bool = False

    def observe(self, event: AgentStreamEvent, *, response_history_count: int) -> None:
        """Observe a public response-part event at its model-response boundary."""
        if not isinstance(event, PartStartEvent | PartDeltaEvent | PartEndEvent):
            return
        self._select_response(response_history_count)
        self._observed = True
        if isinstance(event, PartStartEvent):
            self._observe_part(
                event.index,
                event.part,
                finalized=isinstance(event.part, BaseToolReturnPart),
            )
        elif isinstance(event, PartDeltaEvent):
            self._observe_delta(event.index, event)
        else:
            self._observe_part(event.index, event.part, finalized=True)

    def sanitize(self, messages: Sequence[ModelMessage]) -> tuple[ModelMessage, ...]:
        """Replace Pydantic's interrupted tail with only safely replayable parts."""
        sanitized = list(messages)
        if not sanitized:
            return ()
        tail = sanitized[-1]
        if not isinstance(tail, ModelResponse) or tail.state != "interrupted":
            return tuple(sanitized)
        if not self._observed or self._response_history_count != len(sanitized) - 1 or not tail.parts:
            sanitized.pop()
            return tuple(sanitized)

        parts = self._safe_parts()
        if parts is None or not parts:
            sanitized.pop()
        else:
            sanitized[-1] = replace(tail, parts=parts)
        return tuple(sanitized)

    def _select_response(self, response_history_count: int) -> None:
        if response_history_count == self._response_history_count:
            return
        self._parts.clear()
        self._finalized_indices.clear()
        self._response_history_count = response_history_count
        self._observed = False

    def _observe_part(self, index: int, part: ModelResponsePart, *, finalized: bool) -> None:
        if isinstance(part, TextPart):
            self._parts[index] = replace(part)
        elif isinstance(part, ThinkingPart | BaseToolCallPart | BaseToolReturnPart):
            self._parts[index] = part
        else:
            return
        if finalized:
            self._finalized_indices.add(index)
        else:
            self._finalized_indices.discard(index)

    def _observe_delta(self, index: int, event: PartDeltaEvent) -> None:
        delta = event.delta
        if not isinstance(delta, TextPartDelta):
            return
        existing = self._parts.get(index)
        if isinstance(existing, TextPart):
            self._parts[index] = delta.apply(existing)
        elif existing is None:
            self._parts[index] = TextPart(
                content=delta.content_delta,
                provider_name=delta.provider_name,
                provider_details=delta.provider_details,
            )

    def _safe_parts(self) -> list[ModelResponsePart] | None:
        safe: list[ModelResponsePart] = []
        for index in sorted(self._parts):
            part = self._parts[index]
            if isinstance(part, TextPart):
                if part.content:
                    safe.append(part)
            elif isinstance(part, ThinkingPart):
                if index in self._finalized_indices and (part.content or part.signature):
                    safe.append(part)
            elif isinstance(part, BaseToolCallPart | BaseToolReturnPart):
                if index not in self._finalized_indices:
                    return None
                safe.append(part)
        return safe if _native_parts_are_balanced(safe) else None


def normalize_interrupted_history(
    messages: Sequence[ModelMessage],
    *,
    response_tracker: InterruptedResponseTracker | None = None,
) -> tuple[tuple[ModelMessage, ...], int]:
    """Retain safe streamed parts and close finalized tool calls at an interrupted boundary."""
    normalized = list(response_tracker.sanitize(messages) if response_tracker is not None else messages)
    if not normalized:
        return (), 0

    tail = normalized[-1]
    if isinstance(tail, ModelResponse) and tail.state == "interrupted":
        if not _native_parts_are_balanced(tail.parts):
            normalized.pop()
            return tuple(normalized), 0
        missing = _missing_tool_calls(tail, ())
        if missing:
            normalized.append(ModelRequest(parts=[_failed_tool_result(call) for call in missing]))
        return tuple(normalized), len(missing)

    if isinstance(tail, ModelRequest) and tail.state == "interrupted":
        response = next(
            (message for message in reversed(normalized[:-1]) if isinstance(message, ModelResponse)),
            None,
        )
        if response is None:
            return tuple(normalized), 0
        missing = _missing_tool_calls(response, tail.parts)
        if missing:
            normalized[-1] = replace(
                tail,
                parts=[*tail.parts, *(_failed_tool_result(call) for call in missing)],
            )
        return tuple(normalized), len(missing)

    return tuple(normalized), 0


def is_recoverable_model_failure(error: BaseException, messages: Sequence[ModelMessage]) -> bool:
    """Classify only failures at a model-request boundary as attempt-recoverable."""
    if isinstance(error, HarnessError | RunCancelled | UsageLimitExceeded | asyncio.CancelledError):
        return False
    if isinstance(error, ModelAPIError):
        return True
    if isinstance(error, UnexpectedModelBehavior):
        text = str(error).lower()
        return "exceeded maximum" not in text or "retries" not in text
    if not messages:
        return False
    tail = messages[-1]
    if isinstance(tail, ModelResponse):
        return tail.state == "interrupted"
    return isinstance(tail, ModelRequest) and tail.state != "interrupted"


def _native_parts_are_balanced(parts: Sequence[object]) -> bool:
    pending: dict[str, str] = {}
    completed: set[str] = set()
    for part in parts:
        if isinstance(part, NativeToolCallPart):
            if part.tool_call_id in pending or part.tool_call_id in completed:
                return False
            pending[part.tool_call_id] = part.tool_name
        elif isinstance(part, NativeToolReturnPart):
            if pending.get(part.tool_call_id) != part.tool_name:
                return False
            del pending[part.tool_call_id]
            completed.add(part.tool_call_id)
    return not pending


def _missing_tool_calls(
    response: ModelResponse,
    result_parts: Sequence[object],
) -> list[ToolCallPart]:
    answered = {
        part.tool_call_id
        for part in result_parts
        if isinstance(part, ToolReturnPart | RetryPromptPart) and part.tool_call_id is not None
    }
    return [part for part in response.parts if isinstance(part, ToolCallPart) and part.tool_call_id not in answered]


def _failed_tool_result(call: ToolCallPart) -> ToolReturnPart:
    return ToolReturnPart(
        tool_name=call.tool_name,
        tool_call_id=call.tool_call_id,
        content=INTERRUPTED_TOOL_RESULT,
        outcome="failed",
    )
