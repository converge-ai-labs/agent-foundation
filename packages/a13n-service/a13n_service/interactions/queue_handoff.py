"""State-first completion-time handoff to the queued successor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.environments.usage import lock_run_environments, refresh_run_retention
from a13n_service.hooks import InlineHookValidator
from a13n_service.interactions.environment_acceptance import add_run_with_environment
from a13n_service.interactions.environment_selection import (
    EnvironmentDefault,
    queued_environment_choice,
    requested_environment,
)
from a13n_service.lifecycle import new_mutation_id
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from ._outcome_transitions import (
    apply_completed_outcome,
    select_sealed_state,
    validate_outcome_candidate,
    validate_outcome_candidate_scope,
)
from ._transitions import charge_attempt_usage, terminalize_attempt
from .acceptance import (
    RunAcceptanceReceipt,
    require_session,
    validate_advancement,
    validate_prepared_run,
    validate_queued_run_input,
)
from .attempts import AttemptContext, AttemptMutationError, lock_attempt_authority
from .control_domain import QueuedSubmission, QueuedSubmissionFailure, QueuedSubmissionState
from .domain import Run, RunAttemptStatus, RunInputKind, RunLineageKind
from .errors import RunAcceptanceError
from .inbox_persistence import ThreadInboxConflict, apply_run_outcome, bind_unbound_async_entries
from .initialization import RunStateSeed, initialize_completed_continuation_state
from .inline_hooks import InlineHookAcceptance
from .input import AcceptedAgentInput
from .lifecycle import LifecycleWriter
from .models import RunAttemptRecord, RunRecord, ThreadRecord
from .objects import RunPayloadStore, RunStateStore, StaleStateWriter, StoredRunState
from .queue_persistence import QueueConsumptionConflict, consume_first_submission, fail_first_submission
from .state import CompletedOutcomeCandidate, RunCheckpoint, RunPayloadEnvelope


@dataclass(frozen=True, slots=True)
class CombinedQueueHandoffReceipt:
    source_run_id: str
    source_run_version: int
    source_attempt_version: int
    outcome: Literal["run_accepted", "submission_failed"]
    queued_submission: QueuedSubmission
    queue_version: int
    successor: RunAcceptanceReceipt | None = None

    def __post_init__(self) -> None:
        accepted = self.outcome == "run_accepted"
        if self.outcome not in {"run_accepted", "submission_failed"} or accepted != (self.successor is not None):
            raise ValueError("combined handoff outcome and successor are inconsistent")
        expected_state = QueuedSubmissionState.consumed if accepted else QueuedSubmissionState.failed
        if self.queued_submission.state is not expected_state:
            raise ValueError("combined handoff outcome and queued submission are inconsistent")


class CompletionQueueHandoffService:
    """Commit source completion and queue consumption as one relational change."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        states: RunStateStore,
        payloads: RunPayloadStore,
        inline_hooks: InlineHookValidator,
        *,
        lifecycle: LifecycleWriter,
        clock: Clock = utc_now,
    ) -> None:
        self._lifecycle = lifecycle
        self._sessions = sessions
        self._states = states
        self._payloads = payloads
        self._inline_hooks = InlineHookAcceptance(sessions, inline_hooks)
        self._clock = clock

    async def complete_and_consume(
        self,
        *,
        authority: AttemptContext,
        source_state: StoredRunState,
        successor_run: Run,
        successor_state: RunCheckpoint,
        queued_submission_id: str,
        submission_digest_sha256: str,
        accepted_input: AcceptedAgentInput,
        expected_thread_version: int,
        expected_queue_version: int,
        expected_head_run_id: str | None,
    ) -> CombinedQueueHandoffReceipt:
        """Atomically seal a completed source and accept the prepared queue head."""

        await self._inline_hooks.validate_queued_destination(
            organization_id=successor_run.organization_id,
            queued_submission_id=queued_submission_id,
            submission_digest_sha256=submission_digest_sha256,
        )
        candidate, input_payload = await self._verify_prepared(
            authority=authority,
            source_state=source_state,
            successor_run=successor_run,
            successor_state=successor_state,
            accepted_input=accepted_input,
        )
        await self._publish_initial(successor_run, successor_state)

        now = assume_utc(self._clock())
        try:
            async with transaction(self._sessions) as database:
                source, attempt, thread = await _lock_and_seal_source(
                    database,
                    authority=authority,
                    source_state=source_state,
                    candidate=candidate,
                    expected_thread_version=expected_thread_version,
                    expected_queue_version=expected_queue_version,
                    expected_head_run_id=expected_head_run_id,
                    now=now,
                    additional_environment_ids=(
                        (successor_run.environment_id,) if successor_run.environment_id else ()
                    ),
                )
                _validate_successor_scope(successor_run, thread.organization_id, thread.session_id, thread.id)

                await validate_advancement(
                    database,
                    thread,
                    source,
                    successor_run,
                    candidate_payload=input_payload,
                    next_head_run_id=source.id,
                )
                session_record_value = await require_session(database, successor_run)
                successor_record = await add_run_with_environment(
                    database,
                    run=successor_run,
                    state=successor_state,
                    workspace_id=session_record_value.workspace_id,
                    intent=requested_environment(
                        await queued_environment_choice(database, queued_submission_id),
                        default=EnvironmentDefault.thread,
                    ),
                )
                await bind_unbound_async_entries(
                    database,
                    organization_id=successor_run.organization_id,
                    thread_id=thread.id,
                    target_run_id=successor_run.id,
                    now=now,
                )
                consumed = await _consume_queue_head(
                    database,
                    run=successor_run,
                    queued_submission_id=queued_submission_id,
                    submission_digest_sha256=submission_digest_sha256,
                    now=now,
                )

                thread.head_run_id = source.id
                thread.current_run_id = successor_run.id
                thread.version += 2
                thread.queue_version += 1
                thread.updated_at = now
                await database.flush()
                await self._inline_hooks.authorize(
                    database,
                    run=successor_run,
                    workspace_id=session_record_value.workspace_id,
                    subscription=consumed.submission.hook_subscription,
                )
                mutation_id = new_mutation_id()
                await self._lifecycle.append_run_with_attempt_lifecycle(
                    database,
                    source,
                    "run.completed",
                    attempt=attempt,
                    attempt_event_type="run_attempt.succeeded",
                    mutation_id=mutation_id,
                    occurred_at=now,
                    actor_type="worker",
                    actor_id=attempt.worker_id,
                )
                successor_hook_subscription_id = await self._inline_hooks.create(
                    database,
                    run=successor_record,
                    workspace_id=session_record_value.workspace_id,
                    subscription=consumed.submission.hook_subscription,
                    now=now,
                )
                await self._lifecycle.append_accepted_run_lifecycle(
                    database,
                    successor_record,
                    mutation_id=mutation_id,
                )
                return CombinedQueueHandoffReceipt(
                    source_run_id=source.id,
                    source_run_version=source.version,
                    source_attempt_version=attempt.version,
                    outcome="run_accepted",
                    queued_submission=consumed,
                    successor=RunAcceptanceReceipt(
                        session_id=thread.session_id,
                        thread_id=thread.id,
                        thread_version=thread.version,
                        run_id=successor_run.id,
                        run_version=successor_run.version,
                        hook_subscription_id=successor_hook_subscription_id,
                    ),
                    queue_version=thread.queue_version,
                )
        except IntegrityError as error:
            raise RunAcceptanceError(
                "combined_handoff_conflict",
                "Combined handoff lost a concurrent mutation",
            ) from error

    async def complete_and_fail_permanently(
        self,
        *,
        authority: AttemptContext,
        source_state: StoredRunState,
        queued_submission_id: str,
        submission_digest_sha256: str,
        failure: QueuedSubmissionFailure,
        expected_thread_version: int,
        expected_queue_version: int,
        expected_head_run_id: str | None,
    ) -> CombinedQueueHandoffReceipt:
        """Atomically seal completion and terminally fail an invalid queue head."""

        candidate = await self._verify_source(authority=authority, source_state=source_state)
        now = assume_utc(self._clock())
        try:
            async with transaction(self._sessions) as database:
                source, attempt, thread = await _lock_and_seal_source(
                    database,
                    authority=authority,
                    source_state=source_state,
                    candidate=candidate,
                    expected_thread_version=expected_thread_version,
                    expected_queue_version=expected_queue_version,
                    expected_head_run_id=expected_head_run_id,
                    now=now,
                )
                try:
                    failed = await fail_first_submission(
                        database,
                        organization_id=source.organization_id,
                        thread_id=thread.id,
                        queued_submission_id=queued_submission_id,
                        submission_digest_sha256=submission_digest_sha256,
                        failure=failure,
                        now=now,
                    )
                except QueueConsumptionConflict as error:
                    raise RunAcceptanceError(
                        "queue_consumption_conflict",
                        "Queued submission changed before combined handoff",
                    ) from error
                thread.head_run_id = source.id
                thread.current_run_id = source.id
                thread.version += 1
                thread.queue_version += 1
                thread.updated_at = now
                await database.flush()
                mutation_id = new_mutation_id()
                await self._lifecycle.append_run_with_attempt_lifecycle(
                    database,
                    source,
                    "run.completed",
                    attempt=attempt,
                    attempt_event_type="run_attempt.succeeded",
                    mutation_id=mutation_id,
                    occurred_at=now,
                    actor_type="worker",
                    actor_id=attempt.worker_id,
                )
                return CombinedQueueHandoffReceipt(
                    source_run_id=source.id,
                    source_run_version=source.version,
                    source_attempt_version=attempt.version,
                    outcome="submission_failed",
                    queued_submission=failed.to_resource(),
                    queue_version=thread.queue_version,
                )
        except IntegrityError as error:
            raise RunAcceptanceError(
                "combined_handoff_conflict",
                "Combined handoff lost a concurrent mutation",
            ) from error

    async def _verify_prepared(
        self,
        *,
        authority: AttemptContext,
        source_state: StoredRunState,
        successor_run: Run,
        successor_state: RunCheckpoint,
        accepted_input: AcceptedAgentInput,
    ) -> tuple[CompletedOutcomeCandidate, RunPayloadEnvelope | None]:
        candidate = await self._verify_source(authority=authority, source_state=source_state)
        validate_prepared_run(successor_run, successor_state)
        _validate_combined_successor(authority, source_state, successor_run, successor_state)
        input_payload = await self._verify_input_payload(successor_run)
        validate_queued_run_input(successor_run, input_payload, accepted_input)
        return candidate, input_payload

    async def _verify_source(
        self,
        *,
        authority: AttemptContext,
        source_state: StoredRunState,
    ) -> CompletedOutcomeCandidate:
        validate_outcome_candidate(source_state, authority)
        candidate = source_state.envelope.outcome_candidate
        if not isinstance(candidate, CompletedOutcomeCandidate):
            raise RunAcceptanceError("combined_handoff_invalid", "Combined handoff requires completed source state")
        if candidate.output_object is not None:
            await self._payloads.verify_reference(
                authority.organization_id,
                authority.run_id,
                "output",
                candidate.output_object,
            )
        return candidate

    async def _verify_input_payload(self, run: Run) -> RunPayloadEnvelope | None:
        if run.input_object is None:
            return None
        return await self._payloads.verify_reference(run.organization_id, run.id, "input", run.input_object)

    async def _publish_initial(self, run: Run, state: RunCheckpoint) -> None:
        try:
            await self._states.create(run.organization_id, state)
        except StaleStateWriter:
            existing = await self._states.read(run.organization_id, run.id, expected_thread_id=run.thread_id)
            if existing.envelope != state:
                raise RunAcceptanceError(
                    "run_state_conflict",
                    "Run state key already contains different accepted state",
                ) from None


