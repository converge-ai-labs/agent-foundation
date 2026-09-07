"""Attempt-scoped coordination for Service control at Harness safe boundaries."""

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
    SafeFailure,
)
from a13n_harness.errors import RunError
from a13n_logging import get_logger
from pydantic_ai.capabilities import NodeResult
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import DeferredToolRequests
from pydantic_graph import End

from a13n_service.storage import ObjectStoreUnavailable

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
from .inbox_delivery import AdaptedThreadInboxEntry, incorporated_receipts, merge_receipts
from .objects import RunStateStore, StaleStateWriter, StoredRunState
from .state import (
    CompletedOutcomeCandidate,
    ConsumedThreadInboxEntry,
    HostContinuationState,
    RunStateEnvelope,
    RunStateOutcomeCandidate,
)

logger = get_logger(__name__)


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
    offered: dict[str, AdaptedThreadInboxEntry] = field(default_factory=dict)
    incorporated: dict[str, ConsumedThreadInboxEntry] = field(default_factory=dict)
    delivery_gate: _DeliveryGate = _DeliveryGate.open
    handoff_reason: RunAttemptYieldReason | None = None
    phase: _CoordinatorPhase = _CoordinatorPhase.active
    pending_checkpoint: RunStateEnvelope | None = None
    checkpoint_confirmed: bool = False
    model_attempt_bound: bool = False
    model_attempt_checkpoint: bool = True


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
        # Relational CAS receipts share a lock; object and Harness I/O do not.
        self._authority_lock = asyncio.Lock()
        self._gate.delivery_gate = (
            _DeliveryGate.first_response
            if envelope.input_disposition == "pending" and envelope.host.deferred is not None
            else _DeliveryGate.open
        )
        self._driver: HarnessControlDriver | None = None
        self._cancel_executor: Callable[[], None] | None = None

    @property
    def harness_identity(self) -> HarnessRunIdentity:
        if self._gate.identity is None:
            raise RunError("Harness Run has not entered execution.", code="foundation_control_identity_mismatch")
        return self._gate.identity

    @property
    def terminal_observation_allowed(self) -> bool:
        """Suppress the synthetic cancellation used only to quiesce a planned handoff."""

        return self._gate.phase is not _CoordinatorPhase.handoff_ready

    @property
    def handoff_ready(self) -> bool:
        return self._gate.phase is _CoordinatorPhase.handoff_ready

    def bind_executor(
        self,
        driver: HarnessControlDriver,
        cancel_executor: Callable[[], None],
    ) -> None:
        """Bind the only driver and executor cancellation scope before execution."""

        if self._driver is not None or self._cancel_executor is not None:
            raise RunError(
                "Service run control is already bound to an executor.",
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
            if self._gate.identity is not None:
                raise RunError(
                    "Service run control already has an active Harness Run.",
                    code="foundation_control_reused",
                )
            if identity.thread_id != self._state.envelope.thread_id:
                raise RunError(
                    "Harness Run identity does not match Service preparation.",
                    code="foundation_control_identity_mismatch",
                )
            self._gate.identity = identity
            try:
                async with self._authority_lock:
                    self._require_open()
                    mutation = await self._execution.enter_harness(
                        self._context,
                        preparation=preparation,
                        harness_run_id=identity.run_id,
                    )
                    self._advance(mutation)
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def after_stream_entry(self) -> None:
        """Honor a pre-existing handoff at the first direct complete boundary."""

        async with self._gate.lock:
            self._require_open()
            driver = self._require_driver()
            try:
                if self._gate.handoff_reason is None or self._state.envelope.input_disposition == "pending":
                    return
                await self._prepare_boundary()
                await self._checkpoint_state(await driver.export_state())
                await self._confirm_state()
                await self._try_handoff()
            except (ObjectStoreUnavailable, TimeoutError) as error:
                await self._defer_handoff(error)
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def bind_model_attempt(self, binding: HarnessContextBinding) -> None:
        async with self._gate.lock:
            self._require_open()
            self._require_driver().validate_binding(binding)
            try:
                await self._validate_authority()
                self._gate.model_attempt_bound = True
                self._gate.model_attempt_checkpoint = True
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def before_model_node(self, boundary: HarnessHookBoundary) -> None:
        """Offer eligible input before Pydantic drains the next model request."""

        async with self._gate.lock:
            self._require_boundary(boundary)
            try:
                await self._prepare_boundary()
                if self._gate.delivery_gate is _DeliveryGate.open and self._gate.handoff_reason is None:
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
            self._record_incorporation(request_context.messages)
            try:
                waiting_first_request = self._gate.delivery_gate is _DeliveryGate.first_response
                try:
                    await self._prepare_boundary()
                    if not waiting_first_request and (
                        self._gate.model_attempt_checkpoint
                        or self._state.envelope.input_disposition == "pending"
                        or self._gate.handoff_reason is not None
                    ):
                        await self._checkpoint(boundary, request_context.messages)
                        await self._confirm_state()
                except (ObjectStoreUnavailable, TimeoutError) as error:
                    await self._defer_handoff(error)
                if self._gate.handoff_reason is not None and not waiting_first_request and not self._gate.offered:
                    if await self._try_handoff():
                        return
                if not waiting_first_request:
                    await self._offer_pending(boundary)
                await self._increment_model_request()
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def before_nested_model_request(self) -> None:
        """Fence and charge nested provider I/O without exporting its temporary history."""

        async with self._gate.lock:
            try:
                await self._increment_model_request()
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def _increment_model_request(self) -> None:
        async with self._authority_lock:
            self._require_open()
            mutation = await self._execution.increment_model_request(self._context)
            self._advance(mutation)

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
                if self._gate.handoff_reason is not None and not self._gate.offered:
                    await self._try_handoff()
                else:
                    await self._offer_pending(boundary)
            except (ObjectStoreUnavailable, TimeoutError) as error:
                await self._defer_handoff(error)
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def request_handoff(self, reason: RunAttemptYieldReason) -> None:
        async with self._gate.lock:
            self._require_open()
            if self._gate.handoff_reason is not None and self._gate.handoff_reason is not reason:
                raise RunError(
                    "Service run control already has another handoff request.",
                    code="foundation_handoff_conflict",
                )
            self._gate.handoff_reason = reason

    async def commit_preparation(self) -> AttemptPreparationResult:
        """Commit preflight using the latest context after concurrent lease renewal."""

        async with self._gate.lock:
            self._require_open()
            try:
                async with self._authority_lock:
                    self._require_open()
                    decision = await self._execution.commit_preparation_success(self._context)
                    self._advance(decision.mutation)
                    if isinstance(decision, AttemptPreparationRejected):
                        self._gate.phase = _CoordinatorPhase.terminal
                    return decision
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def renew_lease(self) -> None:
        """Renew only this exact Attempt under the same serialized authority context."""

        async with self._authority_lock:
            if self._gate.phase in {_CoordinatorPhase.terminal, _CoordinatorPhase.yielded, _CoordinatorPhase.fenced}:
                return
            self._advance(
                await self._execution.heartbeat(
                    self._context,
                    lease_duration=self._context.lease_duration,
                )
            )

    async def reconcile(self) -> None:
        """Reread durable control facts and offer active input only after stream entry."""

        async with self._gate.lock:
            if self._gate.phase in {
                _CoordinatorPhase.terminal,
                _CoordinatorPhase.yielded,
                _CoordinatorPhase.handoff_ready,
            }:
                return
            self._require_open()
            try:
                await self._prepare_boundary()
                if (
                    self._gate.identity is None
                    or not self._gate.model_attempt_bound
                    or self._gate.delivery_gate is not _DeliveryGate.open
                    or self._gate.handoff_reason is not None
                ):
                    return
                driver = self._require_driver()
                for entry in await self._eligible_entries():
                    await driver.steer(entry.tagged_input(self._context.run_id))
                    self._gate.offered[entry.receipt.inbox_entry_id] = entry
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
                    async with self._authority_lock:
                        if self._gate.phase is not _CoordinatorPhase.handoff_ready:
                            raise AttemptAuthorityError("Attempt lost authority before yielding")
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
                    async with self._authority_lock:
                        self._require_open()
                        receipt = await committer.reconcile_cancelled(self._context)
                        self._gate.phase = _CoordinatorPhase.terminal
                    _require_terminal_disposition(receipt, RunTerminalDisposition.cancelled)
                else:
                    await self._validate_authority()
                    if result.status == "failed":
                        failure = result.failure
                        if failure is None:  # pragma: no cover - enforced by HarnessRunResult
                            raise RuntimeError("failed Harness result is missing its failure")
                        async with self._authority_lock:
                            self._require_open()
                            receipt = await committer.commit_failure(self._context, failure)
                            self._gate.phase = _CoordinatorPhase.terminal
                        _require_terminal_disposition(
                            receipt,
                            RunTerminalDisposition.retrying,
                            RunTerminalDisposition.failed,
                        )
                    else:
                        projection = await adapter.project(result)
                        await self._publish_terminal(projection)
                        await self._confirm_state()
                        commit = await committer.prepare_state_outcome(self._context, self._state)
                        async with self._authority_lock:
                            self._require_open()
                            receipt = await commit(self._context)
                            self._gate.phase = _CoordinatorPhase.terminal
                        expected = (
                            RunTerminalDisposition.completed
                            if isinstance(projection.candidate, CompletedOutcomeCandidate)
                            else RunTerminalDisposition.waiting
                        )
                        _require_terminal_disposition(receipt, expected)
                self._gate.phase = _CoordinatorPhase.terminal
                return receipt
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def recover_outcome(self, committer: RunTerminalCommitter) -> RunTerminalReceipt:
        """Adopt a prior complete candidate under the newly claimed object writer fence."""

        async with self._gate.lock:
            await self._prepare_boundary()
            commit = await committer.prepare_state_outcome(self._context, self._state)
            async with self._authority_lock:
                self._require_open()
                receipt = await commit(self._context)
                self._gate.phase = _CoordinatorPhase.terminal
                return receipt

    async def fail_execution(self, committer: RunTerminalCommitter, failure: SafeFailure) -> RunTerminalReceipt:
        """Classify a root-task failure using current authority, including pre-Harness failures."""

        async with self._gate.lock:
            await self._validate_authority()
            async with self._authority_lock:
                self._require_open()
                receipt = await committer.commit_failure(self._context, failure)
                self._gate.phase = _CoordinatorPhase.terminal
                return receipt

    async def authority_lost(self) -> None:
        """Terminally fence local control after lease authority cannot be confirmed."""

        async with self._authority_lock:
            if self._gate.phase in {
                _CoordinatorPhase.fenced,
                _CoordinatorPhase.terminal,
                _CoordinatorPhase.yielded,
            }:
                return
            await self._fence()

    async def close_admission(self) -> None:
        """Prevent later control work during executor teardown."""

        async with self._authority_lock:
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
        async with self._authority_lock:
            self._require_open()
            self._advance(await self._execution.validate(self._context))

    async def _confirm_state(self) -> None:
        async with self._authority_lock:
            self._require_open()
            self._gate.checkpoint_confirmed = False
            self._advance(await self._inbox.confirm_checkpoint(self._context, self._state))
            self._gate.checkpoint_confirmed = True

    async def _checkpoint(
        self,
        boundary: HarnessHookBoundary,
        messages: Sequence[ModelMessage],
    ) -> None:
        await self._checkpoint_state(await boundary.export_state(messages))

    async def _checkpoint_state(self, harness: HarnessState) -> None:
        self._record_incorporation(harness.message_history)
        await self._retry_publication()
        prior = self._state.envelope
        receipts = merge_receipts(prior.host.consumed_inbox_entries, self._gate.incorporated.values())
        host = HostContinuationState(consumed_inbox_entries=receipts)
        if prior.input_disposition == "applied" and prior.harness == harness and prior.host == host:
            self._gate.model_attempt_checkpoint = False
            return
        await self._publish(self._successor(harness, host, "progress", None))
        self._gate.model_attempt_checkpoint = False

    async def _publish_terminal(self, projection: HarnessOutcomeProjection) -> None:
        self._record_incorporation(projection.harness.message_history)
        await self._retry_publication()
        prior = self._state.envelope
        host = HostContinuationState(
            deferred=projection.deferred,
            consumed_inbox_entries=merge_receipts(prior.host.consumed_inbox_entries, self._gate.incorporated.values()),
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
            harness_schema_version=harness.schema_version,
            harness=harness,
            host=host,
            outcome_candidate=candidate,
        )
        return RunStateEnvelope.model_validate(payload)

    async def _publish(self, successor: RunStateEnvelope) -> None:
        await self._validate_authority()
        self._gate.pending_checkpoint = successor
        published = await self._states.replace(
            self._state,
            successor,
            run_attempt_id=self._context.run_attempt_id,
            fence=self._context.fence,
        )
        await self._validate_authority()
        self._accept_publication(published)

    def _accept_publication(self, published: StoredRunState) -> None:
        self._state = published
        self._gate.pending_checkpoint = None
        self._gate.checkpoint_confirmed = False
        for receipt in published.envelope.host.consumed_inbox_entries:
            self._gate.offered.pop(receipt.inbox_entry_id, None)
            self._gate.incorporated.pop(receipt.inbox_entry_id, None)

    async def _retry_publication(self) -> None:
        candidate = self._gate.pending_checkpoint
        if candidate is None:
            return
        await self._validate_authority()
        observed = await self._states.read(
            self._context.organization_id, self._context.run_id, expected_thread_id=self._context.thread_id
        )
        await self._validate_authority()
        if observed.envelope == candidate and observed.writer_fence == self._context.fence:
            self._accept_publication(observed)
        elif observed.info.version == self._state.info.version and observed.body == self._state.body:
            await self._publish(candidate)
        else:
            raise StaleStateWriter("Run state changed during checkpoint reconciliation")

    async def _defer_handoff(self, error: Exception) -> None:
        if self._gate.handoff_reason is None:
            raise error
        await self._validate_authority()
        logger.info("run_handoff_checkpoint_pending", extra={"run_attempt_id": self._context.run_attempt_id})

    def _record_incorporation(self, messages: Sequence[ModelMessage]) -> None:
        found = incorporated_receipts(messages, self._gate.offered.values(), run_id=self._context.run_id)
        represented = {receipt.inbox_entry_id for receipt in found} | self._gate.incorporated.keys()
        missing_prefix = False
        for entry_id in self._gate.offered:
            if entry_id not in represented:
                missing_prefix = True
            elif missing_prefix:
                raise RunError("Incorporated inbox input is not a FIFO prefix.", code="foundation_inbox_order_invalid")
        for receipt in found:
            if receipt.inbox_entry_id not in self._gate.incorporated:
                self._gate.incorporated[receipt.inbox_entry_id] = receipt
                logger.info(
                    "run_inbox_incorporated",
                    extra={"run_id": self._context.run_id, "inbox_entry_id": receipt.inbox_entry_id},
                )

    async def _offer_pending(self, boundary: HarnessHookBoundary) -> None:
        for entry in await self._eligible_entries():
            await boundary.enqueue(entry.tagged_input(self._context.run_id), priority="asap")
            self._gate.offered[entry.receipt.inbox_entry_id] = entry

    async def _eligible_entries(self) -> tuple[AdaptedThreadInboxEntry, ...]:
        if self._gate.offered:
            return ()
        entries = tuple(await self._inbox.read_eligible(self._context))
        await self._validate_authority()
        _validate_delivery_batch(entries, self._state.envelope)
        # The same provenance check handles a restored raw checkpoint without
        # receipts. No content-only or historical-format recovery path exists.
        represented = incorporated_receipts(
            self._state.envelope.harness.message_history, entries, run_id=self._context.run_id
        )
        if represented:
            prefix = tuple(entry.receipt for entry in entries[: len(represented)])
            if represented != prefix:
                raise RunError("Checkpointed inbox input is not a FIFO prefix.", code="foundation_inbox_order_invalid")
            self._gate.incorporated.update((receipt.inbox_entry_id, receipt) for receipt in represented)
            await self._checkpoint_state(self._state.envelope.harness)
            await self._confirm_state()
            return entries[len(represented) :]
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
                "Service run control received an incompatible Harness result.",
                code="foundation_control_identity_mismatch",
            )

    def _require_open(self) -> None:
        if self._gate.phase is not _CoordinatorPhase.active:
            raise RunError(
                "Service run control is terminally fenced.",
                code="foundation_control_fenced",
            )

    def _require_driver(self) -> HarnessControlDriver:
        driver = self._driver
        if driver is None:
            raise RunError(
                "Service run control is not bound to its Harness driver.",
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

    async def _try_handoff(self) -> bool:
        if self._gate.pending_checkpoint is not None or not self._gate.checkpoint_confirmed or self._gate.offered:
            return False
        async with self._authority_lock:
            self._require_open()
            if not await self._execution.can_handoff(self._context):
                self._gate.handoff_reason = None
                return False
        await self._require_driver().cancel()
        async with self._authority_lock:
            self._require_open()
            self._gate.phase = _CoordinatorPhase.handoff_ready
        return True

    async def _fence(self) -> None:
        self._gate.phase = _CoordinatorPhase.fenced
        cancel_executor = self._cancel_executor
        if cancel_executor is None:
            raise RunError(
                "Service run control is not bound to its executor cancellation scope.",
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
    "RunAttemptControl",
    "ThreadInboxReconciler",
]
