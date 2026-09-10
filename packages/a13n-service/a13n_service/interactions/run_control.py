"""Attempt-scoped coordination for Service control at Harness safe boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol

from a13n_harness import (
    AgentContext,
    HarnessRunResult,
    HarnessState,
    RunInputValue,
    SafeFailure,
)
from a13n_harness.errors import RunError
from a13n_logging import get_logger
from anyio import sleep_forever
from pydantic_ai.capabilities import NodeResult
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import DeferredToolRequests
from pydantic_graph import End

from a13n_service.agents.domain import EffectiveAgentConfig, PreparedAgentPlugins
from a13n_service.observability import observe_phase, observe_phase_result
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
from .domain import Run, RunAttemptYieldReason
from .harness_control import (
    HarnessContextBinding,
    HarnessControlDriver,
    HarnessHookBoundary,
    HarnessRunIdentity,
)
from .harness_results import (
    AttemptCommitter,
    AttemptDisposition,
    AttemptOutcome,
    HarnessOutcomeAdapter,
    HarnessOutcomeProjection,
)
from .inbox_delivery import AdaptedThreadInboxEntry, incorporated_receipts, merge_receipts, retained_inbox_ids
from .objects import RunStateStore, StaleStateWriter, StoredRunState
from .state import (
    CompletedOutcomeCandidate,
    HostContinuationState,
    InboxReceipt,
    RunCheckpoint,
    RunStateOutcomeCandidate,
)
from .state_admission import claim_run_state

logger = get_logger(__name__)


class ThreadInboxReconciler(Protocol):
    """Reconcile authoritative Thread-inbox receipts around Harness checkpoints."""

    async def confirm_inbox_receipts(
        self,
        authority: AttemptContext,
        state: StoredRunState,
    ) -> AttemptMutationReceipt: ...

    async def read_eligible(
        self,
        authority: AttemptContext,
        config: EffectiveAgentConfig,
    ) -> Sequence[AdaptedThreadInboxEntry]: ...


class _DeliveryGate(StrEnum):
    open = "open"
    closed = "closed"
    first_response = "first_response"
    first_tool_batch = "first_tool_batch"


class _CoordinatorPhase(StrEnum):
    preparing = "preparing"
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
    incorporated: dict[str, InboxReceipt] = field(default_factory=dict)
    delivery_gate: _DeliveryGate = _DeliveryGate.open
    handoff_reason: RunAttemptYieldReason | None = None
    phase: _CoordinatorPhase = _CoordinatorPhase.active
    pending_checkpoint: RunCheckpoint | None = None
    inbox_receipts_confirmed: bool = False
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
        state: StoredRunState | None = None,
        inbox: ThreadInboxReconciler,
    ) -> None:
        self._context = context
        self._execution = execution
        self._states = states
        self._stored_state: StoredRunState | None = None
        self._inbox = inbox
        self._gate = _RunControlGate()
        # Serialize local terminalization with renewal; object and Harness I/O stay outside.
        self._authority_lock = asyncio.Lock()
        self._gate.phase = _CoordinatorPhase.preparing
        if state is not None:
            self._install_state(state)
        self._driver: HarnessControlDriver | None = None
        self._cancel_executor: Callable[[], None] | None = None
        self._pre_execution_outcome: AttemptOutcome | None = None

    def _install_state(self, state: StoredRunState) -> None:
        self._require_open()
        envelope = state.envelope
        if envelope.run_id != self._context.run_id or envelope.thread_id != self._context.thread_id:
            raise ValueError("Run state and Attempt context must name the same Run and Thread")
        self._stored_state = state
        self._gate.delivery_gate = (
            _DeliveryGate.first_response
            if not envelope.initial_input_applied and envelope.host.deferred is not None
            else _DeliveryGate.open
        )

    async def claim_state_writer(self, run: Run) -> None:
        """Admit the complete recovery object while monitoring remains active."""

        self._require_open()
        if self._stored_state is not None or self._gate.phase is not _CoordinatorPhase.preparing:
            raise RuntimeError("State admission may run only once before preparation")
        state = await claim_run_state(
            run,
            self._states,
            context=self._context,
            validate_authority=self._validate_authority,
        )
        self._install_state(state)

    async def prepare_plugins(self, prepared: PreparedAgentPlugins) -> None:
        """Publish the complete configuration before opening any plugin runtime."""

        async with self._gate.lock:
            self._require_open()
            if self._gate.phase is not _CoordinatorPhase.preparing:
                raise RuntimeError("Plugin preparation requires a preparing Attempt")
            await self._validate_authority()
            state = await self._states.prepare_plugins(
                self.current_state,
                prepared,
                attempt_number=self._context.attempt_number,
            )
            self._install_state(state)
            await self._validate_authority()

    async def reconcile_recovery_state(self) -> None:
        """Confirm existing evidence before constructing or entering a new Harness."""

        async with self._gate.lock:
            self._require_open()
            if self._gate.phase is not _CoordinatorPhase.active:
                raise RuntimeError("Recovery confirmation requires successful preparation")
            await self._prepare_boundary()
            # Only inspect pending payloads when retained provenance needs repair.
            # Waiting successors with pending feedback cannot read new inbox input.
            state = self.current_state.envelope
            missing = retained_inbox_ids(state.harness.message_history, run_id=self._context.run_id) - {
                receipt.inbox_entry_id for receipt in state.host.inbox_receipts
            }
            if self._gate.delivery_gate is _DeliveryGate.open and missing:
                await self._eligible_entries()

    @property
    def pre_execution_outcome(self) -> AttemptOutcome | None:
        """Return the committed receipt when input preparation ended execution."""

        return self._pre_execution_outcome

    @property
    def harness_identity(self) -> HarnessRunIdentity:
        if self._gate.identity is None:
            raise RunError("Harness Run has not entered execution.", code="service_control_identity_mismatch")
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
                code="service_control_reused",
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
                    code="service_control_reused",
                )
            if identity.thread_id != self.current_state.envelope.thread_id:
                raise RunError(
                    "Harness Run identity does not match Service preparation.",
                    code="service_control_identity_mismatch",
                )
            self._gate.identity = identity
            try:
                async with self._authority_lock:
                    self._require_open()
                    await self._execution.enter_harness(
                        self._context,
                        preparation=preparation,
                        harness_run_id=identity.run_id,
                    )
                    self._gate.phase = _CoordinatorPhase.active
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def after_stream_entry(self) -> None:
        """Honor a pre-existing handoff at the first direct complete boundary."""

        async with self._gate.lock:
            self._require_open()
            driver = self._require_driver()
            try:
                # Continuation input is prepared at stream entry but is not yet
                # incorporated. Its first model hook publishes the new receipts.
                state = self.current_state.envelope
                if (
                    self._gate.handoff_reason is None
                    or not state.initial_input_applied
                    or state.outcome_candidate is not None
                ):
                    return
                await self._prepare_boundary()
                await self._checkpoint_state(await driver.export_state())
                await self._confirm_inbox_receipts()
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
                        or not self.current_state.envelope.initial_input_applied
                        or self._gate.handoff_reason is not None
                    ):
                        await self._checkpoint(boundary, request_context.messages)
                        await self._confirm_inbox_receipts()
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
            await self._execution.increment_model_request(self._context)

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
                await self._confirm_inbox_receipts()
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
                    await self._confirm_inbox_receipts()
                    self._gate.delivery_gate = _DeliveryGate.open
                    await self._offer_pending(boundary)
                    return
                await self._confirm_inbox_receipts()
                await self._checkpoint(boundary, complete_messages)
                await self._confirm_inbox_receipts()
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
                    code="service_handoff_conflict",
                )
            self._gate.handoff_reason = reason

    async def commit_preparation(self) -> AttemptPreparationResult:
        """Commit preflight against current leased Attempt state."""

        async with self._gate.lock:
            self._require_open()
            if self._stored_state is None:
                raise RuntimeError("Preparation requires a confirmed state claim")
            try:
                async with self._authority_lock:
                    self._require_open()
                    decision = await self._execution.commit_preparation_success(self._context)
                    if isinstance(decision, AttemptPreparationRejected):
                        self._gate.phase = _CoordinatorPhase.terminal
                    else:
                        self._gate.phase = _CoordinatorPhase.active
                    return decision
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def renew_lease(self) -> None:
        """Renew only this exact Attempt under the same serialized authority context."""

        async with self._authority_lock:
            if self._gate.phase in {_CoordinatorPhase.terminal, _CoordinatorPhase.yielded, _CoordinatorPhase.fenced}:
                return
            await self._execution.heartbeat(self._context, lease_duration=self._context.lease_duration)

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
                    self._gate.phase is _CoordinatorPhase.preparing
                    or self._gate.identity is None
                    or not self._gate.model_attempt_bound
                    or self._gate.delivery_gate is not _DeliveryGate.open
                    or self._gate.handoff_reason is not None
                ):
                    return
                driver = self._require_driver()
                for entry in await self._eligible_entries():
                    if await driver.steer(entry.tagged_input(self._context.run_id)) is None:
                        break
                    self._gate.offered[entry.receipt.inbox_entry_id] = entry
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def close_delivery(self) -> None:
        """Join any in-flight offer before runtime resources close; retain the lease."""

        async with self._gate.lock:
            self._gate.delivery_gate = _DeliveryGate.closed

    async def finalize(
        self,
        result: HarnessRunResult[Any],
        *,
        adapter: HarnessOutcomeAdapter,
        committer: AttemptCommitter,
    ) -> AttemptOutcome | AttemptMutationReceipt:
        """Commit the winning ordinary result or a prepared planned handoff."""

        async with self._gate.lock:
            self._require_result_identity(result)
            if self._gate.phase is _CoordinatorPhase.handoff_ready:
                if result.status != "cancelled":
                    raise RunError(
                        "Harness produced a non-cancelled result after handoff quiescence.",
                        code="service_handoff_result_invalid",
                    )
                reason = self._gate.handoff_reason
                if reason is None:  # pragma: no cover - maintained by the private gate
                    raise RuntimeError("handoff-ready control is missing its reason")
                try:
                    async with self._authority_lock:
                        if self._gate.phase is not _CoordinatorPhase.handoff_ready:
                            raise AttemptAuthorityError("Attempt lost authority before yielding")
                        mutation = await self._execution.yield_attempt(self._context, reason)
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
                    _require_terminal_disposition(receipt, AttemptDisposition.cancelled)
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
                            AttemptDisposition.retrying,
                            AttemptDisposition.failed,
                        )
                    else:
                        projection = await adapter.project(result)
                        await self._publish_terminal(projection)
                        await self._confirm_inbox_receipts()
                        receipt = await self._commit_outcome(committer)
                self._gate.phase = _CoordinatorPhase.terminal
                return receipt
            except AttemptAuthorityError:
                await self._fence()
                raise

    async def recover_outcome(self, committer: AttemptCommitter) -> AttemptOutcome | None:
        """Commit a saved candidate or continue its accepted pending input."""

        async with self._gate.lock:
            self._require_open()
            await self._prepare_boundary()
            receipt = await self._commit_outcome(committer)
            return None if receipt.disposition is AttemptDisposition.continuing else receipt

    async def _commit_outcome(self, committer: AttemptCommitter) -> AttemptOutcome:
        verified = await committer.verify_state_outcome(self._context, self.current_state)
        async with self._authority_lock:
            self._require_open()
            receipt = await committer.commit_verified_state_outcome(self._context, verified)
            if receipt.disposition is not AttemptDisposition.continuing or self._gate.identity is not None:
                self._gate.phase = _CoordinatorPhase.terminal
            return receipt

    async def continuation_input(self, committer: AttemptCommitter) -> RunInputValue:
        """Supply pending input, or adopt the saved outcome if preparation outlived it."""

        async with self._gate.lock:
            self._require_open()
            while True:
                entries = await self._eligible_entries()
                if entries:
                    entry = entries[0]
                    self._gate.offered[entry.receipt.inbox_entry_id] = entry
                    return entry.tagged_input(self._context.run_id)
                with observe_phase("a13n.service.persist", operation="continuation_decision") as span:
                    receipt = await self._commit_outcome(committer)
                    observe_phase_result(
                        span,
                        disposition=receipt.disposition.value,
                        run_version=receipt.run_version,
                        attempt_version=receipt.attempt_version,
                    )
                if receipt.disposition is not AttemptDisposition.continuing:
                    self._pre_execution_outcome = receipt
                    break
                # An input arrived between the FIFO read and the locked outcome decision.
        cancel_executor = self._cancel_executor
        if cancel_executor is None:
            raise RuntimeError("Completion continuation requires a bound executor")
        cancel_executor()
        # Unwind the Harness input factory through its cancellation cleanup, before entry.
        await sleep_forever()
        raise AssertionError("cancelled continuation resumed")  # pragma: no cover

    async def fail_execution(self, committer: AttemptCommitter, failure: SafeFailure) -> AttemptOutcome:
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
            if self._gate.phase in {_CoordinatorPhase.preparing, _CoordinatorPhase.active}:
                await self._fence()

    @property
    def current_context(self) -> AttemptContext:
        return self._context

    @property
    def current_state(self) -> StoredRunState:
        if self._stored_state is None:
            raise RuntimeError("Run state writer has not been admitted")
        return self._stored_state

    async def _prepare_boundary(self) -> None:
        await self._validate_authority()
        if self._gate.phase is _CoordinatorPhase.active:
            await self._confirm_inbox_receipts()

    async def _validate_authority(self) -> AttemptMutationReceipt:
        async with self._authority_lock:
            self._require_open()
            return await self._execution.validate(self._context)

    async def _confirm_inbox_receipts(self) -> None:
        async with self._authority_lock:
            self._require_open()
            if self._gate.inbox_receipts_confirmed:
                return
            await self._inbox.confirm_inbox_receipts(self._context, self.current_state)
            self._gate.inbox_receipts_confirmed = True

    async def _checkpoint(
        self,
        boundary: HarnessHookBoundary,
        messages: Sequence[ModelMessage],
    ) -> None:
        await self._checkpoint_state(await boundary.export_state(messages))

    async def _checkpoint_state(self, harness: HarnessState) -> None:
        self._record_incorporation(harness.message_history)
        await self._retry_publication()
        prior = self.current_state.envelope
        receipts = merge_receipts(prior.host.inbox_receipts, self._gate.incorporated.values())
        host = HostContinuationState(inbox_receipts=receipts)
        if (
            prior.initial_input_applied
            and prior.outcome_candidate is None
            and prior.harness == harness
            and prior.host == host
        ):
            self._gate.model_attempt_checkpoint = False
            return
        await self._publish(self._successor(harness, host, "progress", None))
        self._gate.model_attempt_checkpoint = False

    async def _publish_terminal(self, projection: HarnessOutcomeProjection) -> None:
        self._record_incorporation(projection.harness.message_history)
        await self._retry_publication()
        prior = self.current_state.envelope
        host = HostContinuationState(
            deferred=projection.deferred,
            inbox_receipts=merge_receipts(prior.host.inbox_receipts, self._gate.incorporated.values()),
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
    ) -> RunCheckpoint:
        harness = HarnessState.new(
            thread_id=harness.thread_id,
            message_history=harness.message_history,
            agent_context_state=harness.agent_context_state,
        )
        prior = self.current_state.envelope
        payload = prior.model_dump(mode="python", by_alias=True)
        payload.update(
            checkpoint_seq=prior.checkpoint_seq + 1,
            checkpoint_kind=checkpoint_kind,
            last_checkpoint_run_attempt_id=self._context.run_attempt_id,
            last_checkpoint_fence=self._context.attempt_number,
            harness_schema_version=harness.schema_version,
            harness=harness,
            host=host,
            outcome_candidate=candidate,
        )
        return RunCheckpoint.model_validate(payload)

    async def _publish(self, successor: RunCheckpoint) -> None:
        await self._validate_authority()
        self._gate.pending_checkpoint = successor
        published = await self._states.replace(
            self.current_state,
            successor,
            run_attempt_id=self._context.run_attempt_id,
            attempt_number=self._context.attempt_number,
        )
        await self._validate_authority()
        self._accept_publication(published)

    def _accept_publication(self, published: StoredRunState) -> None:
        self._stored_state = published
        self._gate.pending_checkpoint = None
        self._gate.inbox_receipts_confirmed = False
        for receipt in published.envelope.host.inbox_receipts:
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
        if observed.envelope == candidate and observed.writer_fence == self._context.attempt_number:
            self._accept_publication(observed)
        elif observed.info.version == self.current_state.info.version and observed.body == self.current_state.body:
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
                raise RunError("Incorporated inbox input is not a FIFO prefix.", code="service_inbox_order_invalid")
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
        entries = tuple(
            await self._inbox.read_eligible(self._context, self.current_state.envelope.effective_agent_config)
        )
        await self._validate_authority()
        _validate_delivery_batch(entries, self.current_state.envelope)
        # The same provenance check handles a restored raw checkpoint without
        # receipts. No content-only or historical-format recovery path exists.
        represented = incorporated_receipts(
            self.current_state.envelope.harness.message_history, entries, run_id=self._context.run_id
        )
        if represented:
            prefix = tuple(entry.receipt for entry in entries[: len(represented)])
            if represented != prefix:
                raise RunError("Checkpointed inbox input is not a FIFO prefix.", code="service_inbox_order_invalid")
            self._gate.incorporated.update((receipt.inbox_entry_id, receipt) for receipt in represented)
            prior = self.current_state.envelope
            host = prior.host.model_copy(
                update={"inbox_receipts": merge_receipts(prior.host.inbox_receipts, represented)}
            )
            kind = prior.checkpoint_kind if prior.checkpoint_kind != "initial" else "progress"
            await self._publish(self._successor(prior.harness, host, kind, prior.outcome_candidate))
            await self._confirm_inbox_receipts()
            return entries[len(represented) :]
        return entries

    def _require_boundary(self, boundary: HarnessHookBoundary) -> None:
        self._require_open()
        self._require_driver().validate_boundary(boundary)

    def _require_result_identity(self, result: HarnessRunResult[Any]) -> None:
        identity = self._gate.identity
        if (
            identity is None
            or result.thread_id != self.current_state.envelope.thread_id
            or result.thread_id != identity.thread_id
            or result.run_id != identity.run_id
        ):
            raise RunError(
                "Service run control received an incompatible Harness result.",
                code="service_control_identity_mismatch",
            )

    def _require_open(self) -> None:
        if self._gate.phase not in {_CoordinatorPhase.preparing, _CoordinatorPhase.active}:
            raise RunError(
                "Service run control is terminally fenced.",
                code="service_control_fenced",
            )

    def _require_driver(self) -> HarnessControlDriver:
        driver = self._driver
        if driver is None:
            raise RunError(
                "Service run control is not bound to its Harness driver.",
                code="service_control_unbound",
            )
        return driver

    async def _try_handoff(self) -> bool:
        if self._gate.pending_checkpoint is not None or not self._gate.inbox_receipts_confirmed or self._gate.offered:
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
                code="service_control_unbound",
            )
        try:
            await self._require_driver().cancel()
        finally:
            cancel_executor()


def _is_deferred(result: End[Any]) -> bool:
    return isinstance(result.data.output, DeferredToolRequests)


def _validate_delivery_batch(
    entries: tuple[AdaptedThreadInboxEntry, ...],
    state: RunCheckpoint,
) -> None:
    if len(entries) + len(state.host.inbox_receipts) > 1024:
        raise RunError(
            "Thread inbox receipts exceed the Run state limit.",
            code="service_inbox_capacity_exceeded",
        )
    sequences = tuple(entry.delivery_sequence for entry in entries)
    if sequences != tuple(sorted(sequences)) or len(sequences) != len(set(sequences)):
        raise RunError(
            "Thread inbox reconciliation returned a non-FIFO batch.",
            code="service_inbox_order_invalid",
        )
    receipt_ids = tuple(entry.receipt.inbox_entry_id for entry in entries)
    existing_ids = {receipt.inbox_entry_id for receipt in state.host.inbox_receipts}
    if len(receipt_ids) != len(set(receipt_ids)) or existing_ids.intersection(receipt_ids):
        raise RunError(
            "Thread inbox reconciliation returned duplicate delivery receipts.",
            code="service_inbox_receipt_invalid",
        )


def _require_terminal_disposition(
    receipt: AttemptOutcome,
    *expected: AttemptDisposition,
) -> None:
    if receipt.disposition not in expected:
        raise RuntimeError("terminal committer returned an incompatible disposition")


__all__ = [
    "RunAttemptControl",
    "ThreadInboxReconciler",
]
