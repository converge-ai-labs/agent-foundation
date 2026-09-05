"""State-first relational acceptance for prepared durable Runs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import canonical_digest
from a13n_service.environments.domain import EnvironmentSelection, ExistingEnvironmentSelection
from a13n_service.environments.selection import Omitted, bind_environment_intent, queued_environment_choice
from a13n_service.environments.usage import add_run_with_environment, schedule_environment_maintenance
from a13n_service.hooks import InlineHookValidator
from a13n_service.hooks.domain import InlineHookSubscriptionInput
from a13n_service.hooks.persistence import load_inline_hook_subscription
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now

from .control_domain import (
    QueuedSubmissionConsumptionReceipt,
    QueuedSubmissionFailure,
    RunAcceptanceReceipt,
    WaitingRunContinueInput,
    WaitingRunFeedback,
)
from .control_models import QueuedSubmissionRecord
from .control_records import inbox_counter_record
from .domain import (
    Run,
    RunInputKind,
    RunLineageKind,
    RunStatus,
    Session,
    Thread,
    ThreadOriginKind,
    ThreadRole,
)
from .errors import RunAcceptanceError
from .inbox_persistence import (
    abandon_waiting_entries,
    bind_unbound_async_entries,
    bind_waiting_entries,
)
from .inline_hooks import InlineHookAcceptance
from .input import AcceptedAgentInput
from .lifecycle import append_accepted_run_lifecycle
from .models import RunRecord, SessionRecord, ThreadRecord
from .objects import RunPayloadStore, RunStateStore, StaleStateWriter
from .queue_persistence import QueueConsumptionConflict, consume_first_submission, fail_first_submission
from .records import session_record, thread_record
from .state import RunPayloadEnvelope, RunStateEnvelope


class RunAcceptanceService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        states: RunStateStore,
        payloads: RunPayloadStore,
        inline_hooks: InlineHookValidator,
        *,
        clock=None,
    ) -> None:
        self._sessions = sessions
        self._states = states
        self._payloads = payloads
        self._inline_hooks = InlineHookAcceptance(sessions, inline_hooks)
        self._clock = clock or utc_now

    async def accept_new_thread(
        self,
        *,
        session: Session | None,
        thread: Thread,
        run: Run,
        state: RunStateEnvelope,
        hook_subscription: InlineHookSubscriptionInput | None = None,
        final_validator: Callable[[AsyncSession], Awaitable[None]] | None = None,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        environment: EnvironmentSelection | Omitted | None = Omitted.UNSET,
    ) -> RunAcceptanceReceipt:
        run = bind_environment_intent(run, environment)
        validate_prepared_run(run, state)
        _validate_new_thread(thread, run, session)
        replay = await self._load_replay(run, state, accepted_thread_version=1)
        if replay is not None:
            await self._require_inline_hook_replay(run, hook_subscription)
            return replay
        await self._inline_hooks.validate_destination(hook_subscription)
        await self._verify_input_payload(run)
        await self._publish_initial(run, state)
        try:
            async with transaction(self._sessions) as database:
                if final_validator is not None:
                    await final_validator(database)
                if session is None:
                    session_record_value = await _require_session(database, run)
                    workspace_id = session_record_value.workspace_id
                else:
                    database.add(session_record(session))
                    workspace_id = session.workspace_id
                await self._inline_hooks.authorize(
                    database,
                    run=run,
                    workspace_id=workspace_id,
                    subscription=hook_subscription,
                )
                if thread.origin_kind is not ThreadOriginKind.new:
                    await _require_origin(database, thread, run)
                database.add(thread_record(thread))
                run_record_value = await add_run_with_environment(
                    database,
                    run=run,
                    state=state,
                    workspace_id=workspace_id,
                    choice=environment,
                )
                database.add(inbox_counter_record(thread))
                hook_subscription_id = await self._inline_hooks.create(
                    database,
                    run=run_record_value,
                    workspace_id=workspace_id,
                    subscription=hook_subscription,
                    now=self._clock(),
                )
                await append_accepted_run_lifecycle(database, run_record_value)
                receipt = _receipt(thread, run, hook_subscription_id=hook_subscription_id)
                if transaction_hook is not None:
                    await transaction_hook(database, receipt)
        except IntegrityError as error:
            return await self._reconcile_conflict(run, state, error, accepted_thread_version=1)
        return receipt

    async def advance_thread(
        self,
        *,
        run: Run,
        state: RunStateEnvelope,
        expected_thread_version: int,
        expected_current_run_id: str | None,
        expected_head_run_id: str | None,
        next_head_run_id: str | None,
        hook_subscription: InlineHookSubscriptionInput | None = None,
        inherit_parent_environment: bool = False,
        final_validator: Callable[[AsyncSession], Awaitable[None]] | None = None,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        environment: EnvironmentSelection | Omitted | None = Omitted.UNSET,
    ) -> RunAcceptanceReceipt:
        run = bind_environment_intent(run, environment)
        validate_prepared_run(run, state)
        accepted_thread_version = expected_thread_version + 1
        replay = await self._load_replay(run, state, accepted_thread_version=accepted_thread_version)
        if replay is not None:
            await self._require_inline_hook_replay(run, hook_subscription)
            return replay
        await self._inline_hooks.validate_destination(hook_subscription)
        candidate_payload = await self._verify_input_payload(run)
        await self._verify_retry_payload(run, candidate_payload)
        await self._publish_initial(run, state)
        try:
            async with transaction(self._sessions) as database:
                if final_validator is not None:
                    await final_validator(database)
                thread = await _lock_thread(database, run)
                _require_thread_precondition(
                    thread,
                    expected_version=expected_thread_version,
                    expected_current_run_id=expected_current_run_id,
                    expected_head_run_id=expected_head_run_id,
                )
                current = (
                    await _load_run(database, run.tenant_id, thread.current_run_id) if thread.current_run_id else None
                )
                if current is not None and current.status in {RunStatus.accepted.value, RunStatus.running.value}:
                    raise RunAcceptanceError("thread_busy", "Thread already has active Run work")
                await _validate_advancement(
                    database,
                    thread,
                    current,
                    run,
                    candidate_payload=candidate_payload,
                    next_head_run_id=next_head_run_id,
                )
                session_record_value = await _require_session(database, run)
                await self._inline_hooks.authorize(
                    database,
                    run=run,
                    workspace_id=session_record_value.workspace_id,
                    subscription=hook_subscription,
                )
                selected_environment = environment
                if inherit_parent_environment and environment is Omitted.UNSET:
                    if run.parent_run_id is None:
                        raise RunAcceptanceError("run_parent_required", "Historical continuation needs a parent Run")
                    parent = await _load_run(database, run.tenant_id, run.parent_run_id)
                    selected_environment = (
                        ExistingEnvironmentSelection(environment_id=parent.environment_id)
                        if parent.environment_id
                        else None
                    )
                    run = run.model_copy(update={"environment_access": parent.environment_access})
                run_record_value = await add_run_with_environment(
                    database,
                    run=run,
                    state=state,
                    workspace_id=session_record_value.workspace_id,
                    choice=selected_environment,
                )
                if run.input_kind in {RunInputKind.waiting_feedback, RunInputKind.waiting_continue}:
                    await database.flush()
                    assert run.parent_run_id is not None
                    await bind_waiting_entries(
                        database,
                        tenant_id=run.tenant_id,
                        thread_id=thread.id,
                        source_waiting_run_id=run.parent_run_id,
                        target_run_id=run.id,
                        now=self._clock(),
                    )
                elif thread.head_run_id is not None and thread.head_run_id != next_head_run_id:
                    prior_head = await _load_run(database, run.tenant_id, thread.head_run_id)
                    if prior_head.status == RunStatus.waiting.value:
                        await abandon_waiting_entries(
                            database,
                            tenant_id=run.tenant_id,
                            thread_id=thread.id,
                            source_waiting_run_id=prior_head.id,
                            now=self._clock(),
                        )
                thread.version += 1
                thread.current_run_id = run.id
                if current is not None:
                    await schedule_environment_maintenance(database, run=current, now=self._clock())
                thread.head_run_id = next_head_run_id
                thread.updated_at = self._clock()
                await database.flush()
                hook_subscription_id = await self._inline_hooks.create(
                    database,
                    run=run_record_value,
                    workspace_id=session_record_value.workspace_id,
                    subscription=hook_subscription,
                    now=self._clock(),
                )
                await append_accepted_run_lifecycle(database, run_record_value)
                receipt = RunAcceptanceReceipt(
                    session_id=thread.session_id,
                    thread_id=thread.id,
                    thread_version=thread.version,
                    run_id=run.id,
                    run_version=run.version,
                    hook_subscription_id=hook_subscription_id,
                )
                if transaction_hook is not None:
                    await transaction_hook(database, receipt)
        except IntegrityError as error:
            return await self._reconcile_conflict(
                run,
                state,
                error,
                accepted_thread_version=accepted_thread_version,
            )
        return receipt

    async def consume_queued(
        self,
        *,
        run: Run,
        state: RunStateEnvelope,
        queued_submission_id: str,
        submission_digest_sha256: str,
        accepted_input: AcceptedAgentInput,
        expected_thread_version: int,
        expected_queue_version: int,
        expected_current_run_id: str | None,
        expected_head_run_id: str | None,
        next_head_run_id: str | None,
        final_validator: Callable[[AsyncSession], Awaitable[None]] | None = None,
    ) -> QueuedSubmissionConsumptionReceipt:
        """Atomically consume the first queue row and accept its prepared Run."""

        validate_prepared_run(run, state)
        accepted_thread_version = expected_thread_version + 1
        replay = await self._load_replay(run, state, accepted_thread_version=accepted_thread_version)
        if replay is not None:
            queued = await self._validate_queue_replay(
                run=run,
                queued_submission_id=queued_submission_id,
                submission_digest_sha256=submission_digest_sha256,
            )
            return QueuedSubmissionConsumptionReceipt(
                outcome="run_accepted",
                queued_submission=queued.to_resource(),
                queue_version=expected_queue_version + 1,
                run=replay,
            )
        await self._inline_hooks.validate_queued_destination(
            tenant_id=run.tenant_id,
            queued_submission_id=queued_submission_id,
            submission_digest_sha256=submission_digest_sha256,
        )
        candidate_payload = await self._verify_input_payload(run)
        _validate_queued_run_input(run, candidate_payload, accepted_input)
        await self._verify_retry_payload(run, candidate_payload)
        await self._publish_initial(run, state)
        now = self._clock()
        try:
            async with transaction(self._sessions) as database:
                if final_validator is not None:
                    await final_validator(database)
                thread = await _lock_thread(database, run)
                _require_thread_precondition(
                    thread,
                    expected_version=expected_thread_version,
                    expected_current_run_id=expected_current_run_id,
                    expected_head_run_id=expected_head_run_id,
                )
                if thread.queue_version != expected_queue_version:
                    raise RunAcceptanceError("queue_version_conflict", "Thread queue generation changed")
                current = await _load_run(database, run.tenant_id, thread.current_run_id)
                await _require_queue_drain_state(database, thread, current)
                await _validate_advancement(
                    database,
                    thread,
                    current,
                    run,
                    candidate_payload=candidate_payload,
                    next_head_run_id=next_head_run_id,
                )
                session_record_value = await _require_session(database, run)
                run_record_value = await add_run_with_environment(
                    database,
                    run=run,
                    state=state,
                    workspace_id=session_record_value.workspace_id,
                    choice=await queued_environment_choice(database, queued_submission_id),
                )
                await database.flush()
                await bind_unbound_async_entries(
                    database,
                    tenant_id=run.tenant_id,
                    thread_id=thread.id,
                    target_run_id=run.id,
                    now=now,
                )
                try:
                    consumed = await consume_first_submission(
                        database,
                        tenant_id=run.tenant_id,
                        thread_id=thread.id,
                        queued_submission_id=queued_submission_id,
                        submission_digest_sha256=submission_digest_sha256,
                        authority_principal=run.authority_principal,
                        consumed_run_id=run.id,
                        now=now,
                    )
                except QueueConsumptionConflict as error:
                    raise RunAcceptanceError(
                        "queue_consumption_conflict",
                        "Queued submission changed before Run acceptance",
                    ) from error
                thread.version += 1
                thread.queue_version += 1
                thread.current_run_id = run.id
                if current is not None:
                    await schedule_environment_maintenance(database, run=current, now=self._clock())
                thread.head_run_id = next_head_run_id
                thread.updated_at = now
                await database.flush()
                queued_hook = consumed.to_resource().submission.hook_subscription
                await self._inline_hooks.authorize(
                    database,
                    run=run,
                    workspace_id=session_record_value.workspace_id,
                    subscription=queued_hook,
                )
                hook_subscription_id = await self._inline_hooks.create(
                    database,
                    run=run_record_value,
                    workspace_id=session_record_value.workspace_id,
                    subscription=queued_hook,
                    now=now,
                )
                await append_accepted_run_lifecycle(database, run_record_value)
                if consumed.consumed_run_id != run.id:
                    raise RuntimeError("queue consumption lost its accepted Run correlation")
                receipt = QueuedSubmissionConsumptionReceipt(
                    outcome="run_accepted",
                    queued_submission=consumed.to_resource(),
                    queue_version=thread.queue_version,
                    run=RunAcceptanceReceipt(
                        session_id=thread.session_id,
                        thread_id=thread.id,
                        thread_version=thread.version,
                        run_id=run.id,
                        run_version=run.version,
                        hook_subscription_id=hook_subscription_id,
                    ),
                )
        except IntegrityError as error:
            replay = await self._load_replay(run, state, accepted_thread_version=accepted_thread_version)
            if replay is not None:
                queued = await self._validate_queue_replay(
                    run=run,
                    queued_submission_id=queued_submission_id,
                    submission_digest_sha256=submission_digest_sha256,
                )
                return QueuedSubmissionConsumptionReceipt(
                    outcome="run_accepted",
                    queued_submission=queued.to_resource(),
                    queue_version=expected_queue_version + 1,
                    run=replay,
                )
            raise RunAcceptanceError(
                "run_acceptance_conflict", "Queue consumption lost a concurrent mutation"
            ) from error
        return receipt

    async def fail_queued_permanently(
        self,
        *,
        tenant_id: str,
        thread_id: str,
        queued_submission_id: str,
        submission_digest_sha256: str,
        failure: QueuedSubmissionFailure,
        expected_thread_version: int,
        expected_queue_version: int,
        expected_current_run_id: str | None,
        expected_head_run_id: str | None,
    ) -> QueuedSubmissionConsumptionReceipt:
        """Record locked permanent invalidity without accepting a Run."""

        now = self._clock()
        async with transaction(self._sessions) as database:
            thread = await _lock_thread_by_id(database, tenant_id=tenant_id, thread_id=thread_id)
            _require_thread_precondition(
                thread,
                expected_version=expected_thread_version,
                expected_current_run_id=expected_current_run_id,
                expected_head_run_id=expected_head_run_id,
            )
            if thread.queue_version != expected_queue_version:
                raise RunAcceptanceError("queue_version_conflict", "Thread queue generation changed")
            current = await _load_run(database, tenant_id, thread.current_run_id)
            await _require_queue_drain_state(database, thread, current)
            try:
                failed = await fail_first_submission(
                    database,
                    tenant_id=tenant_id,
                    thread_id=thread_id,
                    queued_submission_id=queued_submission_id,
                    submission_digest_sha256=submission_digest_sha256,
                    failure=failure,
                    now=now,
                )
            except QueueConsumptionConflict as error:
                raise RunAcceptanceError(
                    "queue_consumption_conflict",
                    "Queued submission changed before terminal failure",
                ) from error
            thread.queue_version += 1
            thread.updated_at = now
            await database.flush()
            return QueuedSubmissionConsumptionReceipt(
                outcome="submission_failed",
                queued_submission=failed.to_resource(),
                queue_version=thread.queue_version,
            )

    async def _load_replay(
        self,
        run: Run,
        state: RunStateEnvelope,
        *,
        accepted_thread_version: int,
    ) -> RunAcceptanceReceipt | None:
        if run.idempotency_key is None:
            return None
        async with short_session(self._sessions) as database:
            record = await database.scalar(
                select(RunRecord).where(
                    RunRecord.tenant_id == run.tenant_id,
                    RunRecord.idempotency_key == run.idempotency_key,
                )
            )
            if record is None:
                return None
            return await _validate_replay(database, record, run, accepted_thread_version=accepted_thread_version)

    async def _publish_initial(self, run: Run, state: RunStateEnvelope) -> None:
        try:
            await self._states.create(run.tenant_id, state)
        except StaleStateWriter:
            existing = await self._states.read(run.tenant_id, run.id, expected_thread_id=run.thread_id)
            if existing.envelope != state:
                raise RunAcceptanceError(
                    "run_state_conflict",
                    "Run state key already contains different accepted state",
                ) from None

    async def _verify_input_payload(self, run: Run) -> RunPayloadEnvelope | None:
        if run.input_object is None:
            return None
        return await self._payloads.verify_reference(run.tenant_id, run.id, "input", run.input_object)

    async def _verify_retry_payload(
        self,
        run: Run,
        candidate_payload: RunPayloadEnvelope | None,
    ) -> None:
        if run.retry_of_run_id is None:
            return
        async with short_session(self._sessions) as database:
            source = (await _load_run(database, run.tenant_id, run.retry_of_run_id)).to_resource()
        if source.thread_id != run.thread_id or source.status not in {RunStatus.failed, RunStatus.cancelled}:
            raise RunAcceptanceError("run_retry_conflict", "Retry source is not an eligible same-Thread terminal Run")
        if source.input_object is None:
            if candidate_payload is not None:
                raise RunAcceptanceError("run_retry_invalid", "Retry must preserve its input representation")
            return
        if candidate_payload is None:
            raise RunAcceptanceError("run_retry_invalid", "Retry must preserve its input representation")
        source_payload = await self._payloads.verify_reference(
            source.tenant_id,
            source.id,
            "input",
            source.input_object,
        )
        if (
            source_payload.payload_schema_version,
            source_payload.payload,
        ) != (
            candidate_payload.payload_schema_version,
            candidate_payload.payload,
        ):
            raise RunAcceptanceError("run_retry_invalid", "Retry must copy the terminal Run's exact accepted input")

    async def _reconcile_conflict(
        self,
        run: Run,
        state: RunStateEnvelope,
        error: IntegrityError,
        *,
        accepted_thread_version: int,
    ) -> RunAcceptanceReceipt:
        replay = await self._load_replay(run, state, accepted_thread_version=accepted_thread_version)
        if replay is not None:
            return replay
        raise RunAcceptanceError("run_acceptance_conflict", "Run acceptance lost a concurrent mutation") from error

    async def _require_inline_hook_replay(
        self,
        run: Run,
        expected: InlineHookSubscriptionInput | None,
    ) -> None:
        async with short_session(self._sessions) as database:
            record = await database.get(RunRecord, run.id)
            if record is None or not await self._inline_hooks.replay_matches(database, run=record, expected=expected):
                raise RunAcceptanceError(
                    "run_idempotency_conflict",
                    "Idempotency key was reused with different inline Hook configuration",
                )

    async def _validate_queue_replay(
        self,
        *,
        run: Run,
        queued_submission_id: str,
        submission_digest_sha256: str,
    ) -> QueuedSubmissionRecord:
        async with short_session(self._sessions) as database:
            queued = await database.scalar(
                select(QueuedSubmissionRecord).where(
                    QueuedSubmissionRecord.tenant_id == run.tenant_id,
                    QueuedSubmissionRecord.id == queued_submission_id,
                )
            )
            if (
                queued is None
                or queued.thread_id != run.thread_id
                or queued.submission_digest_sha256 != submission_digest_sha256
                or queued.consumed_run_id != run.id
            ):
                raise RunAcceptanceError(
                    "queue_consumption_replay_conflict",
                    "Accepted Run does not match the queued-submission replay",
                )
            return queued


def validate_prepared_run(run: Run, state: RunStateEnvelope) -> None:
    if run.status is not RunStatus.accepted or run.version != 1:
        raise ValueError("prepared acceptance requires a version-one accepted Run")
    if state.checkpoint_kind != "initial" or state.checkpoint_seq != 0:
        raise ValueError("prepared acceptance requires initial Run state")
    validate_run_state_selection(run, state)


def validate_run_state_selection(run: Run, state: RunStateEnvelope) -> None:
    """Validate immutable Run selection facts against any retained checkpoint."""

    if (state.run_id, state.thread_id) != (run.id, run.thread_id):
        raise ValueError("Run and state identities do not match")
    if (state.agent_id, state.agent_revision_id) != (run.agent_id, run.agent_revision_id):
        raise ValueError("Run and state Agent selection do not match")
    effective = state.effective_agent_config
    effective_payload = effective.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
    if canonical_digest(effective_payload) != effective.content_digest:
        raise ValueError("Run effective configuration digest is invalid")
    if effective.content_digest != run.effective_agent_config_digest:
        raise ValueError("Run effective configuration digest does not match state")
    if effective.resolved_model.execution.observation() != run.model_execution_observation:
        raise ValueError("Run model observation does not match state")
    if state.runtime_lock_digest != run.runtime_lock_digest:
        raise ValueError("Run Runtime lock does not match state")


def _validate_new_thread(thread: Thread, run: Run, session: Session | None) -> None:
    _validate_new_thread_identity(thread, run)
    _validate_new_thread_session(thread, run, session)
    _validate_new_thread_origin(thread, run)


def _validate_new_thread_identity(thread: Thread, run: Run) -> None:
    if thread.version != 1 or thread.queue_version != 0 or thread.head_run_id is not None:
        raise ValueError("new Thread must start at version one with an empty head and queue")
    if thread.current_run_id != run.id:
        raise ValueError("new Thread must select its first Run")
    if (thread.tenant_id, thread.session_id, thread.id) != (run.tenant_id, run.session_id, run.thread_id):
        raise ValueError("new Thread and first Run scope do not match")
    if run.retry_of_run_id is not None:
        raise ValueError("the first Run of a new Thread cannot retry another Run")


def _validate_new_thread_session(thread: Thread, run: Run, session: Session | None) -> None:
    if session is not None and (session.id, session.tenant_id) != (thread.session_id, thread.tenant_id):
        raise ValueError("new Session and root Thread scope do not match")
    if session is not None and thread.role is not ThreadRole.root:
        raise ValueError("a new Session must begin with its root Thread")
    if session is None and thread.role is not ThreadRole.child:
        raise ValueError("an existing Session can accept only a child Thread")


def _validate_new_thread_origin(thread: Thread, run: Run) -> None:
    if thread.origin_kind is ThreadOriginKind.fork and run.lineage_kind is not RunLineageKind.fork:
        raise ValueError("fork Thread requires fork Run lineage")
    if thread.origin_kind is ThreadOriginKind.child and run.lineage_kind is not RunLineageKind.root:
        raise ValueError("independent child Thread requires root Run lineage")
    if thread.origin_kind is ThreadOriginKind.new and run.lineage_kind is not RunLineageKind.root:
        raise ValueError("new root Thread requires root Run lineage")


async def _require_session(database: AsyncSession, run: Run) -> SessionRecord:
    record = await database.scalar(
        select(SessionRecord).where(SessionRecord.id == run.session_id, SessionRecord.tenant_id == run.tenant_id)
    )
    if record is None:
        raise RunAcceptanceError("session_not_found", "The interaction Session was not found")
    return record


async def _require_origin(database: AsyncSession, thread: Thread, run: Run) -> None:
    if thread.origin_run_id != run.parent_run_id and thread.origin_kind is ThreadOriginKind.fork:
        raise ValueError("fork Thread origin Run must equal its first Run parent")
    origin = await _load_run(database, thread.tenant_id, thread.origin_run_id)
    if origin.thread_id != thread.origin_thread_id:
        raise RunAcceptanceError("thread_origin_invalid", "Thread origin Run does not belong to its origin Thread")
    if thread.role is ThreadRole.child and origin.session_id != thread.session_id:
        raise RunAcceptanceError("thread_origin_invalid", "Child Thread origin must belong to the same Session")
    if thread.role is ThreadRole.root and origin.session_id == thread.session_id:
        raise RunAcceptanceError("thread_origin_invalid", "Session fork origin must belong to another Session")
    if thread.origin_kind is ThreadOriginKind.fork and origin.status != RunStatus.completed.value:
        raise RunAcceptanceError("thread_origin_invalid", "Thread fork source must be completed")


async def _lock_thread(database: AsyncSession, run: Run) -> ThreadRecord:
    record = await _lock_thread_by_id(database, tenant_id=run.tenant_id, thread_id=run.thread_id)
    if record.session_id != run.session_id:
        raise RunAcceptanceError("thread_not_found", "The interaction Thread was not found")
    return record


async def _lock_thread_by_id(
    database: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
) -> ThreadRecord:
    record = await database.scalar(
        select(ThreadRecord).where(ThreadRecord.tenant_id == tenant_id, ThreadRecord.id == thread_id).with_for_update()
    )
    if record is None:
        raise RunAcceptanceError("thread_not_found", "The interaction Thread was not found")
    return record


async def _require_queue_drain_state(
    database: AsyncSession,
    thread: ThreadRecord,
    current: RunRecord,
) -> None:
    if current.status not in {
        RunStatus.completed.value,
        RunStatus.failed.value,
        RunStatus.cancelled.value,
    }:
        raise RunAcceptanceError("thread_busy", "Thread is not eligible for queue consumption")
    if thread.head_run_id is None:
        if current.status == RunStatus.completed.value:
            raise RunAcceptanceError("thread_head_invalid", "Completed Thread has no selected head")
        return
    head = await _load_run(database, current.tenant_id, thread.head_run_id)
    if head.thread_id != thread.id or head.status != RunStatus.completed.value:
        raise RunAcceptanceError("thread_head_invalid", "Queue consumption requires a completed selected head")


def _require_thread_precondition(
    thread: ThreadRecord,
    *,
    expected_version: int,
    expected_current_run_id: str | None,
    expected_head_run_id: str | None,
) -> None:
    if (
        thread.version != expected_version
        or thread.current_run_id != expected_current_run_id
        or thread.head_run_id != expected_head_run_id
    ):
        raise RunAcceptanceError("thread_version_conflict", "Thread advancement precondition changed")


async def _validate_advancement(
    database: AsyncSession,
    thread: ThreadRecord,
    current: RunRecord | None,
    run: Run,
    *,
    candidate_payload: RunPayloadEnvelope | None,
    next_head_run_id: str | None,
) -> None:
    if current is None:
        if (
            thread.current_run_id is not None
            or thread.head_run_id is not None
            or run.parent_run_id is not None
            or run.retry_of_run_id is not None
            or next_head_run_id is not None
        ):
            raise RunAcceptanceError("thread_head_invalid", "First Run must use empty root state")
        return
    required_head = run.parent_run_id if run.lineage_kind is RunLineageKind.continue_ else thread.head_run_id
    if next_head_run_id != required_head:
        raise RunAcceptanceError("thread_head_invalid", "Thread advancement selected an unrelated continuation head")
    if run.retry_of_run_id is not None:
        _validate_retry_advancement(current, run)
    elif run.parent_run_id is None:
        _validate_root_advancement(thread, current, run)
    else:
        await _validate_parent_advancement(database, thread, run, candidate_payload)
    if next_head_run_id is not None:
        await _validate_selected_head(database, thread, run.tenant_id, next_head_run_id)


def _validate_retry_advancement(current: RunRecord, run: Run) -> None:
    if run.retry_of_run_id != current.id or current.status not in {
        RunStatus.failed.value,
        RunStatus.cancelled.value,
    }:
        raise RunAcceptanceError("run_retry_conflict", "Retry source is not the current failed or cancelled Run")
    _validate_retry_copy(current.to_resource(), run)


def _validate_root_advancement(thread: ThreadRecord, current: RunRecord, run: Run) -> None:
    if run.lineage_kind is not RunLineageKind.root or thread.head_run_id is not None:
        raise RunAcceptanceError("run_lineage_invalid", "Root-like advancement requires a null Thread head")
    if current.status not in {RunStatus.failed.value, RunStatus.cancelled.value}:
        raise RunAcceptanceError("run_lineage_invalid", "Root-like advancement requires terminal current work")


async def _validate_parent_advancement(
    database: AsyncSession,
    thread: ThreadRecord,
    run: Run,
    candidate_payload: RunPayloadEnvelope | None,
) -> None:
    assert run.parent_run_id is not None
    parent = await _load_run(database, run.tenant_id, run.parent_run_id)
    if run.lineage_kind is RunLineageKind.fork:
        raise RunAcceptanceError("run_lineage_invalid", "Fork lineage can only be the first Run of a new Thread")
    expected_status = (
        RunStatus.waiting.value
        if run.input_kind in {RunInputKind.waiting_feedback, RunInputKind.waiting_continue}
        else RunStatus.completed.value
    )
    if parent.thread_id != thread.id or parent.status != expected_status:
        raise RunAcceptanceError("run_lineage_invalid", "Continuation parent is not an eligible same-Thread state")
    if expected_status == RunStatus.waiting.value:
        _validate_inherited_execution(parent.to_resource(), run)
        _validate_waiting_input(parent, run, candidate_payload)


async def _validate_selected_head(
    database: AsyncSession,
    thread: ThreadRecord,
    tenant_id: str,
    head_run_id: str,
) -> None:
    head = await _load_run(database, tenant_id, head_run_id)
    if head.thread_id != thread.id or head.status not in {RunStatus.waiting.value, RunStatus.completed.value}:
        raise RunAcceptanceError("thread_head_invalid", "Selected Thread head is not a sealed continuation state")


def _validate_retry_copy(source: Run, candidate: Run) -> None:
    _validate_inherited_execution(source, candidate)
    object_backed = source.input_object is not None
    immutable_intent = (
        source.parent_run_id,
        source.lineage_kind,
        source.input_kind,
        object_backed,
        source.input_text,
        None if object_backed else source.input,
    )
    candidate_intent = (
        candidate.parent_run_id,
        candidate.lineage_kind,
        candidate.input_kind,
        candidate.input_object is not None,
        candidate.input_text,
        None if object_backed else candidate.input,
    )
    if candidate_intent != immutable_intent:
        raise RunAcceptanceError("run_retry_invalid", "Retry must copy the terminal Run's exact accepted intent")


def _validate_inherited_execution(source: Run, candidate: Run) -> None:
    source_authority = (
        source.authority_principal,
        source.agent_id,
        source.agent_revision_id,
        source.effective_agent_config_digest,
        source.encrypted_config_payload,
        source.runtime_lock_digest,
        source.model_execution_observation,
        source.connector_connection_selections,
        source.mcp_connection_selections,
        source.native_tool_contexts,
    )
    candidate_authority = (
        candidate.authority_principal,
        candidate.agent_id,
        candidate.agent_revision_id,
        candidate.effective_agent_config_digest,
        candidate.encrypted_config_payload,
        candidate.runtime_lock_digest,
        candidate.model_execution_observation,
        candidate.connector_connection_selections,
        candidate.mcp_connection_selections,
        candidate.native_tool_contexts,
    )
    if candidate_authority != source_authority:
        raise RunAcceptanceError(
            "run_inherited_authority_invalid",
            "Run must preserve its source's accepted execution authority",
        )


def _validate_waiting_input(
    parent: RunRecord,
    candidate: Run,
    payload: RunPayloadEnvelope | None,
) -> None:
    raw = candidate.input if payload is None else payload.payload
    try:
        if candidate.input_kind is RunInputKind.waiting_feedback:
            accepted = WaitingRunFeedback.model_validate(raw)
        elif candidate.input_kind is RunInputKind.waiting_continue:
            accepted = WaitingRunContinueInput.model_validate(raw)
        else:
            raise RunAcceptanceError(
                "run_input_invalid",
                "Waiting continuation requires feedback or composite Continue input",
            )
    except ValidationError as error:
        raise RunAcceptanceError("run_input_invalid", "Waiting continuation input is invalid") from error
    if accepted.waiting_run_id != parent.id or accepted.sealed_state_digest_sha256 != parent.sealed_state_digest_sha256:
        raise RunAcceptanceError("run_waiting_state_conflict", "Waiting continuation state changed")
    pending = parent.to_resource().pending
    if pending is None:
        raise RunAcceptanceError("run_waiting_state_invalid", "Waiting Run has no pending summary")
    expected = tuple((call.call_id, call.kind) for call in pending.calls)
    actual = tuple((resolution.call_id, resolution.kind) for resolution in accepted.resolutions)
    if actual != expected:
        raise RunAcceptanceError(
            "run_feedback_invalid",
            "Waiting continuation does not exactly cover the frozen pending set",
        )


async def _load_run(database: AsyncSession, tenant_id: str, run_id: str | None) -> RunRecord:
    if run_id is None:
        raise RunAcceptanceError("run_not_found", "The interaction Run was not found")
    record = await database.scalar(select(RunRecord).where(RunRecord.tenant_id == tenant_id, RunRecord.id == run_id))
    if record is None:
        raise RunAcceptanceError("run_not_found", "The interaction Run was not found")
    return record


async def _validate_replay(
    database: AsyncSession,
    record: RunRecord,
    run: Run,
    *,
    accepted_thread_version: int,
) -> RunAcceptanceReceipt:
    if record.request_fingerprint != run.request_fingerprint:
        raise RunAcceptanceError("run_idempotency_conflict", "Idempotency key was reused with different Run intent")
    thread = await database.scalar(
        select(ThreadRecord).where(ThreadRecord.tenant_id == record.tenant_id, ThreadRecord.id == record.thread_id)
    )
    if thread is None:
        raise RunAcceptanceError("run_acceptance_corrupt", "Accepted Run has no owning Thread")
    persisted_hook = await load_inline_hook_subscription(
        database,
        organization_id=record.tenant_id,
        run_id=record.id,
    )
    return RunAcceptanceReceipt(
        session_id=record.session_id,
        thread_id=record.thread_id,
        thread_version=accepted_thread_version,
        run_id=record.id,
        run_version=1,
        hook_subscription_id=None if persisted_hook is None else persisted_hook[0].id,
    )


def _validate_queued_run_input(
    run: Run,
    payload: RunPayloadEnvelope | None,
    accepted_input: AcceptedAgentInput,
) -> None:
    if run.input_kind is not RunInputKind.agent_input or run.retry_of_run_id is not None:
        raise ValueError("queue consumption requires ordinary non-retry Agent input")
    expected = accepted_input.model_dump(mode="json", by_alias=True, exclude_none=True)
    actual = run.input if payload is None else payload.payload
    if actual != expected:
        raise ValueError("prepared queued Run input does not match its accepted submission")


def _receipt(
    thread: Thread,
    run: Run,
    *,
    hook_subscription_id: str | None,
) -> RunAcceptanceReceipt:
    return RunAcceptanceReceipt(
        session_id=thread.session_id,
        thread_id=thread.id,
        thread_version=thread.version,
        run_id=run.id,
        run_version=run.version,
        hook_subscription_id=hook_subscription_id,
    )


__all__ = [
    "RunAcceptanceError",
    "RunAcceptanceReceipt",
    "RunAcceptanceService",
    "validate_prepared_run",
    "validate_run_state_selection",
]
