"""Attempt-scoped coordination for Foundation control at Harness safe boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Literal, Protocol

from a13n_harness import (
    AgentContext,
    HarnessRunResult,
    HarnessState,
    RunInputValue,
)
from a13n_harness.errors import RunError
from pydantic_ai.capabilities import NodeResult
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import DeferredToolRequests
from pydantic_graph import End

from .attempts import (
    AttemptAuthorityError,
    AttemptContext,
    AttemptExecutionService,
    AttemptMutationReceipt,
    AttemptPreparationAccepted,
    AttemptPreparationRejected,
    AttemptPreparationResult,
)
from .domain import RunAttemptYieldReason
from .harness_control import (
    HarnessContextBinding,
    HarnessControlDriver,
    HarnessHookBoundary,
    HarnessRunIdentity,
)
from .harness_results import (
    HarnessOutcomeAdapter,
    HarnessOutcomeProjection,
    RunTerminalCommitter,
    RunTerminalDisposition,
    RunTerminalReceipt,
)
from .objects import RunStateStore, StaleStateWriter, StoredRunState
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
        authority: AttemptContext,
        state: StoredRunState,
    ) -> AttemptMutationReceipt: ...

    async def read_eligible(
        self,
        authority: AttemptContext,
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


@dataclass(slots=True)
class _RunControlGate:
    """Private serialization and local state for one Attempt control facade."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    identity: HarnessRunIdentity | None = None
    offered: list[ConsumedThreadInboxEntry] = field(default_factory=list)
    delivery_gate: _DeliveryGate = _DeliveryGate.open
    handoff_reason: RunAttemptYieldReason | None = None
    phase: _CoordinatorPhase = _CoordinatorPhase.active


