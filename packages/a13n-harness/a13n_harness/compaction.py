"""Detached compaction work and deterministic prefix replacement.

The Host owns admission, model-call authority, execution lifetime and storage.
Harness owns the fixed input, summary protocol, retries and history transformation.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from random import uniform
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator
from pydantic_ai import ModelSettings
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters

from a13n_harness.errors import DefinitionError
from a13n_harness.model_context import _requires_exact_history
from a13n_harness.recovery import is_recoverable_model_failure
from a13n_harness.state import AgentContextState, HarnessState, StateStore, decode_messages, encode_messages
from a13n_harness.storage import RunStorage

COMPACTION_CAPABILITY_ID = "a13n.compaction"

_PARAMETERS = TypeAdapter(ModelRequestParameters)
_SETTINGS = TypeAdapter(dict[str, JsonValue])


class _CompactionState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    applied_operation_id: str | None = None
    applied_plan_digest: str | None = None
    restoration_pending: bool = False


class CompactionPlan(BaseModel):
    """Portable fixed prefix and request configuration, with no live Run resources.

    Encoded native values are immutable; accessors decode fresh mutable values.
    Use ``create`` to capture an eligible prefix and its semantic replay inputs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    model_id: str | None = None
    model_name: str
    provider_name: str
    history_json: str
    replay_json: str
    request_json: str
    parameters_json: str
    settings_json: str
    request_limit: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_snapshot(self) -> CompactionPlan:
        history = self.messages
        if not history or not any(isinstance(message, ModelRequest) for message in history):
            raise ValueError("Compaction requires a non-empty request history.")
        if _requires_exact_history(history):
            raise ValueError("Compaction cannot split unresolved tool calls or a suspended response.")
        if any(not isinstance(message, ModelRequest) for message in self.replay_requests):
            raise ValueError("Compaction replay must contain requests only.")
        decode_messages(self.request_json.encode())
        _PARAMETERS.validate_json(self.parameters_json)
        _SETTINGS.validate_json(self.settings_json)
        return self

    @classmethod
    def create(
        cls,
        *,
        operation_id: str,
        thread_id: str,
        run_id: str,
        messages: Sequence[ModelMessage],
        summary_messages: Sequence[ModelMessage] | None = None,
        retained_requests: Sequence[ModelRequest] = (),
        model_id: str | None = None,
        model_name: str,
        provider_name: str,
        parameters: ModelRequestParameters,
        settings: ModelSettings | None = None,
        request_limit: int | None = None,
    ) -> CompactionPlan:
        from a13n_harness.capabilities.context import _COMPACTION_PROMPT

        # Remove structured output requirements, preserving ordinary tools,
        # instructions, native configuration and tool_choice for cache reuse.
        parameters = replace(
            deepcopy(parameters),
            output_mode="text",
            output_object=None,
            output_tools=[],
            prompted_output_template=None,
            allow_text_output=True,
            allow_image_output=False,
        )
        return cls(
            operation_id=operation_id,
            thread_id=thread_id,
            run_id=run_id,
            model_id=model_id,
            model_name=model_name,
            provider_name=provider_name,
            history_json=encode_messages(messages).decode(),
            replay_json=encode_messages(retained_requests).decode(),
            request_json=encode_messages(
                [
                    *(messages if summary_messages is None else summary_messages),
                    ModelRequest(parts=[UserPromptPart(_COMPACTION_PROMPT)]),
                ]
            ).decode(),
            parameters_json=_PARAMETERS.dump_json(parameters).decode(),
            settings_json=_SETTINGS.dump_json(_SETTINGS.validate_python(settings or {})).decode(),
            request_limit=request_limit,
        )

    @property
    def messages(self) -> list[ModelMessage]:
        return list(decode_messages(self.history_json.encode()))

    @property
    def replay_requests(self) -> tuple[ModelRequest, ...]:
        messages = decode_messages(self.replay_json.encode())
        if any(not isinstance(message, ModelRequest) for message in messages):
            raise ValueError("Compaction replay must contain requests only.")
        return tuple(message for message in messages if isinstance(message, ModelRequest))

    @property
    def request_messages(self) -> list[ModelMessage]:
        return list(decode_messages(self.request_json.encode()))

    @property
    def parameters(self) -> ModelRequestParameters:
        return _PARAMETERS.validate_json(self.parameters_json)

    @property
    def settings(self) -> ModelSettings:
        return cast(ModelSettings, _SETTINGS.validate_json(self.settings_json))

    @property
    def digest(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()


class CompactionResult(BaseModel):
    """A generated summary correlated to one exact plan, ready for repeated reads."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    operation_id: str
    thread_id: str
    plan_digest: str
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def _nonblank(self) -> CompactionResult:
        if not self.summary.strip():
            raise ValueError("Compaction summary must not be blank.")
        return self

    @classmethod
    def from_summary(cls, plan: CompactionPlan, summary: str) -> CompactionResult:
        return cls(
            operation_id=plan.operation_id, thread_id=plan.thread_id, plan_digest=plan.digest, summary=summary.strip()
        )


class CompactionOperation(BaseModel):
    """Non-consuming Host observation; failed operations remain visible until retired."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    plan: CompactionPlan
    status: Literal["running", "ready", "failed"]
    result: CompactionResult | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def _result_matches(self) -> CompactionOperation:
        if (self.status == "ready") != (self.result is not None):
            raise ValueError("Only a ready operation carries a result.")
        if self.result is not None:
            _validate_result(self.plan, self.result, self.plan.thread_id)
        return self


class CompactionOperator(ABC):
    """Host-owned execution independent of the submitting Run.

    Submit is idempotent by operation ID and admits at most one unacknowledged
    operation per Thread. Save ready results before notifying the Host. Inspection
    and waiting never consume results. Cancellation of a waiter must not cancel
    execution. The Host retires a result only after saving the applied marker.
    """

    @abstractmethod
    async def submit(self, plan: CompactionPlan) -> CompactionOperation: ...

    @abstractmethod
    async def inspect(self, thread_id: str) -> CompactionOperation | None: ...

    @abstractmethod
    async def wait(self, thread_id: str, operation_id: str) -> CompactionOperation: ...


class CompactionModelRequest(Protocol):
    async def __call__(
        self,
        plan: CompactionPlan,
        messages: list[ModelMessage],
        settings: ModelSettings,
        parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Admit, account for and dispatch one request using fresh Host authority.

        The callback must not capture a closing parent Run. Native Model.request
        can perform dispatch inside the Host's admission/accounting scope. No
        Agent tool dispatcher or parent capabilities participate in this request.
        """
        ...


async def generate_compaction(
    plan: CompactionPlan,
    request: CompactionModelRequest,
    *,
    max_attempts: int = 3,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> CompactionResult:
    """Generate once for an admitted operation, retrying the same immutable input.

    The Host retains the terminal outcome across Runs. Retrying storage or
    publication must reuse this result, not invoke generation again.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if plan.request_limit is not None:
        max_attempts = min(max_attempts, plan.request_limit)
    for attempt in range(max_attempts):
        try:
            response = await request(plan, plan.request_messages, plan.settings, plan.parameters)
        except Exception as error:
            if attempt + 1 == max_attempts or not is_recoverable_model_failure(error):
                raise
            ceiling = min(30.0, 2.0**attempt)
            await sleep(uniform(ceiling / 2, ceiling))
            continue
        if any(isinstance(part, ToolCallPart) for part in response.parts):
            raise DefinitionError("Compaction returned a function-tool call.", code="compaction_summary_invalid")
        summary = "\n\n".join(part.content for part in response.parts if isinstance(part, TextPart)).strip()
        if not summary:
            raise DefinitionError("Compaction returned an empty summary.", code="compaction_summary_invalid")
        return CompactionResult.from_summary(plan, summary)
    raise AssertionError("unreachable")


def _validate_result(plan: CompactionPlan, result: CompactionResult, thread_id: str) -> None:
    if (
        plan.thread_id != thread_id
        or result.thread_id != thread_id
        or result.operation_id != plan.operation_id
        or result.plan_digest != plan.digest
    ):
        raise DefinitionError(
            "Compaction result does not match this Thread and plan.", code="compaction_result_mismatch"
        )


def _already_applied(state: _CompactionState | None, plan: CompactionPlan) -> bool:
    if state is None or state.applied_operation_id != plan.operation_id:
        return False
    if state.applied_plan_digest != plan.digest:
        raise DefinitionError("Compaction operation identity was reused.", code="compaction_result_mismatch")
    return True


async def apply_compaction_to_state(
    state: HarnessState,
    *,
    plan: CompactionPlan,
    result: CompactionResult,
    state_store: StateStore | None = None,
) -> HarnessState:
    """Return a compacted continuation without changing the supplied checkpoint.

    The Host selects the latest checkpoint under its writer fence and persists the
    returned value. Only the fixed prefix is hydrated for comparison; suffix refs,
    unrelated namespaces, Environment state and unknown fields are preserved.
    The next Run finishes current-tool restoration before projecting model context,
    even when its executable has no CompactionCapability or background operator.
    """
    _validate_result(plan, result, state.thread_id)
    namespaces = AgentContextState(state.agent_context_state)
    previous = await namespaces.read(COMPACTION_CAPABILITY_ID, _CompactionState, version="1")
    if _already_applied(previous, plan):
        return state
    messages = state.message_history
    cutoff = len(plan.messages)
    prefix = await RunStorage(state_store).resolve_messages(messages[:cutoff])
    compacted = apply_compaction(plan, result, thread_id=state.thread_id, messages=[*prefix, *messages[cutoff:]])
    await namespaces.write(
        COMPACTION_CAPABILITY_ID,
        _CompactionState(
            applied_operation_id=plan.operation_id,
            applied_plan_digest=plan.digest,
            restoration_pending=True,
        ),
        version="1",
    )
    return state.model_copy(
        deep=True,
        update={
            "message_history_json": encode_messages(compacted),
            "agent_context_state": await namespaces.snapshot(),
        },
    )


def compaction_tail(plan: CompactionPlan, messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """Validate the fixed prefix and return every native message after its cutoff."""
    prefix = plan.messages
    if encode_messages(messages[: len(prefix)]) != encode_messages(prefix):
        raise DefinitionError("Compaction prefix changed since submission.", code="compaction_prefix_mismatch")
    return list(decode_messages(encode_messages(messages[len(prefix) :])))


def apply_compaction(
    plan: CompactionPlan,
    result: CompactionResult,
    *,
    thread_id: str,
    messages: Sequence[ModelMessage],
    retained_discovery: list[ModelMessage] | None = None,
) -> list[ModelMessage]:
    """Build Compact(H0) + delta without mutating input or replacing unrelated state.

    Runtime callers use CompactionCapability.apply_result to also publish the
    canonical history, applied marker, refreshed descriptions and notifications.
    """
    from a13n_harness.capabilities.context import _build_compacted_history

    _validate_result(plan, result, thread_id)
    tail = compaction_tail(plan, messages)
    if _requires_exact_history(list(messages)):
        raise DefinitionError("Compaction requires a complete tool boundary.", code="compaction_boundary_pending")
    return [
        *_build_compacted_history(
            [*plan.messages, *tail[:1]],
            result.summary,
            retained_requests=plan.replay_requests,
            retained_discovery=deepcopy(retained_discovery),
        ),
        *tail,
    ]