def _validate_combined_successor(
    authority: AttemptContext,
    source_state: StoredRunState,
    run: Run,
    state: RunCheckpoint,
) -> None:
    if (
        run.organization_id != authority.organization_id
        or run.parent_run_id != authority.run_id
        or run.thread_id != source_state.envelope.thread_id
        or run.lineage_kind is not RunLineageKind.continue_
        or run.input_kind is not RunInputKind.agent_input
        or run.retry_of_run_id is not None
    ):
        raise ValueError("combined handoff successor lineage or scope is invalid")
    expected = initialize_completed_continuation_state(
        RunStateSeed(
            run_id=run.id,
            agent_id=run.agent_id,
            agent_revision_id=run.agent_revision_id,
            effective_agent_config=state.effective_agent_config,
        ),
        source_state.envelope,
    )
    if state != expected:
        raise ValueError("combined handoff successor state is not derived from the completed source")


def _validate_successor_scope(run: Run, organization_id: str, session_id: str, thread_id: str) -> None:
    if (run.organization_id, run.session_id, run.thread_id) != (organization_id, session_id, thread_id):
        raise RunAcceptanceError(
            "combined_handoff_invalid",
            "Combined successor does not belong to the source Thread",
        )


async def _lock_and_seal_source(
    database: AsyncSession,
    *,
    authority: AttemptContext,
    source_state: StoredRunState,
    candidate: CompletedOutcomeCandidate,
    expected_thread_version: int,
    expected_queue_version: int,
    expected_head_run_id: str | None,
    now: datetime,
    additional_environment_ids: tuple[str, ...] = (),
) -> tuple[RunRecord, RunAttemptRecord, ThreadRecord]:
    source, attempt, thread = await lock_attempt_authority(
        database,
        authority,
        now,
        lock_inbox_origins=True,
    )
    if (
        thread.version != expected_thread_version
        or thread.queue_version != expected_queue_version
        or thread.current_run_id != source.id
        or thread.head_run_id != expected_head_run_id
    ):
        raise RunAcceptanceError(
            "combined_handoff_conflict",
            "Thread or queue changed before combined handoff",
        )
    validate_outcome_candidate_scope(source_state, source, thread)
    if attempt.status != RunAttemptStatus.running.value:
        raise AttemptMutationError("combined handoff requires Harness entry")
    await lock_run_environments(
        database,
        run=source,
        additional_environment_ids=additional_environment_ids,
    )
    await _seal_completed_source(
        database,
        source=source,
        attempt=attempt,
        source_state=source_state,
        candidate=candidate,
        now=now,
    )
    return source, attempt, thread


