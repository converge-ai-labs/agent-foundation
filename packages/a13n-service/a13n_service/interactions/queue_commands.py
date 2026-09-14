"""Prepare and consume queued intent under its retained Principal."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.invocation_resolution import (
    AgentInvocationResolver,
)
from a13n_service.application_errors import ErrorCategory
from a13n_service.durable_operations.idempotency import (
    IdempotencyConflict,
    IdempotencyIdentity,
    load_evidence,
    new_evidence,
)
from a13n_service.environments.selection import Omitted
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_persisted_agent_principal_actions,
)
from a13n_service.interactions.acceptance import RunAcceptanceError, RunAcceptanceService
from a13n_service.interactions.control_domain import (
    ConsumeQueuedSubmissionRequest,
    QueuedSubmission,
    QueuedSubmissionConsumptionReceipt,
)
from a13n_service.interactions.control_models import QueuedSubmissionRecord
from a13n_service.interactions.domain import (
    Run,
    RunLineageKind,
    RunStatus,
    Thread,
    new_run_id,
)
from a13n_service.interactions.initialization import (
    RunStateSeed,
    initialize_completed_continuation_state,
    initialize_empty_thread_state,
)
from a13n_service.interactions.input import AcceptedAgentInput
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.interactions.objects import RunStateStore
from a13n_service.interactions.origin import SubmissionOrigin
from a13n_service.interactions.queue_validity import permanent_queue_failure
from a13n_service.interactions.state import RunCheckpoint
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, utc_now

from .command_evidence import (
    fingerprint_request,
    require_idempotency_key,
    run_command_scope,
    scoped_idempotency_key,
)
from .command_preparation import CommandInput, validate_invocation
from .errors import InteractionCommandError, command_not_found, idempotency_conflict, map_acceptance_error
from .initialization import NewRunPolicy

_USER_INPUT_ORIGIN = SubmissionOrigin()


@dataclass(frozen=True, slots=True)
class PreparedQueuedRun:
    run: Run
    state: RunCheckpoint
    input: AcceptedAgentInput
    validate: Callable[[AsyncSession], Awaitable[None]]


class QueuedRunCommands:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        invocations: AgentInvocationResolver,
        acceptance: RunAcceptanceService,
        states: RunStateStore,
        inputs: CommandInput,
        policy: NewRunPolicy,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._invocations = invocations
        self._acceptance = acceptance
        self._states = states
        self._inputs = inputs
        self._policy = policy
        self._clock = clock

    async def recover_queued(
        self, *, workspace_id: str, thread: Thread, queued: QueuedSubmission
    ) -> QueuedSubmissionConsumptionReceipt:
        """Recover one observed queue head without manufacturing HTTP replay evidence."""

        async def invalidity(database: AsyncSession):
            return await permanent_queue_failure(
                database, organization_id=thread.organization_id, workspace_id=workspace_id, queued=queued
            )

        async with short_session(self._sessions) as database:
            failure = await invalidity(database)
        if failure is not None:

            async def revalidate(database: AsyncSession) -> bool:
                return await invalidity(database) == failure

            try:
                return await self._acceptance.fail_queued_permanently(
                    organization_id=thread.organization_id,
                    thread_id=thread.id,
                    queued_submission_id=queued.queued_submission_id,
                    submission_digest_sha256=queued.submission_digest_sha256,
                    failure=failure,
                    expected_thread_version=thread.version,
                    expected_queue_version=thread.queue_version,
                    expected_current_run_id=thread.current_run_id,
                    expected_head_run_id=thread.head_run_id,
                    revalidate=revalidate,
                )
            except RunAcceptanceError as error:
                raise map_acceptance_error(error) from error
        return await self.consume_queued(
            actor=AuthenticatedActor(
                principal=queued.authority_principal,
                auth_method="stored_queued_submission",
                credential_id=queued.queued_submission_id,
                boundary_workspace_id=workspace_id,
                request_id=queued.queued_submission_id,
            ),
            thread_id=thread.id,
            idempotency_key=None,
            request=ConsumeQueuedSubmissionRequest(
                expected_thread_version=thread.version,
                expected_queue_version=thread.queue_version,
            ),
        )

    async def consume_queued(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        idempotency_key: str | None,
        request: ConsumeQueuedSubmissionRequest,
    ) -> QueuedSubmissionConsumptionReceipt:
        """Consume the current queue head under its retained authority."""

        request_fingerprint = fingerprint_request(request)
        stored_key = None
        if idempotency_key is not None:
            require_idempotency_key(idempotency_key)
            stored_key = scoped_idempotency_key(
                actor=actor,
                operation="queue.consume",
                scope_id=thread_id,
                supplied=idempotency_key,
            )
            replay = await self._queued_consumption_replay(
                actor=actor,
                thread_id=thread_id,
                stored_key=stored_key,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return replay

        current, head, thread, queued = await self._load_queued_consumption_source(
            actor=actor,
            thread_id=thread_id,
        )
        prepared = await self.prepare_queued_run(
            actor=actor,
            current=current,
            head=head,
            head_state=None if head is None else (await self._states.read_run(head)).envelope,
            thread=thread,
            queued=queued,
            request_fingerprint=request_fingerprint,
        )
        run = prepared.run

        async def record_receipt(database: AsyncSession, receipt: QueuedSubmissionConsumptionReceipt) -> None:
            # Automatic drain recovers from the queue row, not a synthetic HTTP command.
            if stored_key is None:
                return
            database.add(
                new_evidence(
                    organization_id=run.organization_id,
                    scope=run_command_scope(actor),
                    identity=IdempotencyIdentity(stored_key.removeprefix("idem_"), request_fingerprint),
                    result_kind="queue_consumption",
                    result_ref=run.id,
                    receipt=receipt.model_dump(mode="json"),
                    now=self._clock(),
                )
            )

        try:
            return await self._acceptance.consume_queued(
                run=run,
                state=prepared.state,
                queued_submission_id=queued.queued_submission_id,
                submission_digest_sha256=queued.submission_digest_sha256,
                accepted_input=prepared.input,
                expected_thread_version=request.expected_thread_version,
                expected_queue_version=request.expected_queue_version,
                expected_current_run_id=current.id,
                expected_head_run_id=None if head is None else head.id,
                next_head_run_id=None if head is None else head.id,
                final_validator=prepared.validate,
                transaction_hook=record_receipt,
            )
        except RunAcceptanceError as error:
            if stored_key is None:
                raise map_acceptance_error(error) from error
            replay = await self._queued_consumption_replay(
                actor=actor,
                thread_id=thread_id,
                stored_key=stored_key,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return replay
            raise map_acceptance_error(error) from error

    async def prepare_queued_run(
        self,
        *,
        actor: AuthenticatedActor,
        current: Run,
        head: Run | None,
        head_state: RunCheckpoint | None,
        thread: Thread,
        queued: QueuedSubmission,
        request_fingerprint: str,
    ) -> PreparedQueuedRun:
        """Resolve the queue Principal's intent outside its final acceptance transaction."""
        retained_actor = AuthenticatedActor(
            principal=queued.authority_principal,
            auth_method="stored_queued_submission",
            credential_id=queued.queued_submission_id,
            boundary_workspace_id=actor.workspace_id,
            request_id=actor.request_id,
        )
        target_agent_id = queued.submission.agent_id or (head.agent_id if head is not None else current.agent_id)
        run_id = new_run_id()
        prepared_input = await self._inputs.prepare(
            self._invocations,
            actor=retained_actor,
            agent_id=target_agent_id,
            agent_revision_id=queued.submission.agent_revision_id,
            expected_current_revision_id=queued.submission.expected_current_revision_id,
            config_override=queued.submission.config_override,
            submitted=queued.submission.input,
            environment=queued.submission.environment
            if "environment" in queued.submission.model_fields_set
            else Omitted.UNSET,
            inherited_environment_id=thread.default_environment_id,
        )
        seed = RunStateSeed.from_invocation(
            run_id=run_id,
            invocation=prepared_input.frozen,
            input=prepared_input.input,
        )
        if head is None:
            state = initialize_empty_thread_state(seed, thread_id=thread.id)
            parent_run_id = None
            lineage_kind = RunLineageKind.root
        else:
            assert head_state is not None
            state = initialize_completed_continuation_state(seed, head_state)
            parent_run_id = head.id
            lineage_kind = RunLineageKind.continue_

        now = self._clock()
        run = self._policy.create(
            now=now,
            id=run_id,
            organization_id=current.organization_id,
            authority_principal=queued.authority_principal,
            session_id=current.session_id,
            thread_id=current.thread_id,
            parent_run_id=parent_run_id,
            lineage_kind=lineage_kind,
            request_fingerprint=request_fingerprint,
            invocation=prepared_input.frozen,
            input=prepared_input.input,
            origin=SubmissionOrigin(trigger_type="queued_submission"),
        )

        async def validate_final(database: AsyncSession) -> None:
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    agent_id=target_agent_id,
                    action=WorkspaceAction.queued_submission_consume,
                )
                await authorize_persisted_agent_principal_actions(
                    database,
                    principal=queued.authority_principal,
                    organization_id=current.organization_id,
                    workspace_id=actor.workspace_id,
                    agent_id=target_agent_id,
                    actions=frozenset({WorkspaceAction.agent_invoke}),
                )
            except AuthorizationError as error:
                raise command_not_found() from error
            await validate_invocation(
                database, self._invocations, prepared=prepared_input.invocation, frozen=prepared_input.frozen
            )

        return PreparedQueuedRun(run, state, prepared_input.input, validate_final)

    async def _queued_consumption_replay(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        stored_key: str,
        request_fingerprint: str,
    ) -> QueuedSubmissionConsumptionReceipt | None:
        async with transaction(self._sessions) as database:
            try:
                evidence = await load_evidence(
                    database,
                    scope=run_command_scope(actor),
                    identity=IdempotencyIdentity(stored_key.removeprefix("idem_"), request_fingerprint),
                    now=self._clock(),
                )
                if evidence is None:
                    return None
                run = await database.get(RunRecord, evidence.result_ref)
                if run is None or run.thread_id != thread_id or run.organization_id != evidence.organization_id:
                    raise RuntimeError("Queue command evidence references a missing Run")
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    agent_id=run.agent_id,
                    action=WorkspaceAction.queued_submission_consume,
                )
            except AuthorizationError as error:
                raise command_not_found() from error
            except IdempotencyConflict as error:
                raise idempotency_conflict() from error
            return QueuedSubmissionConsumptionReceipt.model_validate(evidence.receipt_json)

    async def _load_queued_consumption_source(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
    ) -> tuple[Run, Run | None, Thread, QueuedSubmission]:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(SessionRecord, ThreadRecord, RunRecord)
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.organization_id == SessionRecord.organization_id,
                            ThreadRecord.session_id == SessionRecord.id,
                        ),
                    )
                    .join(
                        RunRecord,
                        and_(
                            RunRecord.organization_id == ThreadRecord.organization_id,
                            RunRecord.id == ThreadRecord.current_run_id,
                        ),
                    )
                    .where(
                        ThreadRecord.id == thread_id,
                        SessionRecord.workspace_id == actor.workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise command_not_found()
            _session_record, thread_record, current_record = row
            queued_record = await database.scalar(
                select(QueuedSubmissionRecord)
                .where(
                    QueuedSubmissionRecord.organization_id == thread_record.organization_id,
                    QueuedSubmissionRecord.thread_id == thread_record.id,
                    QueuedSubmissionRecord.position.is_not(None),
                )
                .order_by(QueuedSubmissionRecord.position, QueuedSubmissionRecord.id)
                .limit(1)
            )
            if queued_record is None:
                raise InteractionCommandError(
                    "queue_empty",
                    "The Thread has no queued submission to consume.",
                    category=ErrorCategory.conflict,
                )
            queued = queued_record.to_resource()
            target_agent_id = queued.submission.agent_id or current_record.agent_id
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    agent_id=target_agent_id,
                    action=WorkspaceAction.queued_submission_consume,
                )
            except AuthorizationError as error:
                raise command_not_found() from error
            head_record = (
                None
                if thread_record.head_run_id is None
                else await database.scalar(
                    select(RunRecord).where(
                        RunRecord.organization_id == thread_record.organization_id,
                        RunRecord.id == thread_record.head_run_id,
                    )
                )
            )
            if thread_record.head_run_id is not None and head_record is None:
                raise InteractionCommandError(
                    "thread_head_missing",
                    "The Thread head Run was not found.",
                    category=ErrorCategory.conflict,
                )
            if head_record is not None and head_record.status != RunStatus.completed.value:
                raise InteractionCommandError(
                    "queue_not_consumable",
                    "The Thread head Run is not completed.",
                    category=ErrorCategory.conflict,
                )
            return (
                current_record.to_resource(),
                None if head_record is None else head_record.to_resource(),
                thread_record.to_resource(),
                queued,
            )
