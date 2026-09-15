"""Bounded model-attempt recovery and interrupted-history repair."""

from __future__ import annotations

import asyncio
import inspect
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Literal

from pydantic_ai.exceptions import (
    ModelAPIError,
    RunCancelled,
    ToolFailed,
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
from pydantic_ai.tools import DeferredToolResult, DeferredToolResults, ToolApproved

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


type ToolRecoveryMode = Literal["declared", "never", "always"]


@dataclass(slots=True)
class ToolRecoveryPlan:
    """One restored batch, its native continuation, and its remaining decisions."""

    mode: ToolRecoveryMode
    messages: tuple[ModelMessage, ...]
    pending: dict[str, ToolCallPart] = field(default_factory=dict)
    results: DeferredToolResults | None = None
    native_results: dict[str, DeferredToolResult | Literal["skip"]] | None = None

    def resolve(self, declarations: Mapping[str, bool]) -> None:
        """Resolve this native batch once from its freshly prepared tool surface."""
        if self.native_results is None:
            return
        for call_id, call in tuple(self.pending.items()):
            if call.tool_name not in declarations or (self.mode == "declared" and not declarations[call.tool_name]):
                self.native_results[call_id] = ToolFailed(INTERRUPTED_TOOL_RESULT)
                self.pending.pop(call_id)
        self.native_results = None


def prepare_tool_recovery(messages: Sequence[ModelMessage], mode: ToolRecoveryMode) -> ToolRecoveryPlan:
    """Retain recorded results and resume only unanswered calls through native dispatch."""
    normalized, _ = normalize_interrupted_history(
        messages, close_pending_tools=mode == "never", close_tool_calls=mode == "never"
    )
    plan = ToolRecoveryPlan(mode, normalized)
    if mode == "never" or not normalized:
        return plan
    response_index = next(
        (index for index in range(len(normalized) - 1, -1, -1) if isinstance(normalized[index], ModelResponse)),
        None,
    )
    if response_index is None:
        return plan
    response = normalized[response_index]
    assert isinstance(response, ModelResponse)
    if response.state == "suspended":
        return plan
    result_parts = [
        part
        for message in normalized[response_index + 1 :]
        if isinstance(message, ModelRequest)
        for part in message.parts
    ]
    plan.pending = {call.tool_call_id: call for call in _missing_tool_calls(response, result_parts)}
    if plan.pending:
        # Native approval values select exact calls for validation and dispatch. The
        # Harness boundary separately enforces recovery policy and fresh approvals.
        plan.results = DeferredToolResults(approvals={call_id: ToolApproved() for call_id in plan.pending})
    return plan


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
        detached_messages = deepcopy(tuple(messages))
        value = self.prompt_factory(error, attempt_index, detached_messages)
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
        """Replace Pydantic's interrupted tail with observed text and finalized parts."""
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
        if not parts:
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

    def _safe_parts(self) -> list[ModelResponsePart]:
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
                if index in self._finalized_indices:
                    safe.append(part)
        return safe


def normalize_interrupted_history(
    messages: Sequence[ModelMessage],
    *,
    response_tracker: InterruptedResponseTracker | None = None,
    close_pending_tools: bool = False,
    close_tool_calls: bool = True,
) -> tuple[tuple[ModelMessage, ...], int]:
    """Close interrupted calls, optionally including unanswered calls in restored history."""
    normalized = list(response_tracker.sanitize(messages) if response_tracker is not None else messages)
    if not normalized:
        return (), 0

    tail = normalized[-1]
    if isinstance(tail, ModelResponse) and (
        tail.state == "interrupted" or (close_pending_tools and tail.state != "suspended")
    ):
        if tail.state == "interrupted" and not _native_parts_are_balanced(tail.parts):
            # Native results have no portable synthetic closure. Discard the
            # native group, not independently recoverable text or ordinary calls.
            parts = [part for part in tail.parts if not isinstance(part, NativeToolCallPart | NativeToolReturnPart)]
            if not parts:
                normalized.pop()
                return tuple(normalized), 0
            tail = replace(tail, parts=parts)
            normalized[-1] = tail
        if not close_tool_calls:
            return tuple(normalized), 0
        missing = _missing_tool_calls(tail, ())
        if missing:
            normalized.append(ModelRequest(parts=[_failed_tool_result(call) for call in missing]))
        return tuple(normalized), len(missing)

    if isinstance(tail, ModelRequest) and (tail.state == "interrupted" or close_pending_tools):
        response_index = next(
            (index for index in range(len(normalized) - 2, -1, -1) if isinstance(normalized[index], ModelResponse)),
            None,
        )
        if response_index is None or not close_tool_calls:
            return tuple(normalized), 0
        response = normalized[response_index]
        assert isinstance(response, ModelResponse)
        if response.state == "suspended":
            return tuple(normalized), 0
        result_parts = [
            part
            for message in normalized[response_index + 1 :]
            if isinstance(message, ModelRequest)
            for part in message.parts
        ]
        missing = _missing_tool_calls(response, result_parts)
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
        # Pydantic AI has no dedicated retry-exhaustion exception: tools use
        # "exceeded max retries", while output validation uses "exceeded maximum retries".
        text = str(error).lower()
        return "exceeded max" not in text or "retries" not in text
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