async def _seal_completed_source(
    database: AsyncSession,
    *,
    source: RunRecord,
    attempt: RunAttemptRecord,
    source_state: StoredRunState,
    candidate: CompletedOutcomeCandidate,
    now: datetime,
) -> None:
    apply_completed_outcome(source, candidate, now)
    select_sealed_state(source, run_attempt_id=attempt.id, state=source_state, now=now)
    terminalize_attempt(attempt, RunAttemptStatus.succeeded, now)
    charge_attempt_usage(source, attempt)
    source.current_run_attempt_id = None
    source.updated_at = now
    source.version += 1
    await refresh_run_retention(database, run=source, now=now)
    if not await apply_run_outcome(database, run=source, outcome="completed", state=source_state, now=now):
        raise ThreadInboxConflict("Run completion must process pending input before queue handoff")


async def _consume_queue_head(
    database: AsyncSession,
    *,
    run: Run,
    queued_submission_id: str,
    submission_digest_sha256: str,
    now: datetime,
) -> QueuedSubmission:
    try:
        consumed = await consume_first_submission(
            database,
            organization_id=run.organization_id,
            thread_id=run.thread_id,
            queued_submission_id=queued_submission_id,
            submission_digest_sha256=submission_digest_sha256,
            authority_principal=run.authority_principal,
            consumed_run_id=run.id,
            now=now,
        )
    except QueueConsumptionConflict as error:
        raise RunAcceptanceError(
            "queue_consumption_conflict",
            "Queued submission changed before combined handoff",
        ) from error
    if consumed.consumed_run_id != run.id:
        raise RuntimeError("combined handoff lost its queue correlation")
    return consumed.to_resource()


__all__ = ["CombinedQueueHandoffReceipt", "CompletionQueueHandoffService"]