class RunAttemptControl:
    """Sole process-local control facade for one current RunAttempt."""

    def __init__(
        self,
        *,
        context: AttemptContext,
        execution: AttemptExecutionService,
        states: RunStateStore,
        state: StoredRunState,
        inbox: ThreadInboxReconciler,
    ) -> None:
        envelope = state.envelope
        if envelope.run_id != context.run_id or envelope.thread_id != context.thread_id:
            raise ValueError("Run state and Attempt context must name the same Run and Thread")
        self._context = context
        self._execution = execution
        self._states = states
        self._state = state
        self._inbox = inbox
        self._gate = _RunControlGate()
        self._gate.delivery_gate = (
            _DeliveryGate.first_response
            if envelope.input_disposition == "pending" and envelope.host.deferred is not None
            else _DeliveryGate.open
        )
        self._driver: HarnessControlDriver | None = None
        self._cancel_executor: Callable[[], None] | None = None

    @property
    def terminal_observation_allowed(self) -> bool:
        """Suppress the synthetic cancellation used only to quiesce a planned handoff."""

        return self._gate.phase is not _CoordinatorPhase.handoff_ready

    def bind_executor(
        self,
        driver: HarnessControlDriver,
        cancel_executor: Callable[[], None],
    ) -> None:
        """Bind the only driver and executor cancellation scope before execution."""

        if self._driver is not None or self._cancel_executor is not None:
            raise RunError(
                "Foundation run control is already bound to an executor.",
                code="foundation_control_reused",
            )
        self._driver = driver
        self._cancel_executor = cancel_executor

    async def enter_harness(
        self,
        identity: HarnessRunIdentity,
        preparation: AttemptPreparationAccepted,
    ) -> None:
        """Bind the entered Harness identity before any stream observation is consumed."""

        async with self._gate.lock:
            self._require_open()
            self._require_driver()
            if self._state.envelope.outcome_candidate is not None:
                raise RunError(
                    "A prepared outcome must be sealed without entering Harness.",
                    code="foundation_outcome_requires_adoption",
                )
            if self._gate.identity is not None:
                raise RunError(
                    "Foundation run control already has an active Harness Run.",
                    code="foundation_control_reused",
                )
            if identity.thread_id != self._state.envelope.thread_id:
                raise RunError(
                    "Harness Run identity does not match Foundation preparation.",
                    code="foundation_control_identity_mismatch",
                )
            self._gate.identity = identity
            try:
                mutation = await self._execution.enter_harness(
                    self._context,
                    preparation=preparation,
                    harness_run_id=identity.run_id,
                )
            except AttemptAuthorityError:
                await self._fence()
                raise
            self._advance(mutation)

    async def after_stream_entry(self) -> None:
        """Honor a pre-existing handoff at the first direct complete boundary."""

        async with self._gate.lock:
            self._require_open()
            driver = self._require_driver()
            try:
                if self._gate.handoff_reason is None:
                    return
                await self._prepare_boundary()
                await self._checkpoint_state(await driver.export_state())
                await self._confirm_state()
                await self._quiesce_for_handoff()
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def bind_model_attempt(self, binding: HarnessContextBinding) -> None:
        async with self._gate.lock:
            self._require_open()
            self._require_driver().validate_binding(binding)
            try:
                await self._validate_authority()
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def before_input_node(self, boundary: HarnessHookBoundary) -> None:
        """Offer durable input before restored terminal history bypasses model hooks."""

        async with self._gate.lock:
            self._require_boundary(boundary)
            try:
                await self._prepare_boundary()
                if (
                    self._state.envelope.input_disposition == "applied"
                    and self._gate.delivery_gate is _DeliveryGate.open
                    and self._gate.handoff_reason is None
                ):
                    await self._offer_pending(boundary)
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def before_model_request(
        self,
        boundary: HarnessHookBoundary,
        request_context: ModelRequestContext,
    ) -> None:
        async with self._gate.lock:
            self._require_boundary(boundary)
            try:
                await self._prepare_boundary()
                waiting_first_request = self._gate.delivery_gate is _DeliveryGate.first_response
                if not waiting_first_request:
                    await self._checkpoint(boundary, request_context.messages)
                    await self._confirm_state()
                if self._gate.handoff_reason is not None:
                    await self._quiesce_for_handoff()
                    return
                if not waiting_first_request:
                    await self._offer_pending(boundary)
                mutation = await self._execution.increment_model_request(self._context)
                self._advance(mutation)
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def after_model_response(
        self,
        boundary: HarnessHookBoundary,
        response: ModelResponse,
    ) -> None:
        async with self._gate.lock:
            self._require_boundary(boundary)
            try:
                await self._validate_authority()
                if self._gate.delivery_gate is not _DeliveryGate.first_response:
                    return
                await self._confirm_state()
                if any(isinstance(part, ToolCallPart) for part in response.parts):
                    self._gate.delivery_gate = _DeliveryGate.first_tool_batch
                    return
                self._gate.delivery_gate = _DeliveryGate.open
                await self._offer_pending(boundary)
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def after_tool_batch(
        self,
        boundary: HarnessHookBoundary,
        result: NodeResult[AgentContext],
        complete_messages: Sequence[ModelMessage],
    ) -> None:
        async with self._gate.lock:
            self._require_boundary(boundary)
            try:
                await self._validate_authority()
                if self._gate.delivery_gate is _DeliveryGate.first_response:
                    return
                if isinstance(result, End):
                    if self._gate.delivery_gate is not _DeliveryGate.first_tool_batch or _is_deferred(result):
                        return
                    await self._confirm_state()
                    self._gate.delivery_gate = _DeliveryGate.open
                    await self._offer_pending(boundary)
                    return
                await self._confirm_state()
                await self._checkpoint(boundary, complete_messages)
                await self._confirm_state()
                if self._gate.delivery_gate is _DeliveryGate.first_tool_batch:
                    self._gate.delivery_gate = _DeliveryGate.open
                if self._gate.handoff_reason is not None:
                    await self._quiesce_for_handoff()
                else:
                    await self._offer_pending(boundary)
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def request_handoff(self, reason: RunAttemptYieldReason) -> None:
        async with self._gate.lock:
            if self._gate.phase in {
                _CoordinatorPhase.terminal,
                _CoordinatorPhase.yielded,
                _CoordinatorPhase.fenced,
            }:
                return
            self._require_open()
            if self._gate.handoff_reason is not None and self._gate.handoff_reason is not reason:
                raise RunError(
                    "Foundation run control already has another handoff request.",
                    code="foundation_handoff_conflict",
                )
            self._gate.handoff_reason = reason

    async def commit_preparation(self) -> AttemptPreparationResult:
        """Commit preflight using the latest context after concurrent lease renewal."""

        async with self._gate.lock:
            self._require_open()
            try:
                decision = await self._execution.commit_preparation_success(self._context)
                self._advance(decision.mutation)
                if isinstance(decision, AttemptPreparationRejected):
                    self._gate.phase = _CoordinatorPhase.terminal
                else:
                    self._state = await self._execution.claim_state_writer(self._context, self._states, self._state)
                    if isinstance(self._state.envelope.outcome_candidate, CompletedOutcomeCandidate):
                        self._state = await self._execution.resume_completed_candidate(
                            self._context, self._states, self._state, preparation=decision
                        )
                return decision
            except (AttemptAuthorityError, StaleStateWriter):
                await self._fence()
                raise

    async def renew_lease(self) -> None:
        """Renew only this exact Attempt under the same serialized authority context."""

        async with self._gate.lock:
            if self._gate.phase in {_CoordinatorPhase.terminal, _CoordinatorPhase.yielded}:
                return
            self._require_open()
            self._advance(
                await self._execution.heartbeat(
                    self._context,
                    lease_duration=self._context.lease_duration,
                )
            )

    async def adopt_prepared_outcome(
        self,
        preparation: AttemptPreparationAccepted,
        *,
        committer: RunTerminalCommitter,
    ) -> RunTerminalReceipt:
        """Seal a predecessor's complete result under this prepared, still-leased owner."""

        async with self._gate.lock:
            self._require_open()
            state = self._state
            candidate = state.envelope.outcome_candidate
            if (
                candidate is None
                or self._gate.identity is not None
                or state.writer_fence != self._context.fence
                or state.envelope.last_checkpoint_fence >= self._context.fence
                or preparation.run_attempt_id != self._context.run_attempt_id
                or preparation.fence != self._context.fence
                or preparation.mutation.run_version != self._context.expected_run_version
                or preparation.mutation.attempt_version > self._context.expected_attempt_version
            ):
                raise RunError(
                    "Outcome adoption requires a claimed predecessor state and matching preparation.",
                    code="foundation_outcome_adoption_invalid",
                )
            try:
                await self._validate_authority()
                receipt = await committer.commit_state_outcome(self._context, state, preparation=preparation)
                _require_terminal_disposition(
                    receipt,
                    RunTerminalDisposition.completed
                    if isinstance(candidate, CompletedOutcomeCandidate)
                    else RunTerminalDisposition.waiting,
                    *(
                        (RunTerminalDisposition.retrying, RunTerminalDisposition.failed)
                        if isinstance(candidate, CompletedOutcomeCandidate)
                        else ()
                    ),
                )
                self._gate.phase = _CoordinatorPhase.terminal
                return receipt
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def reconcile(self) -> None:
        """Reread durable control facts and offer active input only after stream entry."""

        async with self._gate.lock:
            if self._gate.phase in {_CoordinatorPhase.terminal, _CoordinatorPhase.yielded}:
                return
            self._require_open()
            try:
                await self._prepare_boundary()
                if (
                    self._gate.identity is None
                    or self._gate.delivery_gate is not _DeliveryGate.open
                    or self._gate.handoff_reason is not None
                ):
                    return
                driver = self._require_driver()
                for entry in await self._eligible_entries():
                    await driver.steer(entry.input)
                    self._gate.offered.append(entry.receipt)
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def finalize(
        self,
        result: HarnessRunResult[Any],
        *,
        adapter: HarnessOutcomeAdapter,
        committer: RunTerminalCommitter,
    ) -> RunTerminalReceipt | AttemptMutationReceipt:
        """Commit the winning ordinary result or a prepared planned handoff."""

        async with self._gate.lock:
            self._require_result_identity(result)
            if self._gate.phase is _CoordinatorPhase.handoff_ready:
                if result.status != "cancelled":
                    raise RunError(
                        "Harness produced a non-cancelled result after handoff quiescence.",
                        code="foundation_handoff_result_invalid",
                    )
                reason = self._gate.handoff_reason
                if reason is None:  # pragma: no cover - maintained by the private gate
                    raise RuntimeError("handoff-ready control is missing its reason")
                try:
                    mutation = await self._execution.yield_attempt(self._context, reason)
                    self._advance(mutation)
                    self._gate.phase = _CoordinatorPhase.yielded
                    return mutation
                except AttemptAuthorityError:
                    await self._fence()
                    raise
            self._require_open()
            try:
                if result.status == "cancelled":
                    receipt = await committer.reconcile_cancelled(self._context)
                    _require_terminal_disposition(receipt, RunTerminalDisposition.cancelled)
                else:
                    await self._validate_authority()
                    if result.status == "failed":
                        failure = result.failure
                        if failure is None:  # pragma: no cover - enforced by HarnessRunResult
                            raise RuntimeError("failed Harness result is missing its failure")
                        receipt = await committer.commit_failure(self._context, failure)
                        _require_terminal_disposition(
                            receipt,
                            RunTerminalDisposition.retrying,
                            RunTerminalDisposition.failed,
                        )
                    else:
                        projection = await adapter.project(result)
                        await self._publish_terminal(projection)
                        await self._confirm_state()
                        receipt = await committer.commit_state_outcome(self._context, self._state)
                        expected = (
                            RunTerminalDisposition.completed
                            if isinstance(projection.candidate, CompletedOutcomeCandidate)
                            else RunTerminalDisposition.waiting
                        )
                        _require_terminal_disposition(
                            receipt,
                            expected,
                            *(
                                (RunTerminalDisposition.retrying, RunTerminalDisposition.failed)
                                if expected is RunTerminalDisposition.completed
                                else ()
                            ),
                        )
                self._gate.phase = _CoordinatorPhase.terminal
                return receipt
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def authority_lost(self) -> None:
        """Terminally fence local control after lease authority cannot be confirmed."""

        async with self._gate.lock:
            if self._gate.phase in {
                _CoordinatorPhase.fenced,
                _CoordinatorPhase.terminal,
                _CoordinatorPhase.yielded,
            }:
                return
            await self._fence()

    async def close_admission(self) -> None:
        """Prevent later control work during executor teardown."""

        async with self._gate.lock:
            if self._gate.phase is _CoordinatorPhase.active:
                await self._fence()

    @property
    def current_context(self) -> AttemptContext:
        return self._context

    @property
    def current_state(self) -> StoredRunState:
        return self._state

    async def _prepare_boundary(self) -> None:
        await self._validate_authority()
        await self._confirm_state()

    async def _validate_authority(self) -> None:
        self._advance(await self._execution.validate(self._context))

    async def _confirm_state(self) -> None:
        self._advance(await self._inbox.confirm_checkpoint(self._context, self._state))

    async def _checkpoint(
        self,
        boundary: HarnessHookBoundary,
        messages: Sequence[ModelMessage],
    ) -> None:
        await self._checkpoint_state(await boundary.export_state(messages))

    async def _checkpoint_state(self, harness: HarnessState) -> None:
        prior = self._state.envelope
        receipts = (*prior.host.consumed_inbox_entries, *self._gate.offered)
        host = HostContinuationState(consumed_inbox_entries=receipts)
        if prior.input_disposition == "applied" and prior.harness == harness and prior.host == host:
            self._gate.offered.clear()
            return
        await self._publish(self._successor(harness, host, "progress", None))

    async def _publish_terminal(self, projection: HarnessOutcomeProjection) -> None:
        prior = self._state.envelope
        host = HostContinuationState(
            deferred=projection.deferred,
            consumed_inbox_entries=(
                *prior.host.consumed_inbox_entries,
                *self._gate.offered,
            ),
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
        harness = HarnessState.new(
            thread_id=harness.thread_id,
            message_history=harness.message_history,
            agent_context_state=harness.agent_context_state,
        )
        prior = self._state.envelope
        payload = prior.model_dump(mode="python", by_alias=True)
        payload.update(
            checkpoint_seq=prior.checkpoint_seq + 1,
            checkpoint_kind=checkpoint_kind,
            input_disposition="applied",
            last_checkpoint_run_attempt_id=self._context.run_attempt_id,
            last_checkpoint_fence=self._context.fence,
            writer_fence=self._context.fence,
            harness_schema_version=harness.schema_version,
            harness=harness,
            host=host,
            outcome_candidate=candidate,
        )
        return RunStateEnvelope.model_validate(payload)

    async def _publish(self, successor: RunStateEnvelope) -> None:
        self._state = await self._execution.publish_checkpoint(
            self._context,
            self._states,
            self._state,
            successor,
        )
        self._gate.offered.clear()

    async def _offer_pending(self, boundary: HarnessHookBoundary) -> None:
        for entry in await self._eligible_entries():
            await boundary.enqueue(entry.input, priority="asap")
            self._gate.offered.append(entry.receipt)

    async def _eligible_entries(self) -> tuple[AdaptedThreadInboxEntry, ...]:
        if self._gate.offered:
            return ()
        entries = tuple(await self._inbox.read_eligible(self._context))
        _validate_delivery_batch(entries, self._state.envelope)
        return entries

    def _require_boundary(self, boundary: HarnessHookBoundary) -> None:
        self._require_open()
        self._require_driver().validate_boundary(boundary)

    def _require_result_identity(self, result: HarnessRunResult[Any]) -> None:
        identity = self._gate.identity
        if (
            identity is None
            or result.thread_id != self._state.envelope.thread_id
            or result.thread_id != identity.thread_id
            or result.run_id != identity.run_id
        ):
            raise RunError(
                "Foundation run control received an incompatible Harness result.",
                code="foundation_control_identity_mismatch",
            )

    def _require_open(self) -> None:
        if self._gate.phase is not _CoordinatorPhase.active:
            raise RunError(
                "Foundation run control is terminally fenced.",
                code="foundation_control_fenced",
            )

    def _require_driver(self) -> HarnessControlDriver:
        driver = self._driver
        if driver is None:
            raise RunError(
                "Foundation run control is not bound to its Harness driver.",
                code="foundation_control_unbound",
            )
        return driver

    def _advance(self, mutation: AttemptMutationReceipt) -> None:
        if (
            mutation.run_version < self._context.expected_run_version
            or mutation.attempt_version < self._context.expected_attempt_version
        ):
            raise RuntimeError("Attempt mutation receipt moved authority versions backwards")
        self._context = replace(
            self._context,
            expected_run_version=mutation.run_version,
            expected_attempt_version=mutation.attempt_version,
            lease_expires_at=mutation.lease_expires_at,
        )

    async def _quiesce_for_handoff(self) -> None:
        await self._require_driver().cancel()
        self._gate.phase = _CoordinatorPhase.handoff_ready

    async def _fence(self) -> None:
        self._gate.phase = _CoordinatorPhase.fenced
        cancel_executor = self._cancel_executor
        if cancel_executor is None:
            raise RunError(
                "Foundation run control is not bound to its executor cancellation scope.",
                code="foundation_control_unbound",
            )
        try:
            await self._require_driver().cancel()
        finally:
            cancel_executor()


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
    "RunAttemptControl",
    "ThreadInboxReconciler",
]
