"""Typed projection and durable-commit ports for terminal Harness results."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import rfc8785
from a13n_harness import HarnessRunResult, HarnessState, SafeFailure
from a13n_harness.toolsets.interaction import ASK_USER_QUESTION_TOOL_NAME
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_ai.tools import DeferredToolRequests
from pydantic_core import PydanticSerializationError, to_jsonable_python

from .attempts import AttemptContext
from .domain import JsonObject, PendingCallKind, PendingCallSummary, RunPendingSummary, RunWaitReason
from .objects import RunPayloadStore, StoredRunState
from .state import (
    CompletedOutcomeCandidate,
    DeferredContinuationState,
    RunPayloadEnvelope,
    RunStateOutcomeCandidate,
    WaitingOutcomeCandidate,
)

_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)
_JSON_OBJECT_ADAPTER = TypeAdapter(JsonObject)
_DEFERRED_REQUESTS_ADAPTER = TypeAdapter(DeferredToolRequests)
_MAX_OUTPUT_TEXT_CHARS = 65_536


class HarnessOutcomeProjectionError(ValueError):
    """A terminal Harness value cannot become bounded durable Run state."""

    code = "harness_outcome_projection_failed"


@dataclass(frozen=True, slots=True)
class HarnessOutcomeProjection:
    harness: HarnessState
    candidate: RunStateOutcomeCandidate
    deferred: DeferredContinuationState | None


class HarnessOutcomeAdapter(Protocol):
    """Map one successful Harness result into Foundation-owned state fields."""

    async def project[OutputT](self, result: HarnessRunResult[OutputT]) -> HarnessOutcomeProjection: ...


class RunTerminalDisposition(StrEnum):
    waiting = "waiting"
    completed = "completed"
    retrying = "retrying"
    failed = "failed"
    cancelled = "cancelled"


@dataclass(frozen=True, slots=True)
class RunTerminalReceipt:
    disposition: RunTerminalDisposition
    run_version: int
    attempt_version: int
    thread_version: int | None

    def __post_init__(self) -> None:
        if self.run_version < 1:
            raise ValueError("terminal receipt Run version must be positive")
        if self.attempt_version < 1:
            raise ValueError("terminal receipt Attempt version must be positive")
        if self.thread_version is not None and self.thread_version < 1:
            raise ValueError("terminal receipt Thread version must be positive")
        if (self.disposition is RunTerminalDisposition.retrying) != (self.thread_version is None):
            raise ValueError("only a retrying terminal decision omits the Thread version")


class RunTerminalCommitter(Protocol):
    """Own the final fenced outcome and active-control race transactions."""

    async def prepare_state_outcome(
        self,
        authority: AttemptContext,
        state: StoredRunState,
    ) -> Callable[[AttemptContext], Awaitable[RunTerminalReceipt]]:
        """Verify object references, then return a DB-only commit using fresh authority."""
        ...

    async def commit_failure(
        self,
        authority: AttemptContext,
        failure: SafeFailure,
    ) -> RunTerminalReceipt: ...

    async def reconcile_cancelled(
        self,
        authority: AttemptContext,
    ) -> RunTerminalReceipt: ...


class StoredHarnessOutcomeAdapter:
    """Build bounded completed or waiting projections from public Harness values."""

    def __init__(
        self,
        *,
        organization_id: str,
        run_id: str,
        payloads: RunPayloadStore,
        max_output_bytes: int,
        inline_output_bytes: int,
        output_schema_version: str = "1",
        client_tool_surface: JsonValue | None = None,
    ) -> None:
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        if not 1 <= inline_output_bytes <= max_output_bytes:
            raise ValueError("inline_output_bytes must be positive and no greater than max_output_bytes")
        if not 1 <= len(output_schema_version) <= 32:
            raise ValueError("output_schema_version must contain between 1 and 32 characters")
        self._organization_id = organization_id
        self._run_id = run_id
        self._payloads = payloads
        self._max_output_bytes = max_output_bytes
        self._inline_output_bytes = inline_output_bytes
        self._output_schema_version = output_schema_version
        if client_tool_surface is None:
            self._client_tool_surface = None
            self._client_tool_surface_digest = None
        else:
            self._client_tool_surface, encoded_surface = _encode_json_value(
                client_tool_surface,
                description="effective client-tool surface",
            )
            self._client_tool_surface_digest = hashlib.sha256(encoded_surface).hexdigest()

    async def project[OutputT](self, result: HarnessRunResult[OutputT]) -> HarnessOutcomeProjection:
        state = result.state
        if state is None:
            raise HarnessOutcomeProjectionError("successful Harness result is missing complete state")
        if result.status == "completed":
            candidate = await self._completed(result.output)
            return HarnessOutcomeProjection(harness=state, candidate=candidate, deferred=None)
        if result.status == "suspended":
            deferred = result.deferred
            if deferred is None:
                raise HarnessOutcomeProjectionError("suspended Harness result is missing deferred requests")
            continuation, candidate = self._waiting(deferred)
            return HarnessOutcomeProjection(
                harness=state,
                candidate=candidate,
                deferred=continuation,
            )
        raise HarnessOutcomeProjectionError("only successful Harness results produce outcome state")

    async def _completed(self, output: object) -> CompletedOutcomeCandidate:
        value, encoded = _encode_json_value(output)
        if len(encoded) > self._max_output_bytes:
            raise HarnessOutcomeProjectionError("Harness output exceeds the accepted output limit")
        output_text = value if isinstance(value, str) and len(value) <= _MAX_OUTPUT_TEXT_CHARS else None
        if len(encoded) <= self._inline_output_bytes:
            return CompletedOutcomeCandidate(output=value, output_text=output_text)
        reference = await self._payloads.create(
            self._organization_id,
            RunPayloadEnvelope(
                run_id=self._run_id,
                payload_kind="output",
                payload_schema_version=self._output_schema_version,
                payload=value,
            ),
        )
        return CompletedOutcomeCandidate(output_object=reference, output_text=output_text)

    def _waiting(
        self,
        requests: DeferredToolRequests,
    ) -> tuple[DeferredContinuationState, WaitingOutcomeCandidate]:
        serialized = _serialize_deferred(requests)
        calls = (
            *(
                PendingCallSummary(
                    call_id=request.tool_call_id,
                    kind=(
                        PendingCallKind.user_input
                        if request.tool_name == ASK_USER_QUESTION_TOOL_NAME
                        else PendingCallKind.client_tool
                    ),
                    tool_name=request.tool_name,
                )
                for request in requests.calls
            ),
            *(
                PendingCallSummary(
                    call_id=request.tool_call_id,
                    kind=PendingCallKind.approval,
                    tool_name=request.tool_name,
                )
                for request in requests.approvals
            ),
        )
        if not calls:
            raise HarnessOutcomeProjectionError("suspended Harness result has no pending requests")
        kinds = {call.kind for call in calls}
        wait_reason = RunWaitReason.multiple if len(kinds) > 1 else RunWaitReason(next(iter(kinds)).value)
        needs_client_surface = PendingCallKind.client_tool in kinds
        if needs_client_surface and self._client_tool_surface is None:
            raise HarnessOutcomeProjectionError("client-tool suspension is missing its effective surface")
        surface = self._client_tool_surface if needs_client_surface else None
        continuation = DeferredContinuationState(
            requests=serialized,
            effective_client_tool_surface=surface,
            effective_surface_digest_sha256=self._client_tool_surface_digest,
        )
        return continuation, WaitingOutcomeCandidate(
            wait_reason=wait_reason,
            pending=RunPendingSummary(calls=calls),
        )


def _encode_json_value(
    value: object,
    *,
    description: str = "Harness output",
) -> tuple[JsonValue, bytes]:
    try:
        serialized = to_jsonable_python(value, inf_nan_mode="constants")
        validated = _JSON_VALUE_ADAPTER.validate_python(serialized, strict=True)
        return validated, rfc8785.dumps(validated)
    except (TypeError, ValueError) as error:
        raise HarnessOutcomeProjectionError(f"{description} is not finite JSON") from error


def _serialize_deferred(requests: DeferredToolRequests) -> JsonObject:
    try:
        value = _DEFERRED_REQUESTS_ADAPTER.dump_python(requests, mode="json", warnings="error")
        return _JSON_OBJECT_ADAPTER.validate_python(value, strict=True)
    except (PydanticSerializationError, ValidationError) as error:
        raise HarnessOutcomeProjectionError("Harness deferred requests are not finite JSON") from error


__all__ = [
    "HarnessOutcomeAdapter",
    "HarnessOutcomeProjection",
    "HarnessOutcomeProjectionError",
    "RunTerminalCommitter",
    "RunTerminalDisposition",
    "RunTerminalReceipt",
    "StoredHarnessOutcomeAdapter",
]
