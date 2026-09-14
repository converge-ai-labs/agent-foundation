"""Authorized Native API for editable queued Run submissions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyConflict,
    IdempotencyIdentity,
    is_evidence_unique_race,
    load_evidence,
    new_evidence,
)
from a13n_service.durable_operations.requests import request_scope
from a13n_service.environments.selection import Omitted
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.interactions.command_evidence import command_identity
from a13n_service.interactions.command_values import ContinueRunCommand, WaitingContinueRunCommand
from a13n_service.interactions.commands import InteractionCommands
from a13n_service.interactions.control_domain import (
    ConsumeQueuedSubmissionRequest,
    QueuedSubmission,
    QueuedSubmissionCollection,
    QueuedSubmissionConsumptionReceipt,
    QueuedSubmissionMutationReceipt,
    QueuedSubmissionState,
    ReorderQueuedSubmissionsRequest,
    RunAcceptanceReceipt,
    ThreadQueueMutationReceipt,
    ThreadRunSubmissionIntent,
    ThreadRunSubmissionReceipt,
    ThreadRunSubmissionRequest,
    UpdateQueuedSubmissionRequest,
)
from a13n_service.interactions.control_models import QueuedSubmissionRecord
from a13n_service.interactions.domain import Run, StrictModel, Thread
from a13n_service.interactions.errors import InteractionCommandError, RunAcceptanceError, map_acceptance_error
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.interactions.queue import (
    QueuedSubmissionConflict,
    QueuedSubmissionStore,
    ThreadSubmissionAdmission,
    classify_thread_submission,
)
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, utc_now


class DeleteQueuedSubmissionRequest(StrictModel):
    expected_version: int = Field(ge=1)


@dataclass(frozen=True, slots=True)
class _QueueScope:
    organization_id: str
    workspace_id: str
    thread_id: str
    agent_id: str


class QueuedSubmissionService:
    """Apply queue IAM and durable idempotency around the core queue store."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        store: QueuedSubmissionStore,
        commands: InteractionCommands,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._store = store
        self._commands = commands
        self._clock = clock

    async def submit(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        request: ThreadRunSubmissionRequest,
        idempotency_key: str,
    ) -> ThreadRunSubmissionReceipt:
        async def replay() -> ThreadRunSubmissionReceipt | None:
            return await self._pre_replay(
                actor=actor,
                operation="thread.submit",
                scope_id=thread_id,
                idempotency_key=idempotency_key,
                request=request,
                response_type=ThreadRunSubmissionReceipt,
            )

        replayed = await replay()
        if replayed is not None:
            return replayed
        try:
            return await self._submit(
                actor=actor,
                thread_id=thread_id,
                request=request,
                identity=command_identity(idempotency_key, request),
                evidence_scope=_evidence_scope(actor, operation="thread.submit", scope_id=thread_id),
            )
        except (InteractionCommandError, RunAcceptanceError, IntegrityError) as error:
            if isinstance(error, IntegrityError) and not is_evidence_unique_race(error):
                raise
            # Another copy can commit after preflight, including before source eligibility checks.
            replayed = await replay()
            if replayed is not None:
                return replayed
            if isinstance(error, RunAcceptanceError):
                raise map_acceptance_error(error) from error
            raise

    async def _submit(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        request: ThreadRunSubmissionRequest,
        identity: IdempotencyIdentity,
        evidence_scope: EvidenceScope,
    ) -> ThreadRunSubmissionReceipt:
        scope, thread, current, head, admission = await self._submission_admission(
            actor=actor, thread_id=thread_id, request=request
        )
        if admission is ThreadSubmissionAdmission.queued:
            return await self._enqueue_thread_submission(
                actor=actor,
                scope=scope,
                thread=thread,
                request=request,
                identity=identity,
                evidence_scope=evidence_scope,
            )

        accepted_receipt: ThreadRunSubmissionReceipt | None = None

        async def commit_run(database: AsyncSession, receipt: RunAcceptanceReceipt) -> None:
            nonlocal accepted_receipt
            if admission in {ThreadSubmissionAdmission.continuation, ThreadSubmissionAdmission.root}:
                live_queue = await database.scalar(
                    select(QueuedSubmissionRecord.id)
                    .where(
                        QueuedSubmissionRecord.organization_id == scope.organization_id,
                        QueuedSubmissionRecord.thread_id == thread.id,
                        QueuedSubmissionRecord.position.is_not(None),
                    )
                    .order_by(QueuedSubmissionRecord.position, QueuedSubmissionRecord.id)
                    .limit(1)
                    .with_for_update()
                )
                if live_queue is not None:
                    raise InteractionCommandError(
                        "thread_queue_precedence_changed",
                        "Queued intent gained precedence before Run acceptance.",
                        category=ErrorCategory.conflict,
                    )
            selected_thread = await database.get(ThreadRecord, thread.id)
            if selected_thread is None:
                raise _not_found()
            accepted_receipt = ThreadRunSubmissionReceipt(
                outcome="run_accepted", run=receipt, queue_version=selected_thread.queue_version
            )
            await self._record_receipt(
                database,
                scope=scope,
                evidence_scope=evidence_scope,
                identity=identity,
                receipt=accepted_receipt,
            )

        continuation = ContinueRunCommand(
            expected_thread_version=request.expected_thread_version,
            input=request.input,
            agent_id=request.agent_id,
            agent_revision_id=request.agent_revision_id,
            expected_current_revision_id=request.expected_current_revision_id,
            config_override=request.config_override,
            hook_subscription=request.hook_subscription,
            environment=request.environment if "environment" in request.model_fields_set else Omitted.UNSET,
        )
        if admission is ThreadSubmissionAdmission.continuation:
            assert head is not None
            await self._commands.runs.accept_continuation(
                actor=actor,
                source_run_id=head.id,
                request_fingerprint=identity.request_digest,
                request=continuation,
                inherit_parent_environment=False,
                transaction_hook=commit_run,
            )
        elif admission is ThreadSubmissionAdmission.root:
            await self._commands.runs.accept_empty_thread(
                actor=actor,
                thread_id=thread_id,
                request_fingerprint=identity.request_digest,
                request=continuation,
                transaction_hook=commit_run,
            )
        elif admission is ThreadSubmissionAdmission.waiting_continue:
            assert current is not None
            if current.sealed_state is None or request.waiting_resolution is None:
                raise InteractionCommandError(
                    "run_waiting_state_invalid",
                    "The current waiting Run has no complete sealed state.",
                    category=ErrorCategory.conflict,
                )
            await self._commands.continuations.accept_waiting_continue(
                actor=actor,
                run_id=current.id,
                request_fingerprint=identity.request_digest,
                request=WaitingContinueRunCommand(
                    expected_thread_version=request.expected_thread_version,
                    sealed_state_digest_sha256=request.waiting_resolution.sealed_state_digest_sha256,
                    input=request.input,
                    **request.model_dump(include={"hook_subscription"}, exclude_unset=True),
                ),
                transaction_hook=commit_run,
            )
        else:
            raise InteractionCommandError(
                "thread_submission_rejected",
                "The Thread cannot accept or queue this submission.",
                category=ErrorCategory.conflict,
            )
        assert accepted_receipt is not None
        return accepted_receipt

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        state: QueuedSubmissionState,
        limit: int,
    ) -> QueuedSubmissionCollection:
        scope = await self._thread_scope(
            actor=actor, thread_id=thread_id, action=WorkspaceAction.queued_submission_read
        )
        try:
            return await self._store.list(
                organization_id=scope.organization_id,
                thread_id=thread_id,
                state=state,
                limit=limit,
            )
        except (QueuedSubmissionConflict, ValueError) as error:
            raise _queue_error(error) from error

    async def consume(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        request: ConsumeQueuedSubmissionRequest,
        idempotency_key: str,
    ) -> QueuedSubmissionConsumptionReceipt:
        return await self._commands.queued.consume_queued(
            actor=actor,
            thread_id=thread_id,
            request=request,
            idempotency_key=idempotency_key,
        )

    async def get(self, *, actor: AuthenticatedActor, queued_submission_id: str) -> QueuedSubmission:
        scope = await self._submission_scope(
            actor=actor,
            queued_submission_id=queued_submission_id,
            action=WorkspaceAction.queued_submission_read,
        )
        try:
            return await self._store.get(
                organization_id=scope.organization_id,
                queued_submission_id=queued_submission_id,
            )
        except QueuedSubmissionConflict as error:
            raise _queue_error(error) from error

    async def enqueue(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        expected_thread_version: int,
        submission: ThreadRunSubmissionIntent,
        idempotency_key: str,
    ) -> QueuedSubmissionMutationReceipt:
        request = _EnqueueRequest(expected_thread_version=expected_thread_version, submission=submission)
        if replayed := await self._pre_replay(
            actor=actor,
            operation="queue.enqueue",
            scope_id=thread_id,
            idempotency_key=idempotency_key,
            request=request,
            response_type=QueuedSubmissionMutationReceipt,
        ):
            return replayed
        target_agent_id = await self._target_agent_id(actor=actor, thread_id=thread_id, submission=submission)
        scope = await self._thread_scope(
            actor=actor,
            thread_id=thread_id,
            action=WorkspaceAction.queued_submission_create,
            agent_id=target_agent_id,
        )
        await self._authorize(actor=actor, scope=scope, agent_id=target_agent_id, action=WorkspaceAction.agent_invoke)
        return await self._mutate(
            actor=actor,
            scope=scope,
            operation="queue.enqueue",
            scope_id=thread_id,
            idempotency_key=idempotency_key,
            request=request,
            response_type=QueuedSubmissionMutationReceipt,
            final_authorizations=(
                (target_agent_id, WorkspaceAction.queued_submission_create),
                (target_agent_id, WorkspaceAction.agent_invoke),
            ),
            invoke=lambda replay, commit: self._store.enqueue(
                organization_id=scope.organization_id,
                thread_id=thread_id,
                expected_thread_version=expected_thread_version,
                authority_principal=actor.principal,
                submission=submission,
                replay=replay,
                transaction_hook=commit,
            ),
        )

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        queued_submission_id: str,
        request: UpdateQueuedSubmissionRequest,
        idempotency_key: str,
    ) -> QueuedSubmissionMutationReceipt:
        if replayed := await self._pre_replay(
            actor=actor,
            operation="queue.update",
            scope_id=queued_submission_id,
            idempotency_key=idempotency_key,
            request=request,
            response_type=QueuedSubmissionMutationReceipt,
        ):
            return replayed
        scope = await self._submission_scope(
            actor=actor,
            queued_submission_id=queued_submission_id,
            action=WorkspaceAction.queued_submission_update,
        )
        target_agent_id = request.submission.agent_id or scope.agent_id
        await self._authorize(actor=actor, scope=scope, agent_id=target_agent_id, action=WorkspaceAction.agent_invoke)
        return await self._mutate(
            actor=actor,
            scope=scope,
            operation="queue.update",
            scope_id=queued_submission_id,
            idempotency_key=idempotency_key,
            request=request,
            response_type=QueuedSubmissionMutationReceipt,
            final_authorizations=(
                (scope.agent_id, WorkspaceAction.queued_submission_update),
                (target_agent_id, WorkspaceAction.agent_invoke),
            ),
            invoke=lambda replay, commit: self._store.update(
                organization_id=scope.organization_id,
                queued_submission_id=queued_submission_id,
                expected_version=request.expected_version,
                actor_principal=actor.principal,
                submission=request.submission,
                replay=replay,
                transaction_hook=commit,
            ),
        )

    async def delete(
        self,
        *,
        actor: AuthenticatedActor,
        queued_submission_id: str,
        request: DeleteQueuedSubmissionRequest,
        idempotency_key: str,
    ) -> ThreadQueueMutationReceipt:
        if replayed := await self._pre_replay(
            actor=actor,
            operation="queue.delete",
            scope_id=queued_submission_id,
            idempotency_key=idempotency_key,
            request=request,
            response_type=ThreadQueueMutationReceipt,
        ):
            return replayed
        scope = await self._submission_scope(
            actor=actor,
            queued_submission_id=queued_submission_id,
            action=WorkspaceAction.queued_submission_delete,
        )
        return await self._mutate(
            actor=actor,
            scope=scope,
            operation="queue.delete",
            scope_id=queued_submission_id,
            idempotency_key=idempotency_key,
            request=request,
            response_type=ThreadQueueMutationReceipt,
            final_authorizations=((scope.agent_id, WorkspaceAction.queued_submission_delete),),
            invoke=lambda replay, commit: self._store.delete(
                organization_id=scope.organization_id,
                queued_submission_id=queued_submission_id,
                expected_version=request.expected_version,
                replay=replay,
                transaction_hook=commit,
            ),
        )

    async def reorder(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        request: ReorderQueuedSubmissionsRequest,
        idempotency_key: str,
    ) -> ThreadQueueMutationReceipt:
        if replayed := await self._pre_replay(
            actor=actor,
            operation="queue.reorder",
            scope_id=thread_id,
            idempotency_key=idempotency_key,
            request=request,
            response_type=ThreadQueueMutationReceipt,
        ):
            return replayed
        scope = await self._thread_scope(
            actor=actor,
            thread_id=thread_id,
            action=WorkspaceAction.queued_submission_reorder,
        )
        return await self._mutate(
            actor=actor,
            scope=scope,
            operation="queue.reorder",
            scope_id=thread_id,
            idempotency_key=idempotency_key,
            request=request,
            response_type=ThreadQueueMutationReceipt,
            final_authorizations=((scope.agent_id, WorkspaceAction.queued_submission_reorder),),
            invoke=lambda replay, commit: self._store.reorder(
                organization_id=scope.organization_id,
                thread_id=thread_id,
                expected_queue_version=request.expected_queue_version,
                queued_submission_ids=request.queued_submission_ids,
                replay=replay,
                transaction_hook=commit,
            ),
        )

    async def _enqueue_thread_submission(
        self,
        *,
        actor: AuthenticatedActor,
        scope: _QueueScope,
        thread: Thread,
        request: ThreadRunSubmissionRequest,
        identity: IdempotencyIdentity,
        evidence_scope: EvidenceScope,
    ) -> ThreadRunSubmissionReceipt:
        target_agent_id = request.agent_id or scope.agent_id
        await self._authorize(
            actor=actor,
            scope=scope,
            agent_id=target_agent_id,
            action=WorkspaceAction.queued_submission_create,
        )
        await self._authorize(
            actor=actor,
            scope=scope,
            agent_id=target_agent_id,
            action=WorkspaceAction.agent_invoke,
        )

        async def replay(database: AsyncSession) -> QueuedSubmissionMutationReceipt | None:
            wrapped = await _load_receipt(
                database,
                evidence_scope=evidence_scope,
                identity=identity,
                response_type=ThreadRunSubmissionReceipt,
                now=self._clock(),
            )
            if wrapped is None:
                return None
            if wrapped.queued_submission is None:
                raise IdempotencyConflict
            return QueuedSubmissionMutationReceipt(
                queued_submission=wrapped.queued_submission,
                queue_version=wrapped.queue_version,
            )

        async def commit(database: AsyncSession, receipt: QueuedSubmissionMutationReceipt) -> None:
            try:
                for action in (WorkspaceAction.queued_submission_create, WorkspaceAction.agent_invoke):
                    await authorize_agent(
                        database,
                        actor=actor,
                        workspace_id=scope.workspace_id,
                        agent_id=target_agent_id,
                        action=action,
                    )
            except AuthorizationError as error:
                raise _not_found() from error
            wrapped = ThreadRunSubmissionReceipt(
                outcome="queued",
                queued_submission=receipt.queued_submission,
                queue_version=receipt.queue_version,
            )
            await self._record_receipt(
                database,
                scope=scope,
                evidence_scope=evidence_scope,
                identity=identity,
                receipt=wrapped,
            )

        try:
            queued = await self._store.enqueue(
                organization_id=scope.organization_id,
                thread_id=thread.id,
                expected_thread_version=request.expected_thread_version,
                authority_principal=actor.principal,
                submission=request.intent(),
                replay=replay,
                transaction_hook=commit,
            )
        except IdempotencyConflict as error:
            raise _idempotency_conflict() from error
        except QueuedSubmissionConflict as error:
            raise _queue_error(error) from error
        return ThreadRunSubmissionReceipt(
            outcome="queued",
            queued_submission=queued.queued_submission,
            queue_version=queued.queue_version,
        )

    async def _submission_admission(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        request: ThreadRunSubmissionRequest,
    ) -> tuple[_QueueScope, Thread, Run | None, Run | None, ThreadSubmissionAdmission]:
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
                    .outerjoin(
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
                raise _not_found()
            session_record, thread_record, current_record = row
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
            has_queue = bool(
                await database.scalar(
                    select(
                        exists().where(
                            QueuedSubmissionRecord.organization_id == thread_record.organization_id,
                            QueuedSubmissionRecord.thread_id == thread_record.id,
                            QueuedSubmissionRecord.position.is_not(None),
                        )
                    )
                )
            )
            thread = thread_record.to_resource()
            current = current_record.to_resource() if current_record is not None else None
            head = None if head_record is None else head_record.to_resource()
            try:
                admission = classify_thread_submission(
                    thread=thread,
                    current=current,
                    head=head,
                    has_queued_submission=has_queue,
                    waiting_resolution_requested=request.waiting_resolution is not None,
                )
            except QueuedSubmissionConflict as error:
                raise _queue_error(error) from error
            action = (
                WorkspaceAction.queued_submission_create
                if admission is ThreadSubmissionAdmission.queued
                else WorkspaceAction.run_continue
            )
            target_agent_id = request.agent_id or (current.agent_id if current else None)
            if target_agent_id is None:
                raise InteractionCommandError(
                    "agent_required", "First input requires an Agent selection.", category=ErrorCategory.invalid_request
                )
            scope = _QueueScope(
                session_record.organization_id,
                session_record.workspace_id,
                thread.id,
                current.agent_id if current else target_agent_id,
            )
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=scope.workspace_id,
                    agent_id=target_agent_id,
                    action=action,
                )
            except AuthorizationError as error:
                raise _not_found() from error
            return scope, thread, current, head, admission

    async def _mutate[ReceiptT: BaseModel](
        self,
        *,
        actor: AuthenticatedActor,
        scope: _QueueScope,
        operation: str,
        scope_id: str,
        idempotency_key: str,
        request: StrictModel,
        response_type: type[ReceiptT],
        final_authorizations: tuple[tuple[str, WorkspaceAction], ...],
        invoke: Callable[
            [
                Callable[[AsyncSession], Awaitable[ReceiptT | None]],
                Callable[[AsyncSession, ReceiptT], Awaitable[None]],
            ],
            Awaitable[ReceiptT],
        ],
    ) -> ReceiptT:
        identity = command_identity(idempotency_key, request)
        evidence_scope = EvidenceScope(
            workspace_id=scope.workspace_id,
            actor_type=actor.principal.principal_type.value,
            actor_id=actor.principal.principal_id,
            operation=operation,
            scope_id=scope_id,
            organization_id=actor.boundary_organization_id,
        )

        async def replay(database: AsyncSession) -> ReceiptT | None:
            return await _load_receipt(
                database,
                evidence_scope=evidence_scope,
                identity=identity,
                response_type=response_type,
                now=self._clock(),
            )

        async def commit(database: AsyncSession, receipt: ReceiptT) -> None:
            try:
                for agent_id, action in final_authorizations:
                    await authorize_agent(
                        database,
                        actor=actor,
                        workspace_id=scope.workspace_id,
                        agent_id=agent_id,
                        action=action,
                    )
            except AuthorizationError as error:
                raise _not_found() from error
            await self._record_receipt(
                database,
                scope=scope,
                evidence_scope=evidence_scope,
                identity=identity,
                receipt=receipt,
            )

        try:
            return await invoke(replay, commit)
        except IdempotencyConflict as error:
            raise _idempotency_conflict() from error
        except QueuedSubmissionConflict as error:
            raise _queue_error(error) from error
        except IntegrityError as error:
            if not is_evidence_unique_race(error):
                raise
            async with short_session(self._sessions) as database:
                replayed = await replay(database)
            if replayed is None:
                raise InteractionCommandError(
                    "idempotency_reconciliation_failed",
                    "The queue command outcome could not be reconciled.",
                    category=ErrorCategory.unavailable,
                ) from error
            return replayed

    async def _record_receipt(
        self,
        database: AsyncSession,
        *,
        scope: _QueueScope,
        evidence_scope: EvidenceScope,
        identity: IdempotencyIdentity,
        receipt: BaseModel,
    ) -> None:
        now = self._clock()
        database.add(
            new_evidence(
                organization_id=scope.organization_id,
                scope=evidence_scope,
                identity=identity,
                result_kind="thread_command",
                result_ref=scope.thread_id,
                receipt=receipt.model_dump(mode="json", by_alias=True),
                now=now,
            )
        )

    async def _pre_replay[ReceiptT: BaseModel](
        self,
        *,
        actor: AuthenticatedActor,
        operation: str,
        scope_id: str,
        idempotency_key: str,
        request: StrictModel,
        response_type: type[ReceiptT],
    ) -> ReceiptT | None:
        identity = command_identity(idempotency_key, request)
        evidence_scope = EvidenceScope(
            workspace_id=actor.workspace_id,
            actor_type=actor.principal.principal_type.value,
            actor_id=actor.principal.principal_id,
            operation=operation,
            scope_id=scope_id,
            organization_id=actor.boundary_organization_id,
        )
        try:
            async with short_session(self._sessions) as database:
                return await _load_receipt(
                    database,
                    evidence_scope=evidence_scope,
                    identity=identity,
                    response_type=response_type,
                    now=self._clock(),
                )
        except IdempotencyConflict as error:
            raise _idempotency_conflict() from error

    async def _thread_scope(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        action: WorkspaceAction,
        agent_id: str | None = None,
    ) -> _QueueScope:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(SessionRecord.organization_id, SessionRecord.workspace_id, RunRecord.agent_id)
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
                raise _not_found()
            scope = _QueueScope(row[0], row[1], thread_id, row[2])
            await self._authorize(
                actor=actor,
                scope=scope,
                agent_id=agent_id or scope.agent_id,
                action=action,
                database=database,
            )
            return scope

    async def _submission_scope(
        self,
        *,
        actor: AuthenticatedActor,
        queued_submission_id: str,
        action: WorkspaceAction,
    ) -> _QueueScope:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(
                        SessionRecord.organization_id,
                        SessionRecord.workspace_id,
                        ThreadRecord.id,
                        RunRecord.agent_id,
                    )
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.organization_id == SessionRecord.organization_id,
                            ThreadRecord.session_id == SessionRecord.id,
                        ),
                    )
                    .join(
                        QueuedSubmissionRecord,
                        and_(
                            QueuedSubmissionRecord.organization_id == ThreadRecord.organization_id,
                            QueuedSubmissionRecord.thread_id == ThreadRecord.id,
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
                        QueuedSubmissionRecord.id == queued_submission_id,
                        SessionRecord.workspace_id == actor.workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise _not_found()
            scope = _QueueScope(row[0], row[1], row[2], row[3])
            await self._authorize(actor=actor, scope=scope, agent_id=scope.agent_id, action=action, database=database)
            return scope

    async def _target_agent_id(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        submission: ThreadRunSubmissionIntent,
    ) -> str:
        if submission.agent_id is not None:
            return submission.agent_id
        scope = await self._thread_scope(
            actor=actor,
            thread_id=thread_id,
            action=WorkspaceAction.queued_submission_create,
        )
        return scope.agent_id

    async def _authorize(
        self,
        *,
        actor: AuthenticatedActor,
        scope: _QueueScope,
        agent_id: str,
        action: WorkspaceAction,
        database: AsyncSession | None = None,
    ) -> None:
        try:
            if database is not None:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=scope.workspace_id,
                    agent_id=agent_id,
                    action=action,
                )
                return
            async with short_session(self._sessions) as opened:
                await authorize_agent(
                    opened,
                    actor=actor,
                    workspace_id=scope.workspace_id,
                    agent_id=agent_id,
                    action=action,
                )
        except AuthorizationError as error:
            raise _not_found() from error


class _EnqueueRequest(StrictModel):
    expected_thread_version: int = Field(ge=1)
    submission: ThreadRunSubmissionIntent


async def _load_receipt[ReceiptT: BaseModel](
    database: AsyncSession,
    *,
    evidence_scope: EvidenceScope,
    identity: IdempotencyIdentity,
    response_type: type[ReceiptT],
    now: datetime,
) -> ReceiptT | None:
    evidence = await load_evidence(database, scope=evidence_scope, identity=identity, now=now)
    if evidence is None:
        return None
    if evidence.result_kind != "thread_command" or evidence.receipt_json is None:
        raise RuntimeError("Thread command evidence has no original receipt")
    return response_type.model_validate(evidence.receipt_json)


def _evidence_scope(actor: AuthenticatedActor, *, operation: str, scope_id: str) -> EvidenceScope:
    return request_scope(actor, workspace_id=actor.workspace_id, operation=operation, scope_id=scope_id)


def _not_found() -> InteractionCommandError:
    return InteractionCommandError(
        "resource_not_found", "The requested resource was not found.", category=ErrorCategory.not_found
    )


def _idempotency_conflict() -> InteractionCommandError:
    return InteractionCommandError(
        "idempotency_conflict",
        "The Idempotency-Key was already used with different request content.",
        category=ErrorCategory.conflict,
    )


def _queue_error(error: Exception) -> InteractionCommandError:
    category = ErrorCategory.invalid_request if isinstance(error, ValueError) else ErrorCategory.conflict
    return InteractionCommandError("queued_submission_conflict", str(error), category=category)


__all__ = ["DeleteQueuedSubmissionRequest", "QueuedSubmissionService"]
