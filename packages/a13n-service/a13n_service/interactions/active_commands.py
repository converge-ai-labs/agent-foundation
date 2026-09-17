"""Interrupt and steer accepted work without creating another Run."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from a13n_harness import SafeFailure
from sqlalchemy import and_, select
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
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
)
from a13n_service.interactions.control_domain import (
    InterruptRequest,
    SteerReceipt,
    SteerStatus,
)
from a13n_service.interactions.domain import (
    RunStatus,
)
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.inbox_persistence import ThreadInboxConflict
from a13n_service.interactions.input import (
    AgentInput,
)
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.interactions.objects import RunObjectError, RunStateStore
from a13n_service.interactions.origin import SubmissionOrigin
from a13n_service.interactions.outcomes import RunOutcomeError, RunOutcomeService
from a13n_service.storage import ObjectStoreError, short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .acceptance import RunAcceptanceService
from .access import authorize_interaction
from .command_evidence import (
    command_identity,
)
from .command_preparation import CommandInput
from .control_domain import InterruptReceipt
from .errors import InteractionCommandError, command_not_found, idempotency_conflict

_USER_INPUT_ORIGIN = SubmissionOrigin()


class ActiveRunCommands:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        states: RunStateStore,
        outcomes: RunOutcomeService,
        inbox: ThreadInboxStore,
        inputs: CommandInput,
        *,
        acceptance: RunAcceptanceService,
        clock: Clock = utc_now,
    ) -> None:
        self._acceptance = acceptance
        self._sessions = sessions
        self._states = states
        self._outcomes = outcomes
        self._inbox = inbox
        self._inputs = inputs
        self._clock = clock

    async def interrupt(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        request: InterruptRequest,
    ) -> InterruptReceipt:
        identity = command_identity(idempotency_key, request)
        scope = request_scope(actor, workspace_id=actor.workspace_id, operation="run.interrupt", scope_id=run_id)
        replay = await self._interrupt_replay(actor=actor, run_id=run_id, scope=scope, identity=identity)
        if replay is not None:
            return replay
        source, thread = await self._load_interrupt_source(actor=actor, run_id=run_id)
        if source.version != request.expected_run_version or thread.version != request.expected_thread_version:
            raise InteractionCommandError(
                "run_precondition_changed",
                "The Run or Thread version changed before interruption.",
                category=ErrorCategory.conflict,
            )
        now = assume_utc(self._clock())

        async def validate_final(database: AsyncSession) -> None:
            try:
                await authorize_interaction(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    session_id=source.session_id,
                    agent_id=source.agent_id,
                    action=WorkspaceAction.run_interrupt,
                )
            except AuthorizationError as error:
                raise command_not_found() from error

        async def record_evidence(database: AsyncSession) -> None:
            try:
                existing = await load_evidence(database, scope=scope, identity=identity, now=now)
            except IdempotencyConflict as error:
                raise idempotency_conflict() from error
            if existing is None:
                database.add(
                    new_evidence(
                        organization_id=source.organization_id,
                        scope=scope,
                        identity=identity,
                        result_kind="run_interrupt",
                        result_ref=run_id,
                        now=now,
                    )
                )

        try:
            outcome = await self._outcomes.cancel(
                organization_id=source.organization_id,
                run_id=run_id,
                expected_run_version=request.expected_run_version,
                expected_thread_version=request.expected_thread_version,
                failure=SafeFailure(
                    code="run_interrupted",
                    message="The Run was interrupted by the client.",
                    retry_hint="new_run",
                ),
                final_validator=validate_final,
                transaction_hook=record_evidence,
            )
        except IntegrityError as error:
            if not is_evidence_unique_race(error):
                raise InteractionCommandError(
                    "run_interrupt_conflict",
                    "Run interruption lost a concurrent mutation.",
                    category=ErrorCategory.conflict,
                ) from error
            replay = await self._interrupt_replay(actor=actor, run_id=run_id, scope=scope, identity=identity)
            if replay is not None:
                return replay
            raise InteractionCommandError(
                "run_interrupt_conflict",
                "Run interruption lost a concurrent mutation.",
                category=ErrorCategory.conflict,
            ) from error
        except RunOutcomeError as error:
            replay = await self._interrupt_replay(actor=actor, run_id=run_id, scope=scope, identity=identity)
            if replay is not None:
                return replay
            raise InteractionCommandError(
                "run_interrupt_conflict",
                "The Run can no longer be interrupted.",
                category=ErrorCategory.conflict,
            ) from error
        assert outcome.sealed_at is not None
        return InterruptReceipt(run_id=run_id, interrupted_at=outcome.sealed_at)

    async def steer(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        input: AgentInput,
        transaction_hook: Callable[[AsyncSession, SteerReceipt], Awaitable[None]] | None = None,
    ) -> SteerReceipt:
        identity = command_identity(idempotency_key, input)
        scope = request_scope(actor, workspace_id=actor.workspace_id, operation="run.steer", scope_id=run_id)
        replay = await self._steer_replay(actor=actor, run_id=run_id, scope=scope, identity=identity)
        if replay is not None:
            return replay
        source, _thread = await self._load_steer_source(actor=actor, run_id=run_id)
        try:
            state = await self._states.read_run(source)
        except (ObjectStoreError, RunObjectError) as error:
            raise InteractionCommandError(
                "run_state_unavailable",
                "The selected Run state is unavailable.",
                category=ErrorCategory.conflict,
            ) from error
        accepted = await self._inputs.accept_effective(
            actor=actor,
            workspace_id=actor.workspace_id,
            submitted=input,
            effective=state.envelope.effective_agent_config,
            environment_access=source.environment_access,
            retained_secret_bindings=state.envelope.secret_bindings,
        )
        now = assume_utc(self._clock())

        async def record_evidence(database: AsyncSession, receipt: SteerReceipt) -> None:
            await self._acceptance.validate_in_session(database, receipt.run_id)
            try:
                existing = await load_evidence(database, scope=scope, identity=identity, now=now)
            except IdempotencyConflict as error:
                raise idempotency_conflict() from error
            if existing is None:
                database.add(
                    new_evidence(
                        organization_id=source.organization_id,
                        scope=scope,
                        identity=identity,
                        result_kind="run_steer",
                        result_ref=receipt.steer_id,
                        now=now,
                    )
                )

            if transaction_hook is not None:
                await transaction_hook(database, receipt)

        try:
            return await self._inbox.append_steer(
                organization_id=source.organization_id,
                run_id=source.id,
                input=accepted,
                transaction_hook=record_evidence,
            )
        except IntegrityError as error:
            if is_evidence_unique_race(error):
                replay = await self._steer_replay(actor=actor, run_id=run_id, scope=scope, identity=identity)
                if replay is not None:
                    return replay
            raise InteractionCommandError(
                "run_steer_conflict",
                "Run steering lost a concurrent mutation.",
                category=ErrorCategory.conflict,
            ) from error
        except ThreadInboxConflict as error:
            raise InteractionCommandError(
                "run_steer_conflict",
                "The selected Run can no longer accept steer input.",
                category=ErrorCategory.conflict,
            ) from error

    async def get_steer(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        steer_id: str,
    ) -> SteerStatus:
        source, _thread = await self._load_steer_source(actor=actor, run_id=run_id, read_only=True)
        try:
            return await self._inbox.get_steer(organization_id=source.organization_id, run_id=run_id, steer_id=steer_id)
        except ThreadInboxConflict as error:
            raise command_not_found() from error

    async def _steer_replay(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        scope: EvidenceScope,
        identity: IdempotencyIdentity,
    ) -> SteerReceipt | None:
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, SessionRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.organization_id == RunRecord.organization_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise command_not_found()
            run, _session = row
            try:
                await authorize_interaction(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    session_id=run.session_id,
                    agent_id=run.agent_id,
                    action=WorkspaceAction.run_steer,
                )
                evidence = await load_evidence(database, scope=scope, identity=identity, now=now)
            except AuthorizationError as error:
                raise command_not_found() from error
            except IdempotencyConflict as error:
                raise idempotency_conflict() from error
            if evidence is None:
                return None
            if evidence.result_kind != "run_steer":
                raise InteractionCommandError(
                    "idempotency_evidence_invalid",
                    "The Run steer replay evidence is invalid.",
                    category=ErrorCategory.unavailable,
                )
            await self._acceptance.validate_in_session(database, run_id)
            steer_id = evidence.result_ref
            organization_id = run.organization_id
        try:
            status = await self._inbox.get_steer(organization_id=organization_id, run_id=run_id, steer_id=steer_id)
        except ThreadInboxConflict as error:
            raise InteractionCommandError(
                "idempotency_evidence_invalid",
                "The Run steer replay evidence is invalid.",
                category=ErrorCategory.unavailable,
            ) from error
        return SteerReceipt(
            session_id=status.session_id,
            thread_id=status.thread_id,
            run_id=status.accepted_against_run_id,
            steer_id=status.steer_id,
            delivery_sequence=status.delivery_sequence,
            accepted_at=status.created_at,
        )

    async def _load_steer_source(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        read_only: bool = False,
    ):
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, ThreadRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.organization_id == RunRecord.organization_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.organization_id == RunRecord.organization_id,
                            ThreadRecord.id == RunRecord.thread_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise command_not_found()
            source, thread = row
            # Steer writes were authorized by _steer_replay before input preparation.
            # Receipt reads are independent requests and must authorize here.
            if read_only:
                try:
                    await authorize_interaction(
                        database,
                        actor=actor,
                        workspace_id=actor.workspace_id,
                        session_id=source.session_id,
                        agent_id=source.agent_id,
                        action=WorkspaceAction.run_read,
                    )
                except AuthorizationError as error:
                    raise command_not_found() from error
            if not read_only and (
                thread.current_run_id != source.id
                or source.status
                not in {
                    RunStatus.accepted.value,
                    RunStatus.running.value,
                    RunStatus.waiting.value,
                }
            ):
                raise InteractionCommandError(
                    "run_not_steerable",
                    "The selected Run cannot accept steer input.",
                    category=ErrorCategory.conflict,
                )
            return source.to_resource(), thread.to_resource()

    async def _interrupt_replay(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        scope: EvidenceScope,
        identity: IdempotencyIdentity,
    ) -> InterruptReceipt | None:
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, SessionRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.organization_id == RunRecord.organization_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise command_not_found()
            run, _session = row
            try:
                await authorize_interaction(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    session_id=run.session_id,
                    agent_id=run.agent_id,
                    action=WorkspaceAction.run_interrupt,
                )
                evidence = await load_evidence(database, scope=scope, identity=identity, now=now)
            except AuthorizationError as error:
                raise command_not_found() from error
            except IdempotencyConflict as error:
                raise idempotency_conflict() from error
            if evidence is None:
                return None
            if evidence.result_kind != "run_interrupt" or evidence.result_ref != run.id or run.sealed_at is None:
                raise InteractionCommandError(
                    "idempotency_evidence_invalid",
                    "The Run interruption replay evidence is invalid.",
                    category=ErrorCategory.unavailable,
                )
            return InterruptReceipt(run_id=run.id, interrupted_at=assume_utc(run.sealed_at))

    async def _load_interrupt_source(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
    ):
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, ThreadRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.organization_id == RunRecord.organization_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.organization_id == RunRecord.organization_id,
                            ThreadRecord.id == RunRecord.thread_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise command_not_found()
            source, thread = row
            try:
                await authorize_interaction(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    session_id=source.session_id,
                    agent_id=source.agent_id,
                    action=WorkspaceAction.run_interrupt,
                )
            except AuthorizationError as error:
                raise command_not_found() from error
            if source.status not in {RunStatus.accepted.value, RunStatus.running.value}:
                raise InteractionCommandError(
                    "run_not_interruptible",
                    "The selected Run is not active.",
                    category=ErrorCategory.conflict,
                )
            return source.to_resource(), thread.to_resource()
