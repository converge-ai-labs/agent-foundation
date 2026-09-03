"""Attempt-scoped coordination for Foundation control at Harness safe boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Literal, Protocol

from a13n_harness import (
    AgentContext,
    AgentInstanceContext,
    HarnessRunResult,
    HarnessRunStream,
    HarnessState,
    RunInputValue,
)
from a13n_harness.errors import RunError
from pydantic_ai import RunContext
from pydantic_ai.capabilities import NodeResult
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import DeferredToolRequests
from pydantic_graph import End

from .attempts import (
    AttemptAuthority,
    AttemptAuthorityError,
    AttemptExecutionService,
    AttemptMutationReceipt,
    AttemptPreparationAccepted,
)
from .domain import RunAttemptYieldReason
from .harness_results import (
    HarnessOutcomeAdapter,
    HarnessOutcomeProjection,
    RunTerminalCommitter,
    RunTerminalDisposition,
    RunTerminalReceipt,
)
from .objects import RunStateStore, StoredRunState
from .state import (
    CompletedOutcomeCandidate,
    ConsumedThreadInboxEntry,
    HostContinuationState,
    RunStateEnvelope,
    RunStateOutcomeCandidate,
)


@dataclass(frozen=True, slots=True)
class AdaptedThreadInboxEntry:
    """One authorized FIFO entry materialized for native Harness enqueue."""

    delivery_sequence: int
    receipt: ConsumedThreadInboxEntry
    input: RunInputValue

    def __post_init__(self) -> None:
        if self.delivery_sequence < 1:
            raise ValueError("Thread inbox delivery sequence must be positive")


class ThreadInboxReconciler(Protocol):
    """Reconcile authoritative Thread-inbox receipts around Harness checkpoints."""

    async def confirm_checkpoint(
        self,
        authority: AttemptAuthority,
        state: StoredRunState,
    ) -> AttemptMutationReceipt: ...

    async def read_eligible(
        self,
        authority: AttemptAuthority,
    ) -> Sequence[AdaptedThreadInboxEntry]: ...


class _DeliveryGate(StrEnum):
    open = "open"
    first_response = "first_response"
    first_tool_batch = "first_tool_batch"


class _CoordinatorPhase(StrEnum):
    active = "active"
    handoff_ready = "handoff_ready"
    yielded = "yielded"
    terminal = "terminal"
    fenced = "fenced"


@dataclass(frozen=True, slots=True)
class _RunAttachment:
    stream: HarnessRunStream[Any]
    context: AgentContext


class FoundationRunControlCoordinator:
    """Serialize control, checkpoint, and handoff work for one current RunAttempt."""

    def __init__(
        self,
        *,
        authority: AttemptAuthority,
        execution: AttemptExecutionService,
        states: RunStateStore,
        state: StoredRunState,
        instance: AgentInstanceContext,
        inbox: ThreadInboxReconciler,
    ) -> None:
        envelope = state.envelope
        if envelope.run_id != authority.run_id:
            raise ValueError("Run state and Attempt authority must name the same Run")
        if envelope.outcome_candidate is not None:
            raise ValueError("A sealed outcome candidate cannot start active run control")
        self._authority = authority
        self._execution = execution
        self._states = states
        self._state = state
        self._instance = instance
        self._inbox = inbox
        self._lock = asyncio.Lock()
        self._attachment: _RunAttachment | None = None
        self._offered: list[ConsumedThreadInboxEntry] = []
        self._delivery_gate = (
            _DeliveryGate.first_response
            if envelope.input_disposition == "pending" and envelope.host.deferred is not None
            else _DeliveryGate.open
        )
        self._handoff_reason: RunAttemptYieldReason | None = None
        self._phase = _CoordinatorPhase.active

    async def attach_stream(
        self,
        stream: HarnessRunStream[Any],
        preparation: AttemptPreparationAccepted,
    ) -> None:
        """Bind the entered Harness identity before any stream observation is consumed."""

        async with self._lock:
            self._require_open()
            if self._attachment is not None:
                raise RunError(
                    "Foundation run control already has an active Harness Run.",
                    code="foundation_control_reused",
                )
            context = stream.context
            if stream.thread_id != self._state.envelope.thread_id or context.instance is not self._instance:
                raise RunError(
                    "Harness Run identity does not match Foundation preparation.",
                    code="foundation_control_identity_mismatch",
                )
            self._attachment = _RunAttachment(stream=stream, context=context)
            try:
                mutation = await self._execution.enter_harness(
                    self._authority,
                    preparation=preparation,
                    harness_run_id=stream.run_id,
                )
            except AttemptAuthorityError:
                self._fence()
                raise
            self._advance(mutation)

    async def bind_model_attempt(self, ctx: RunContext[AgentContext]) -> None:
        async with self._lock:
            self._require_callback_context(ctx)
            try:
                await self._validate_authority()
            except AttemptAuthorityError:
                self._fence()
                raise

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> None:
        async with self._lock:
            self._require_callback_context(ctx)
            try:
                await self._prepare_boundary()
                waiting_first_request = self._delivery_gate is _DeliveryGate.first_response
                if self._handoff_reason is not None:
                    if not waiting_first_request:
                        await self._checkpoint(ctx, request_context.messages)
                        await self._confirm_state()
                    self._quiesce_for_handoff()
                    return
                if not waiting_first_request:
                    await self._checkpoint(ctx, request_context.messages)
                    await self._confirm_state()
                    await self._offer_pending(ctx)
                mutation = await self._execution.increment_model_request(self._authority)
                self._advance(mutation)
            except AttemptAuthorityError:
                self._fence()
                raise

    async def after_model_response(
        self,
        ctx: RunContext[AgentContext],
        response: ModelResponse,
    ) -> None:
        async with self._lock:
            self._require_callback_context(ctx)
            try:
                await self._validate_authority()
                if self._delivery_gate is not _DeliveryGate.first_response:
                    return
                await self._confirm_state()
                if any(isinstance(part, ToolCallPart) for part in response.parts):
                    self._delivery_gate = _DeliveryGate.first_tool_batch
                    return
                self._delivery_gate = _DeliveryGate.open
                await self._offer_pending(ctx)
            except AttemptAuthorityError:
                self._fence()
                raise

    async def after_tool_batch(
        self,
        ctx: RunContext[AgentContext],
        result: NodeResult[AgentContext],
    ) -> None:
        async with self._lock:
            self._require_callback_context(ctx)
            try:
                await self._validate_authority()
                if isinstance(result, End):
                    if self._delivery_gate is not _DeliveryGate.first_tool_batch or _is_deferred(result):
                        return
                    await self._confirm_state()
                    self._delivery_gate = _DeliveryGate.open
                    await self._offer_pending(ctx)
                    return
                await self._confirm_state()
                await self._checkpoint(ctx, ctx.messages)
                await self._confirm_state()
                if self._delivery_gate is _DeliveryGate.first_tool_batch:
                    self._delivery_gate = _DeliveryGate.open
                if self._handoff_reason is not None:
                    self._quiesce_for_handoff()
                else:
                    await self._offer_pending(ctx)
            except AttemptAuthorityError:
                self._fence()
                raise

    async def request_handoff(self, reason: RunAttemptYieldReason) -> None:
        async with self._lock:
            self._require_open()
            if self._handoff_reason is not None and self._handoff_reason is not reason:
                raise RunError(
                    "Foundation run control already has another handoff request.",
                    code="foundation_handoff_conflict",
                )
            self._handoff_reason = reason

    async def complete_handoff(self) -> AttemptMutationReceipt:
        """Commit yielded only after a safe-boundary cancellation closed local admission."""

        async with self._lock:
            if self._phase is not _CoordinatorPhase.handoff_ready or self._handoff_reason is None:
                raise RunError(
                    "Foundation handoff has not reached a confirmed safe boundary.",
                    code="foundation_handoff_not_ready",
                )
            try:
                mutation = await self._execution.yield_attempt(self._authority, self._handoff_reason)
                self._advance(mutation)
                self._phase = _CoordinatorPhase.yielded
                return mutation
            except AttemptAuthorityError:
                self._fence()
                raise

    async def commit_terminal_result(
        self,
        result: HarnessRunResult[Any],
        *,
        adapter: HarnessOutcomeAdapter,
        committer: RunTerminalCommitter,
    ) -> RunTerminalReceipt | None:
        """Select one ordinary terminal result, or preserve a prepared handoff."""

        async with self._lock:
            self._require_result_identity(result)
            if self._phase is _CoordinatorPhase.handoff_ready:
                if result.status != "cancelled":
                    raise RunError(
                        "Harness produced a non-cancelled result after handoff quiescence.",
                        code="foundation_handoff_result_invalid",
                    )
                return None
            self._require_open()
            try:
                if result.status == "cancelled":
                    receipt = await committer.reconcile_cancelled(self._authority)
                    _require_terminal_disposition(receipt, RunTerminalDisposition.cancelled)
                else:
                    await self._validate_authority()
                    if result.status == "failed":
                        failure = result.failure
                        if failure is None:  # pragma: no cover - enforced by HarnessRunResult
                            raise RuntimeError("failed Harness result is missing its failure")
                        receipt = await committer.commit_failure(self._authority, failure)
                        _require_terminal_disposition(
                            receipt,
                            RunTerminalDisposition.retrying,
                            RunTerminalDisposition.failed,
                        )
                    else:
                        projection = await adapter.project(result)
                        await self._publish_terminal(projection)
                        await self._confirm_state()
                        receipt = await committer.commit_state_outcome(self._authority, self._state)
                        expected = (
                            RunTerminalDisposition.completed
                            if isinstance(projection.candidate, CompletedOutcomeCandidate)
                            else RunTerminalDisposition.waiting
                        )
                        _require_terminal_disposition(receipt, expected)
                self._phase = _CoordinatorPhase.terminal
                return receipt
            except AttemptAuthorityError:
                self._fence()
                raise

    @property
    def current_authority(self) -> AttemptAuthority:
        return self._authority

    @property
    def current_state(self) -> StoredRunState:
        return self._state

    @property
    def handoff_ready(self) -> bool:
        return self._phase is _CoordinatorPhase.handoff_ready

    async def _prepare_boundary(self) -> None:
        await self._validate_authority()
        await self._confirm_state()

    async def _validate_authority(self) -> None:
        self._advance(await self._execution.validate(self._authority))

    async def _confirm_state(self) -> None:
        self._advance(await self._inbox.confirm_checkpoint(self._authority, self._state))

    async def _checkpoint(
        self,
        ctx: RunContext[AgentContext],
        messages: Sequence[ModelMessage],
    ) -> None:
        harness = await ctx.deps.export_state(messages)
        prior = self._state.envelope
        receipts = (*prior.host.consumed_inbox_entries, *self._offered)
        host = HostContinuationState(consumed_inbox_entries=receipts)
        if prior.input_disposition == "applied" and prior.harness == harness and prior.host == host:
            self._offered.clear()
            return
        await self._publish(self._successor(harness, host, "progress", None))

    async def _publish_terminal(self, projection: HarnessOutcomeProjection) -> None:
        prior = self._state.envelope
        host = HostContinuationState(
            deferred=projection.deferred,
            consumed_inbox_entries=(*prior.host.consumed_inbox_entries, *self._offered),
        )
        checkpoint_kind = "completed" if isinstance(projection.candidate, CompletedOutcomeCandidate) else "waiting"
        await self._publish(
            self._successor(
                projection.harness,
                host,
                checkpoint_kind,
                projection.candidate,
            )
        )

    def _successor(
        self,
        harness: HarnessState,
        host: HostContinuationState,
        checkpoint_kind: Literal["progress", "waiting", "completed"],
        candidate: RunStateOutcomeCandidate | None,
    ) -> RunStateEnvelope:
        prior = self._state.envelope
        payload = prior.model_dump(mode="python", by_alias=True)
        payload.update(
            checkpoint_seq=prior.checkpoint_seq + 1,
            checkpoint_kind=checkpoint_kind,
            input_disposition="applied",
            last_checkpoint_run_attempt_id=self._authority.run_attempt_id,
            last_checkpoint_fence=self._authority.fence,
            harness_schema_version=harness.schema_version,
            harness=harness,
            host=host,
            outcome_candidate=candidate,
        )
        return RunStateEnvelope.model_validate(payload)

    async def _publish(self, successor: RunStateEnvelope) -> None:
        self._state = await self._execution.publish_checkpoint(
            self._authority,
            self._states,
            self._state,
            successor,
        )
        self._offered.clear()

    async def _offer_pending(self, ctx: RunContext[AgentContext]) -> None:
        if self._offered:
            return
        entries = tuple(await self._inbox.read_eligible(self._authority))
        _validate_delivery_batch(entries, self._state.envelope)
        for entry in entries:
            enqueue_id = (
                ctx.enqueue(entry.input, priority="asap")
                if isinstance(entry.input, str)
                else ctx.enqueue(*entry.input, priority="asap")
            )
            if enqueue_id is None:
                raise RunError(
                    "A Thread inbox entry produced no Harness input.",
                    code="foundation_inbox_input_invalid",
                )
            self._offered.append(entry.receipt)

    def _require_callback_context(self, ctx: RunContext[AgentContext]) -> None:
        self._require_open()
        attachment = self._attachment
        if (
            attachment is None
            or ctx.deps is not attachment.context
            or ctx.deps.instance is not self._instance
            or ctx.deps.thread_id != self._state.envelope.thread_id
            or ctx.deps.run_id != attachment.stream.run_id
        ):
            raise RunError(
                "Foundation run control received an incompatible Harness context.",
                code="foundation_control_identity_mismatch",
            )

    def _require_result_identity(self, result: HarnessRunResult[Any]) -> None:
        attachment = self._attachment
        if (
            attachment is None
            or result.thread_id != self._state.envelope.thread_id
            or result.thread_id != attachment.stream.thread_id
            or result.run_id != attachment.stream.run_id
        ):
            raise RunError(
                "Foundation run control received an incompatible Harness result.",
                code="foundation_control_identity_mismatch",
            )

    def _require_open(self) -> None:
        if self._phase is not _CoordinatorPhase.active:
            raise RunError(
                "Foundation run control is terminally fenced.",
                code="foundation_control_fenced",
            )

    def _advance(self, mutation: AttemptMutationReceipt) -> None:
        if (
            mutation.run_version < self._authority.expected_run_version
            or mutation.attempt_version < self._authority.expected_attempt_version
        ):
            raise RuntimeError("Attempt mutation receipt moved authority versions backwards")
        self._authority = replace(
            self._authority,
            expected_run_version=mutation.run_version,
            expected_attempt_version=mutation.attempt_version,
        )

    def _quiesce_for_handoff(self) -> None:
        attachment = self._attachment
        if attachment is None:
            raise RuntimeError("Harness stream is missing at a handoff boundary")
        self._phase = _CoordinatorPhase.handoff_ready
        attachment.stream.cancel()

    def _fence(self) -> None:
        self._phase = _CoordinatorPhase.fenced
        if self._attachment is not None:
            self._attachment.stream.cancel()


def _is_deferred(result: End[Any]) -> bool:
    return isinstance(result.data.output, DeferredToolRequests)


def _validate_delivery_batch(
    entries: tuple[AdaptedThreadInboxEntry, ...],
    state: RunStateEnvelope,
) -> None:
    if len(entries) + len(state.host.consumed_inbox_entries) > 1024:
        raise RunError(
            "Thread inbox receipts exceed the Run state limit.",
            code="foundation_inbox_capacity_exceeded",
        )
    sequences = tuple(entry.delivery_sequence for entry in entries)
    if sequences != tuple(sorted(sequences)) or len(sequences) != len(set(sequences)):
        raise RunError(
            "Thread inbox reconciliation returned a non-FIFO batch.",
            code="foundation_inbox_order_invalid",
        )
    receipt_ids = tuple(entry.receipt.inbox_entry_id for entry in entries)
    existing_ids = {receipt.inbox_entry_id for receipt in state.host.consumed_inbox_entries}
    if len(receipt_ids) != len(set(receipt_ids)) or existing_ids.intersection(receipt_ids):
        raise RunError(
            "Thread inbox reconciliation returned duplicate delivery receipts.",
            code="foundation_inbox_receipt_invalid",
        )


def _require_terminal_disposition(
    receipt: RunTerminalReceipt,
    *expected: RunTerminalDisposition,
) -> None:
    if receipt.disposition not in expected:
        raise RuntimeError("terminal committer returned an incompatible disposition")


__all__ = [
    "AdaptedThreadInboxEntry",
    "FoundationRunControlCoordinator",
    "ThreadInboxReconciler",
]
