"""Prepare and consume queued intent under its retained Principal."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.invocation_resolution import (
    AgentInvocationResolver,
)
from a13n_service.application_errors import ErrorCategory
from a13n_service.durable_operations.entity_keys import scope_key
from a13n_service.environments.selection import Omitted
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_persisted_agent_principal_actions,
)
from a13n_service.iam.operation import authorization_operation
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
    initialize_start_state,
)
from a13n_service.interactions.input import AcceptedAgentInput
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunStateStore
from a13n_service.interactions.origin import SubmissionOrigin
from a13n_service.interactions.queue_validity import permanent_queue_failure
from a13n_service.interactions.state import RunCheckpoint
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, utc_now

from .command_evidence import (
    require_idempotency_key,
    run_command_scope,
    run_receipt,
    scoped_idempotency_key,
)
from .command_preparation import CommandInput
from .errors import InteractionCommandError, command_not_found, map_acceptance_error
from .initialization import NewRunPolicy
from .session_scope import SessionScope
from .sources import load_thread_head, load_thread_source

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

    @authorization_operation
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

    @authorization_operation
    async def consume_queued(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        idempotency_key: str | None,
        request: ConsumeQueuedSubmissionRequest,
    ) -> QueuedSubmissionConsumptionReceipt:
        """Consume the current queue head under its retained authority."""

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
            )
            if replay is not None:
                return replay

        try:
            current, head, thread, queued, session_scope = await self._load_queued_consumption_source(
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
            )
        except InteractionCommandError:
            if stored_key is not None:
                replay = await self._queued_consumption_replay(actor=actor, thread_id=thread_id, stored_key=stored_key)
                if replay is not None:
                    return replay
            raise

        run = prepared.run

        try:
            return await self._acceptance.consume_queued(
                session_scope=session_scope,
                run=run,
                state=prepared.state,
                queued=queued,
                accepted_input=prepared.input,
                expected_thread_version=request.expected_thread_version,
                expected_queue_version=request.expected_queue_version,
                expected_current_run_id=current.id,
                expected_head_run_id=None if head is None else head.id,
                next_head_run_id=None if head is None else head.id,
                final_validator=prepared.validate,
                consumption_key=None
                if stored_key is None
                else scope_key(run_command_scope(actor), stored_key.removeprefix("idem_")),
                label_overrides=queued.submission.labels,
            )
        except RunAcceptanceError as error:
            if stored_key is None:
                raise map_acceptance_error(error) from error
            replay = await self._queued_consumption_replay(
                actor=actor,
                thread_id=thread_id,
                stored_key=stored_key,
            )
            if replay is not None:
                return replay
            raise map_acceptance_error(error) from error

    @authorization_operation
    async def prepare_queued_run(
        self,
        *,
        actor: AuthenticatedActor,
        current: Run,
        head: Run | None,
        head_state: RunCheckpoint | None,
        thread: Thread,
        queued: QueuedSubmission,
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
            expected_default_revision_id=queued.submission.expected_default_revision_id,
            config_override=queued.submission.config_override,
            submitted=queued.submission.input,
            environment=queued.submission.environment
            if "environment" in queued.submission.model_fields_set
            else Omitted.UNSET,
            inherited_environment_id=thread.default_environment_id,
            inherited_environment_working_directory=thread.default_environment_working_directory,
        )
        seed = RunStateSeed.from_invocation(
            run_id=run_id,
            invocation=prepared_input.frozen,
            input=prepared_input.input,
        )
        if head is None:
            state = initialize_start_state(seed, thread_id=thread.id)
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

        return PreparedQueuedRun(run, state, prepared_input.input, validate_final)

    async def _queued_consumption_replay(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        stored_key: str,
    ) -> QueuedSubmissionConsumptionReceipt | None:
        async with short_session(self._sessions) as database:
            try:
                queued = await database.scalar(
                    select(QueuedSubmissionRecord).where(
                        QueuedSubmissionRecord.consumption_key
                        == scope_key(run_command_scope(actor), stored_key.removeprefix("idem_"))
                    )
                )
                if queued is None or queued.consumed_run_id is None:
                    return None
                run = await database.get(RunRecord, queued.consumed_run_id)
                if run is None or run.thread_id != thread_id:
                    return None
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    agent_id=run.agent_id,
                    action=WorkspaceAction.queued_submission_consume,
                )
            except AuthorizationError as error:
                raise command_not_found() from error
            thread = await database.get(ThreadRecord, thread_id)
            if thread is None:
                return None
            receipt = QueuedSubmissionConsumptionReceipt(
                outcome="run_accepted",
                queued_submission=queued.to_resource(),
                queue_version=thread.queue_version,
                run=await run_receipt(database, run),
            )
        return receipt

    async def _load_queued_consumption_source(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
    ) -> tuple[Run, Run | None, Thread, QueuedSubmission, SessionScope]:
        async with short_session(self._sessions) as database:
            observed = await load_thread_source(database, workspace_id=actor.workspace_id, thread_id=thread_id)
            thread, current = observed.thread, observed.current
            if current is None:
                raise command_not_found()
            queued_record = await database.scalar(
                select(QueuedSubmissionRecord)
                .where(
                    QueuedSubmissionRecord.organization_id == thread.organization_id,
                    QueuedSubmissionRecord.thread_id == thread.id,
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
            target_agent_id = queued.submission.agent_id or current.agent_id
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
            head = await load_thread_head(database, observed)
            if thread.head_run_id is not None and head is None:
                raise InteractionCommandError(
                    "thread_head_missing",
                    "The Thread head Run was not found.",
                    category=ErrorCategory.conflict,
                )
            if head is not None and head.status is not RunStatus.completed:
                raise InteractionCommandError(
                    "queue_not_consumable",
                    "The Thread head Run is not completed.",
                    category=ErrorCategory.conflict,
                )
            return (
                current,
                head,
                thread,
                queued,
                observed.session_scope,
            )
