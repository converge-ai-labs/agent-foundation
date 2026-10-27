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
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters

from a13n_harness._context_history import _COMPACTION_PROMPT, _build_compacted_history
from a13n_harness.content import replace_request_parts, request_parts
from a13n_harness.errors import DefinitionError
from a13n_harness.model_context import _requires_exact_history
from a13n_harness.recovery import is_recoverable_model_failure
from a13n_harness.state import AgentContextState, HarnessState, StateStore, decode_messages, encode_messages
from a13n_harness.storage import RunStorage

__all__ = [
    "COMPACTION_CAPABILITY_ID",
    "BackgroundCompactionPolicy",
    "CompactionBasis",
    "CompactionInput",
    "CompactionModelRequest",
    "CompactionOperation",
    "CompactionOperator",
    "CompactionResult",
    "apply_compaction",
    "compaction_disposition",
    "compaction_tail",
    "generate_compaction",
]

COMPACTION_CAPABILITY_ID = "a13n.compaction"

_PARAMETERS = TypeAdapter(ModelRequestParameters)
_SETTINGS = TypeAdapter(dict[str, JsonValue])


class BackgroundCompactionPolicy(BaseModel):
    """Host-selected overlap policy, separate from the common compaction trigger."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    blocking_threshold: float = Field(default=0.95, gt=0, le=1)


class _CompactionState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: str
    basis_digest: str
    disposition: Literal["applied", "skipped"]
    restoration_pending: bool = False


class CompactionBasis(BaseModel):
    """Fixed canonical prefix identity and semantic inputs needed for replacement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    prefix_length: int = Field(gt=0)
    prefix_digest: str
    replay_json: str = Field(repr=False)

    @model_validator(mode="after")
    def _validate_replay(self) -> CompactionBasis:
        _ = self.replay_requests
        return self

    @property
    def replay_requests(self) -> tuple[ModelRequest, ...]:
        messages = decode_messages(self.replay_json.encode())
        if any(not isinstance(message, ModelRequest) for message in messages):
            raise ValueError("Compaction replay must contain requests only.")
        return tuple(message for message in messages if isinstance(message, ModelRequest))

    @property
    def digest(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()


class CompactionInput(BaseModel):
    """Detached summary request and Host dispatch attribution.

    Use ``create`` with native values; accessors decode fresh mutable values.
    Encoded fields are the storage representation, not model-call authority.
    Application needs only the basis, never this generation configuration.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    basis: CompactionBasis
    run_id: str = Field(min_length=1)
    model_id: str | None = None
    model_name: str
    provider_name: str
    request_json: str = Field(repr=False)
    parameters_json: str = Field(repr=False)
    settings_json: str = Field(repr=False)
    request_limit: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_snapshot(self) -> CompactionInput:
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
    ) -> CompactionInput:
        if not messages or not any(isinstance(message, ModelRequest) for message in messages):
            raise ValueError("Compaction requires a non-empty request history.")
        if _requires_exact_history(list(messages)):
            raise ValueError("Compaction cannot split unresolved tool calls or a suspended response.")
        basis = CompactionBasis(
            operation_id=operation_id,
            thread_id=thread_id,
            prefix_length=len(messages),
            prefix_digest=_prefix_digest(messages),
            replay_json=encode_messages(retained_requests).decode(),
        )
        # Remove structured output requirements, preserving ordinary tools,
        # instructions, native configuration and tool_choice for cache reuse.
        parameters = replace(
            parameters,
            # Host-only tool metadata may hold live callbacks and resources.
            # Serialize provider definitions, never copy or retain those owners.
            function_tools=[replace(tool, metadata=None) for tool in parameters.function_tools],
            output_mode="text",
            output_object=None,
            output_tools=[],
            prompted_output_template=None,
            allow_text_output=True,
            allow_image_output=False,
        )
        return cls(
            basis=basis,
            run_id=run_id,
            model_id=model_id,
            model_name=model_name,
            provider_name=provider_name,
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
    def request_messages(self) -> list[ModelMessage]:
        return list(decode_messages(self.request_json.encode()))

    @property
    def parameters(self) -> ModelRequestParameters:
        return _PARAMETERS.validate_json(self.parameters_json)

    @property
    def settings(self) -> ModelSettings:
        return cast(ModelSettings, _SETTINGS.validate_json(self.settings_json))


class CompactionResult(BaseModel):
    """Self-contained prefix replacement, independent of generation configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    basis: CompactionBasis
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def _nonblank(self) -> CompactionResult:
        if not self.summary.strip():
            raise ValueError("Compaction summary must not be blank.")
        return self

    @classmethod
    def from_summary(cls, basis: CompactionBasis, summary: str) -> CompactionResult:
        return cls(basis=basis, summary=summary.strip())


class CompactionOperation(BaseModel):
    """Non-consuming Host observation; failed operations remain visible until retired."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    input: CompactionInput
    status: Literal["running", "ready", "failed"]
    result: CompactionResult | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def _result_matches(self) -> CompactionOperation:
        if (self.status == "ready") != (self.result is not None):
            raise ValueError("Only a ready operation carries a result.")
        if self.result is not None and self.result.basis != self.input.basis:
            raise ValueError("Compaction result does not match the submitted basis.")
        return self


class CompactionOperator(ABC):
    """Host-owned execution independent of the submitting Run.

    Submit is idempotent by operation ID and admits at most one unacknowledged
    operation per Thread. Save ready results before notifying the Host. Inspection
    and waiting never consume results. Cancellation of a waiter must not cancel
    execution. The Host retires a result only after committing its disposition.
    """

    @abstractmethod
    async def submit(self, input: CompactionInput) -> CompactionOperation: ...

    @abstractmethod
    async def inspect(self, thread_id: str) -> CompactionOperation | None: ...

    @abstractmethod
    async def wait(self, thread_id: str, operation_id: str) -> CompactionOperation: ...


class CompactionModelRequest(Protocol):
    async def __call__(
        self,
        input: CompactionInput,
    ) -> ModelResponse:
        """Admit, account for and dispatch one request using fresh Host authority.

        The callback must not capture a closing parent Run. Native Model.request
        can perform dispatch inside the Host's admission/accounting scope. No
        Agent tool dispatcher or parent capabilities participate in this request.
        """
        ...


async def generate_compaction(
    input: CompactionInput,
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
    if input.request_limit is not None:
        max_attempts = min(max_attempts, input.request_limit)
    for attempt in range(max_attempts):
        try:
            response = await request(input)
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
        return CompactionResult.from_summary(input.basis, summary)
    raise AssertionError("unreachable")


def _validate_thread(basis: CompactionBasis, thread_id: str) -> None:
    if basis.thread_id != thread_id:
        raise DefinitionError("Compaction belongs to another Thread.", code="compaction_result_mismatch")


def _processed_disposition(
    state: _CompactionState | None, basis: CompactionBasis
) -> Literal["applied", "skipped"] | None:
    if state is None or state.operation_id != basis.operation_id:
        return None
    if state.basis_digest != basis.digest:
        raise DefinitionError("Compaction operation identity was reused.", code="compaction_result_mismatch")
    return state.disposition


async def compaction_disposition(
    state: HarnessState,
    *,
    basis: CompactionBasis,
    state_store: StateStore | None = None,
) -> Literal["applied", "skipped"] | None:
    """Read a checkpoint's terminal disposition for Host acknowledgement.

    Only the Host's selected, committed checkpoint permits retiring the operation.
    A candidate checkpoint or a restoration event does not acknowledge persistence.
    """
    _validate_thread(basis, state.thread_id)
    namespaces = AgentContextState(await RunStorage(state_store).resolve_context(state.agent_context_state))
    previous = await namespaces.read(COMPACTION_CAPABILITY_ID, _CompactionState, version="1")
    return _processed_disposition(previous, basis)


def _prefix_digest(messages: Sequence[ModelMessage]) -> str:
    # System authority belongs to the current executable. Keep native message
    # slots and rebuild semantic part annotations when stripping typed system parts.
    comparison = [
        replace_request_parts(
            message, [(part, items) for part, items in request_parts(message) if not isinstance(part, SystemPromptPart)]
        )
        if isinstance(message, ModelRequest)
        else message
        for message in messages
    ]
    return sha256(encode_messages(comparison)).hexdigest()


def _prefix_matches(basis: CompactionBasis, messages: Sequence[ModelMessage]) -> bool:
    prefix = messages[: basis.prefix_length]
    return len(prefix) == basis.prefix_length and _prefix_digest(prefix) == basis.prefix_digest


def _prepare_compaction(
    result: CompactionResult,
    *,
    thread_id: str,
    messages: Sequence[ModelMessage],
    previous: _CompactionState | None,
    exact: bool = False,
    next_request: ModelRequest | None = None,
) -> tuple[list[ModelMessage], _CompactionState | None]:
    """Decide against canonical history before recovery or request projection.

    A returned marker and history must be published together by the caller.
    None leaves provenance unchanged (duplicate delivery or a deferred boundary).
    """
    basis = result.basis
    _validate_thread(basis, thread_id)
    if _processed_disposition(previous, basis) is not None:
        return list(messages), None
    # Finish a prior restoration before replacing its retryable marker.
    if previous is not None and previous.restoration_pending:
        return list(messages), None
    if not _prefix_matches(basis, messages):
        disposition = "skipped"
        replacement = list(messages)
    elif (
        exact
        or _requires_exact_history(list(messages))
        or _requires_exact_history(list(messages[: basis.prefix_length]))
    ):
        return list(messages), None
    else:
        disposition = "applied"
        replacement = apply_compaction(result, thread_id=thread_id, messages=messages, next_request=next_request)
    return replacement, _CompactionState(
        operation_id=basis.operation_id,
        basis_digest=basis.digest,
        disposition=disposition,
        restoration_pending=disposition == "applied",
    )


def compaction_tail(basis: CompactionBasis, messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """Validate the fixed prefix and return every native message after its cutoff."""
    if not _prefix_matches(basis, messages):
        raise DefinitionError("Compaction prefix changed since submission.", code="compaction_prefix_mismatch")
    return list(decode_messages(encode_messages(messages[basis.prefix_length :])))


def apply_compaction(
    result: CompactionResult,
    *,
    thread_id: str,
    messages: Sequence[ModelMessage],
    retained_discovery: list[ModelMessage] | None = None,
    next_request: ModelRequest | None = None,
) -> list[ModelMessage]:
    """Build Compact(H0) + delta without mutating input or replacing unrelated state.

    Runtime callers use CompactionCapability.apply_result to also publish the
    canonical history, applied marker, refreshed descriptions and notifications.
    """
    basis = result.basis
    _validate_thread(basis, thread_id)
    tail = compaction_tail(basis, messages)
    if _requires_exact_history(list(messages[: basis.prefix_length])) or _requires_exact_history(list(messages)):
        raise DefinitionError("Compaction requires a complete tool boundary.", code="compaction_boundary_pending")
    return [
        *_build_compacted_history(
            [*messages, *([next_request] if next_request is not None else [])],
            result.summary,
            retained_requests=basis.replay_requests,
            retained_discovery=deepcopy(retained_discovery),
        ),
        *tail,
    ]
