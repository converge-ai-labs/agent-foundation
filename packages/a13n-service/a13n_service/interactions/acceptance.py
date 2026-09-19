"""State-first relational acceptance for prepared durable Runs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.environments.devices import DeviceDiscovery
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.websocket.admission import OnlineAdmission, OnlineEvidence
from a13n_service.environments.websocket.coordination import ConnectionCoordination
from a13n_service.hooks import InlineHookValidator
from a13n_service.hooks.domain import InlineHookSubscriptionInput
from a13n_service.hooks.persistence import load_inline_hook_subscription
from a13n_service.iam import PrincipalRef
from a13n_service.interactions.environment_acceptance import add_run_with_environment
from a13n_service.interactions.environment_selection import (
    EnvironmentDefault,
    EnvironmentIntent,
    queued_environment_choice,
    requested_environment,
)
from a13n_service.labels import merge_labels
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, utc_now

from .acceptance_validation import (
    validate_inherited_execution,
    validate_new_thread,
    validate_prepared_run,
    validate_queued_run_input,
    validate_retry_copy,
    validate_waiting_input,
)
from .control_domain import (
    QueuedSubmissionConsumptionReceipt,
    QueuedSubmissionFailure,
    RunAcceptanceReceipt,
)
from .control_models import QueuedSubmissionRecord
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
from .lifecycle import LifecycleWriter
from .models import RunRecord, SessionRecord, ThreadRecord
from .objects import RunPayloadStore, RunStateStore, StaleStateWriter
from .ports.memory import ExecutionBindings
from .queue_persistence import QueueConsumptionConflict, consume_first_submission, fail_first_submission
from .records import session_record, thread_record
from .state import RunCheckpoint, RunPayloadEnvelope


class RunAcceptanceService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        states: RunStateStore,
        payloads: RunPayloadStore,
        inline_hooks: InlineHookValidator,
        *,
        lifecycle: LifecycleWriter,
        bindings: ExecutionBindings,
        coordination: ConnectionCoordination | None = None,
        devices: DeviceDiscovery | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._bindings = bindings
        self._sessions = sessions
        self._online = OnlineAdmission(sessions, coordination, devices=devices)
        self._states = states
        self._payloads = payloads
        self._inline_hooks = InlineHookAcceptance(sessions, inline_hooks)
        self._clock = clock or utc_now

    async def validate_in_session(self, session: AsyncSession, run_id: str) -> None:
        await self._bindings.validate(session, run_id)

    async def validate_retained(self, run_id: str) -> None:
        async with short_session(self._sessions) as session:
            await self._bindings.validate(session, run_id)

    async def accept_new_thread(
        self,
        *,
        session: Session | None,
        thread: Thread,
        run: Run,
        state: RunCheckpoint,
        hook_subscription: InlineHookSubscriptionInput | None = None,
        final_validator: Callable[[AsyncSession], Awaitable[None]] | None = None,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        environment: EnvironmentIntent = EnvironmentDefault.agent,
        thread_label_overrides: Mapping[str, str] | None = None,
        run_label_overrides: Mapping[str, str] | None = None,
    ) -> RunAcceptanceReceipt:
        validate_prepared_run(run, state)
        validate_new_thread(thread, run, session)
        replay = await self._load_replay(run, state, accepted_thread_version=1)
        if replay is not None:
            await self._require_inline_hook_replay(run, hook_subscription)
            return replay
        await self._inline_hooks.validate_destination(hook_subscription)
        await self._verify_input_payload(run)
        await self._publish_initial(run, state)

        async def accept(database: AsyncSession, online: OnlineEvidence) -> RunAcceptanceReceipt:
            if final_validator is not None:
                await final_validator(database)
            if session is None:
                session_record_value = await require_session(database, run)
                workspace_id = session_record_value.workspace_id
                session_labels = session_record_value.labels
            else:
                database.add(session_record(session))
                workspace_id = session.workspace_id
                session_labels = session.labels
            await self._inline_hooks.authorize(
                database,
                run=run,
                workspace_id=workspace_id,
                subscription=hook_subscription,
            )
            if thread.origin_kind is not ThreadOriginKind.new:
                await _require_origin(database, thread, run)
            if thread.origin_kind is ThreadOriginKind.fork:
                assert thread.origin_thread_id is not None
                label_parent = await _lock_thread_by_id(
                    database,
                    organization_id=thread.organization_id,
                    thread_id=thread.origin_thread_id,
                )
                parent_labels = label_parent.labels
            else:
                parent_labels = session_labels
            accepted_thread_labels = _accepted_labels(parent_labels, thread_label_overrides)
            accepted_run = run.model_copy(
                update={"labels": _accepted_labels(accepted_thread_labels, run_label_overrides)}
            )
            accepted_thread = thread.model_copy(update={"labels": accepted_thread_labels})
            database.add(thread_record(accepted_thread))
            run_record_value = await add_run_with_environment(
                database,
                online=online,
                run=accepted_run,
                state=state,
                workspace_id=workspace_id,
                intent=environment,
            )
            hook_subscription_id = await self._inline_hooks.create(
                database,
                run=run_record_value,
                workspace_id=workspace_id,
                subscription=hook_subscription,
                now=self._clock(),
            )
            await self._lifecycle.append_accepted_run_lifecycle(database, run_record_value)
            receipt = _receipt(thread, run, hook_subscription_id=hook_subscription_id)
            if transaction_hook is not None:
                await transaction_hook(database, receipt)
            await self._bindings.finalize(database, accepted_run, source_run_id=binding_source(run))
            return receipt

        try:
            receipt = await self._online.commit(accept)
        except (IntegrityError, EnvironmentManagementError, RunAcceptanceError) as error:
            return await self._reconcile_acceptance_error(run, state, error, accepted_thread_version=1)
        return receipt

    async def advance_thread(
        self,
        *,
        run: Run,
        state: RunCheckpoint,
        expected_thread_version: int,
        expected_current_run_id: str | None,
        expected_head_run_id: str | None,
        next_head_run_id: str | None,
        hook_subscription: InlineHookSubscriptionInput | None = None,
        hook_source_run_id: str | None = None,
        hook_actor: PrincipalRef | None = None,
        final_validator: Callable[[AsyncSession], Awaitable[None]] | None = None,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        environment: EnvironmentIntent = EnvironmentDefault.thread,
        label_overrides: Mapping[str, str] | None = None,
        label_source_run_id: str | None = None,
    ) -> RunAcceptanceReceipt:
        validate_prepared_run(run, state)
        accepted_thread_version = expected_thread_version + 1
        replay = await self._load_replay(run, state, accepted_thread_version=accepted_thread_version)
        if replay is not None:
            if hook_source_run_id is None:
                await self._require_inline_hook_replay(run, hook_subscription)
            return replay
        hook_subscription = await self._inline_hooks.prepare(
            run=run,
            subscription=hook_subscription,
            source_run_id=hook_source_run_id,
        )
        candidate_payload = await self._verify_input_payload(run)
        await self._verify_retry_payload(run, candidate_payload)
        await self._publish_initial(run, state)

        async def accept(database: AsyncSession, online: OnlineEvidence) -> RunAcceptanceReceipt:
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
                await _load_run(database, run.organization_id, thread.current_run_id) if thread.current_run_id else None
            )
            if current is not None and current.status in {RunStatus.accepted.value, RunStatus.running.value}:
                raise RunAcceptanceError("thread_busy", "Thread already has active Run work")
            await validate_advancement(
                database,
                thread,
                current,
                run,
                candidate_payload=candidate_payload,
                next_head_run_id=next_head_run_id,
            )
            session_record_value = await require_session(database, run)
            parent_labels = (
                (await _load_run(database, run.organization_id, label_source_run_id)).labels
                if label_source_run_id is not None
                else thread.labels
            )
            accepted_run = run.model_copy(update={"labels": _accepted_labels(parent_labels, label_overrides)})
            await self._inline_hooks.authorize(
                database,
                run=accepted_run,
                workspace_id=session_record_value.workspace_id,
                subscription=hook_subscription,
                source_run_id=hook_source_run_id,
                actor=hook_actor,
            )
            run_record_value = await add_run_with_environment(
                database,
                online=online,
                run=accepted_run,
                state=state,
                workspace_id=session_record_value.workspace_id,
                intent=environment,
            )
            if run.input_kind in {RunInputKind.waiting_feedback, RunInputKind.waiting_continue}:
                await database.flush()
                assert run.parent_run_id is not None
                await bind_waiting_entries(
                    database,
                    thread=thread,
                    source_waiting_run_id=run.parent_run_id,
                    target_run_id=run.id,
                    now=self._clock(),
                )
            elif thread.head_run_id is not None and thread.head_run_id != next_head_run_id:
                prior_head = await _load_run(database, run.organization_id, thread.head_run_id)
                if prior_head.status == RunStatus.waiting.value:
                    await abandon_waiting_entries(
                        database,
                        thread=thread,
                        source_waiting_run_id=prior_head.id,
                        now=self._clock(),
                    )
            thread.version += 1
            thread.current_run_id = run.id
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
            await self._lifecycle.append_accepted_run_lifecycle(database, run_record_value)
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
            await self._bindings.finalize(database, accepted_run, source_run_id=binding_source(run))
            return receipt

        try:
            receipt = await self._online.commit(accept)
        except (IntegrityError, EnvironmentManagementError, RunAcceptanceError) as error:
            return await self._reconcile_acceptance_error(
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
        state: RunCheckpoint,
        queued_submission_id: str,
        submission_digest_sha256: str,
        accepted_input: AcceptedAgentInput,
        expected_thread_version: int,
        expected_queue_version: int,
        expected_current_run_id: str | None,
        expected_head_run_id: str | None,
        next_head_run_id: str | None,
        final_validator: Callable[[AsyncSession], Awaitable[None]] | None = None,
        transaction_hook: Callable[[AsyncSession, QueuedSubmissionConsumptionReceipt], Awaitable[None]] | None = None,
        label_overrides: Mapping[str, str] | None = None,
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
            organization_id=run.organization_id,
            queued_submission_id=queued_submission_id,
            submission_digest_sha256=submission_digest_sha256,
        )
        candidate_payload = await self._verify_input_payload(run)
        validate_queued_run_input(run, candidate_payload, accepted_input)
        await self._publish_initial(run, state)
        now = self._clock()

        async def accept(database: AsyncSession, online: OnlineEvidence) -> QueuedSubmissionConsumptionReceipt:
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
                raise RunAcceptanceError("queue_version_conflict", "Thread queue version changed")
            current = await _load_run(database, run.organization_id, thread.current_run_id)
            await _require_queue_drain_state(database, thread, current)
            await validate_advancement(
                database,
                thread,
                current,
                run,
                candidate_payload=candidate_payload,
                next_head_run_id=next_head_run_id,
            )
            session_record_value = await require_session(database, run)
            accepted_run = run.model_copy(update={"labels": _accepted_labels(thread.labels, label_overrides)})
            choice = await queued_environment_choice(database, queued_submission_id)
            run_record_value = await add_run_with_environment(
                database,
                online=online,
                run=accepted_run,
                state=state,
                workspace_id=session_record_value.workspace_id,
                intent=requested_environment(choice, default=EnvironmentDefault.thread),
            )
            await database.flush()
            await bind_unbound_async_entries(
                database,
                thread=thread,
                target_run_id=run.id,
                now=now,
            )
            try:
                consumed = await consume_first_submission(
                    database,
                    organization_id=run.organization_id,
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
            await self._lifecycle.append_accepted_run_lifecycle(database, run_record_value)
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
            if transaction_hook is not None:
                await transaction_hook(database, receipt)
            await self._bindings.finalize(database, accepted_run, source_run_id=binding_source(run))
            return receipt

        try:
            receipt = await self._online.commit(accept)
        except (IntegrityError, EnvironmentManagementError, RunAcceptanceError) as error:
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
            if not isinstance(error, IntegrityError):
                raise
            raise RunAcceptanceError(
                "run_acceptance_conflict", "Queue consumption lost a concurrent mutation"
            ) from error
        return receipt

    async def fail_queued_permanently(
        self,
        *,
        organization_id: str,
        thread_id: str,
        queued_submission_id: str,
        submission_digest_sha256: str,
        failure: QueuedSubmissionFailure,
        expected_thread_version: int,
        expected_queue_version: int,
        expected_current_run_id: str | None,
        expected_head_run_id: str | None,
        revalidate: Callable[[AsyncSession], Awaitable[bool]] | None = None,
    ) -> QueuedSubmissionConsumptionReceipt:
        """Record locked permanent invalidity without accepting a Run."""

        now = self._clock()
        async with transaction(self._sessions) as database:
            thread = await _lock_thread_by_id(database, organization_id=organization_id, thread_id=thread_id)
            _require_thread_precondition(
                thread,
                expected_version=expected_thread_version,
                expected_current_run_id=expected_current_run_id,
                expected_head_run_id=expected_head_run_id,
            )
            if thread.queue_version != expected_queue_version:
                raise RunAcceptanceError("queue_version_conflict", "Thread queue version changed")
            current = await _load_run(database, organization_id, thread.current_run_id)
            await _require_queue_drain_state(database, thread, current)
            if revalidate is not None and not await revalidate(database):
                raise RunAcceptanceError("queue_failure_changed", "Queued intent is no longer permanently invalid")
            try:
                failed = await fail_first_submission(
                    database,
                    organization_id=organization_id,
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
        state: RunCheckpoint,
        *,
        accepted_thread_version: int,
    ) -> RunAcceptanceReceipt | None:
        if run.idempotency_key is None:
            return None
        async with short_session(self._sessions) as database:
            record = await database.scalar(
                select(RunRecord).where(
                    RunRecord.organization_id == run.organization_id,
                    RunRecord.idempotency_key == run.idempotency_key,
                )
            )
            if record is None:
                return None
            await self._bindings.validate(database, record.id)
            return await _validate_replay(database, record, run, accepted_thread_version=accepted_thread_version)

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

    async def _verify_input_payload(self, run: Run) -> RunPayloadEnvelope | None:
        if run.input_object is None:
            return None
        return await self._payloads.verify_reference(run.organization_id, run.id, "input", run.input_object)

    async def _verify_retry_payload(
        self,
        run: Run,
        candidate_payload: RunPayloadEnvelope | None,
    ) -> None:
        if run.retry_of_run_id is None:
            return
        async with short_session(self._sessions) as database:
            source = (await _load_run(database, run.organization_id, run.retry_of_run_id)).to_resource()
        if source.thread_id != run.thread_id or source.status not in {RunStatus.failed, RunStatus.cancelled}:
            raise RunAcceptanceError("run_retry_conflict", "Retry source is not an eligible same-Thread terminal Run")
        if source.input_object is None:
            if candidate_payload is not None:
                raise RunAcceptanceError("run_retry_invalid", "Retry must preserve its input representation")
            return
        if candidate_payload is None:
            raise RunAcceptanceError("run_retry_invalid", "Retry must preserve its input representation")
        source_payload = await self._payloads.verify_reference(
            source.organization_id,
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

    async def _reconcile_acceptance_error(
        self,
        run: Run,
        state: RunCheckpoint,
        error: IntegrityError | EnvironmentManagementError | RunAcceptanceError,
        *,
        accepted_thread_version: int,
    ) -> RunAcceptanceReceipt:
        replay = await self._load_replay(run, state, accepted_thread_version=accepted_thread_version)
        if replay is not None:
            return replay
        if not isinstance(error, IntegrityError):
            raise error
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
                    QueuedSubmissionRecord.organization_id == run.organization_id,
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


async def require_session(database: AsyncSession, run: Run) -> SessionRecord:
    record = await database.scalar(
        select(SessionRecord).where(
            SessionRecord.id == run.session_id, SessionRecord.organization_id == run.organization_id
        )
    )
    if record is None:
        raise RunAcceptanceError(
            "session_not_found", "The interaction Session was not found", category=ErrorCategory.not_found
        )
    if (record.configuration_owner_user_id is not None) != (run.configuration_context is not None):
        raise RunAcceptanceError(
            "configuration_scope_required", "Configuration scope cannot be added or removed by ordinary Run admission"
        )
    if run.configuration_context is not None:
        from a13n_service.agent_configuration.authorization import authorize_execution

        if (
            run.configuration_context.session_id != run.session_id
            or run.configuration_context.thread_id != run.thread_id
            or run.environment_id is not None
        ):
            raise RunAcceptanceError("configuration_scope_invalid", "The accepted configuration binding is invalid")
        await authorize_execution(
            database,
            principal=run.authority_principal,
            organization_id=run.organization_id,
            workspace_id=record.workspace_id,
            agent_id=run.agent_id,
            context=run.configuration_context,
        )
    return record


def _accepted_labels(parent: Mapping[str, str], overrides: Mapping[str, str] | None) -> dict[str, str]:
    try:
        return merge_labels(parent, overrides)
    except ValueError as error:
        raise RunAcceptanceError(
            "merged_labels_invalid",
            str(error),
            category=ErrorCategory.invalid_request,
        ) from error


async def _require_origin(database: AsyncSession, thread: Thread, run: Run) -> None:
    if thread.origin_run_id != run.parent_run_id and thread.origin_kind is ThreadOriginKind.fork:
        raise ValueError("fork Thread origin Run must equal its first Run parent")
    origin = await _load_run(database, thread.organization_id, thread.origin_run_id)
    if origin.thread_id != thread.origin_thread_id:
        raise RunAcceptanceError("thread_origin_invalid", "Thread origin Run does not belong to its origin Thread")
    if thread.role is ThreadRole.child and origin.session_id != thread.session_id:
        raise RunAcceptanceError("thread_origin_invalid", "Child Thread origin must belong to the same Session")
    if thread.role is ThreadRole.root and origin.session_id == thread.session_id:
        raise RunAcceptanceError("thread_origin_invalid", "Session fork origin must belong to another Session")
    if thread.origin_kind is ThreadOriginKind.fork and origin.status != RunStatus.completed.value:
        raise RunAcceptanceError("thread_origin_invalid", "Thread fork source must be completed")


async def _lock_thread(database: AsyncSession, run: Run) -> ThreadRecord:
    record = await _lock_thread_by_id(database, organization_id=run.organization_id, thread_id=run.thread_id)
    if record.session_id != run.session_id:
        raise RunAcceptanceError(
            "thread_not_found", "The interaction Thread was not found", category=ErrorCategory.not_found
        )
    return record


async def _lock_thread_by_id(
    database: AsyncSession,
    *,
    organization_id: str,
    thread_id: str,
) -> ThreadRecord:
    record = await database.scalar(
        select(ThreadRecord)
        .where(ThreadRecord.organization_id == organization_id, ThreadRecord.id == thread_id)
        .with_for_update()
    )
    if record is None:
        raise RunAcceptanceError(
            "thread_not_found", "The interaction Thread was not found", category=ErrorCategory.not_found
        )
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
    head = await _load_run(database, current.organization_id, thread.head_run_id)
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


async def validate_advancement(
    database: AsyncSession,
    thread: ThreadRecord,
    current: RunRecord | None,
    run: Run,
    *,
    candidate_payload: RunPayloadEnvelope | None,
    next_head_run_id: str | None,
) -> None:
    if current is None:
        if run.lineage_kind is RunLineageKind.fork:
            if (
                thread.current_run_id is not None
                or thread.head_run_id is not None
                or next_head_run_id is not None
                or thread.origin_kind != ThreadOriginKind.fork.value
                or run.parent_run_id != thread.origin_run_id
                or run.retry_of_run_id is not None
            ):
                raise RunAcceptanceError("thread_origin_invalid", "First fork Run must preserve the Thread origin")
            await _require_origin(database, thread.to_resource(), run)
            return
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
        await _validate_selected_head(database, thread, run.organization_id, next_head_run_id)


def _validate_retry_advancement(current: RunRecord, run: Run) -> None:
    if run.retry_of_run_id != current.id or current.status not in {
        RunStatus.failed.value,
        RunStatus.cancelled.value,
    }:
        raise RunAcceptanceError("run_retry_conflict", "Retry source is not the current failed or cancelled Run")
    validate_retry_copy(current.to_resource(), run)


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
    parent = await _load_run(database, run.organization_id, run.parent_run_id)
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
        validate_inherited_execution(parent.to_resource(), run)
        validate_waiting_input(parent, run, candidate_payload)


async def _validate_selected_head(
    database: AsyncSession,
    thread: ThreadRecord,
    organization_id: str,
    head_run_id: str,
) -> None:
    head = await _load_run(database, organization_id, head_run_id)
    if head.thread_id != thread.id or head.status not in {RunStatus.waiting.value, RunStatus.completed.value}:
        raise RunAcceptanceError("thread_head_invalid", "Selected Thread head is not a sealed continuation state")


async def _load_run(database: AsyncSession, organization_id: str, run_id: str | None) -> RunRecord:
    if run_id is None:
        raise RunAcceptanceError("run_not_found", "The interaction Run was not found", category=ErrorCategory.not_found)
    record = await database.scalar(
        select(RunRecord).where(RunRecord.organization_id == organization_id, RunRecord.id == run_id)
    )
    if record is None:
        raise RunAcceptanceError("run_not_found", "The interaction Run was not found", category=ErrorCategory.not_found)
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
        select(ThreadRecord).where(
            ThreadRecord.organization_id == record.organization_id, ThreadRecord.id == record.thread_id
        )
    )
    if thread is None:
        raise RunAcceptanceError("run_acceptance_corrupt", "Accepted Run has no owning Thread")
    persisted_hook = await load_inline_hook_subscription(
        database,
        organization_id=record.organization_id,
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
]


def binding_source(run: Run) -> str | None:
    """Only kernel-validated retained-execution lineage selects a binding source."""
    if run.retry_of_run_id is not None:
        return run.retry_of_run_id
    if (
        run.input_kind in {RunInputKind.waiting_feedback, RunInputKind.waiting_continue}
        or run.lineage_kind is RunLineageKind.fork
    ):
        return run.parent_run_id
    return None
