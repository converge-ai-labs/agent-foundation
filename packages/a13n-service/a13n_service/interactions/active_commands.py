"""Interrupt and steer accepted work without creating another Run."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from a13n_harness import SafeFailure
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.durable_operations.entity_keys import scope_key
from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyIdentity,
)
from a13n_service.durable_operations.requests import request_scope
from a13n_service.iam import (
    AuthenticatedActor,
    WorkspaceAction,
)
from a13n_service.iam.operation import authorization_operation
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
from a13n_service.interactions.objects import RunObjectError, RunStateStore
from a13n_service.interactions.origin import SubmissionOrigin
from a13n_service.interactions.outcomes import RunOutcomeError, RunOutcomeService
from a13n_service.storage import ObjectStoreError, short_session
from a13n_service.storage.relational import is_unique_conflict
from a13n_service.temporal import Clock, assume_utc, utc_now

from .access import authorize_run_actions
from .command_evidence import (
    command_identity,
)
from .command_preparation import CommandInput
from .control_domain import InterruptReceipt
from .errors import InteractionCommandError, command_not_found
from .models import RunRecord
from .sources import RunSource, load_run_source
from .steer_idempotency import SteerIdempotency, find_steer, steer_receipt

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
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._states = states
        self._outcomes = outcomes
        self._inbox = inbox
        self._inputs = inputs
        self._clock = clock

    @authorization_operation
    async def interrupt(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        request: InterruptRequest,
    ) -> InterruptReceipt:
        identity = command_identity(idempotency_key)
        scope = request_scope(actor, workspace_id=actor.workspace_id, operation="run.interrupt", scope_id=run_id)
        observed, replay = await self._interrupt_replay(actor=actor, run_id=run_id, scope=scope, identity=identity)
        if replay is not None:
            return replay
        source, thread, session_scope = observed.run, observed.thread, observed.session_scope
        if source.status not in {RunStatus.accepted, RunStatus.running}:
            raise InteractionCommandError(
                "run_not_interruptible", "The selected Run is not active.", category=ErrorCategory.conflict
            )
        if source.version != request.expected_run_version or thread.version != request.expected_thread_version:
            raise InteractionCommandError(
                "run_precondition_changed",
                "The Run or Thread version changed before interruption.",
                category=ErrorCategory.conflict,
            )

        async def validate_final(database: AsyncSession) -> None:
            await authorize_run_actions(
                database,
                actor=actor,
                source=source,
                session_scope=session_scope,
                actions=(WorkspaceAction.run_interrupt,),
            )

            record = await database.get(RunRecord, run_id)
            if record is None:
                raise command_not_found()
            record.interrupt_key = scope_key(scope, identity.key_digest)

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
            )
        except IntegrityError as error:
            observed, replay = await self._interrupt_replay(actor=actor, run_id=run_id, scope=scope, identity=identity)
            if replay is not None:
                return replay
            raise InteractionCommandError(
                "run_interrupt_conflict",
                "Run interruption lost a concurrent mutation.",
                category=ErrorCategory.conflict,
            ) from error
        except RunOutcomeError as error:
            observed, replay = await self._interrupt_replay(actor=actor, run_id=run_id, scope=scope, identity=identity)
            if replay is not None:
                return replay
            raise InteractionCommandError(
                "run_interrupt_conflict",
                "The Run can no longer be interrupted.",
                category=ErrorCategory.conflict,
            ) from error
        assert outcome.sealed_at is not None
        return InterruptReceipt(run_id=run_id, interrupted_at=outcome.sealed_at)

    @authorization_operation
    async def steer(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        input: AgentInput,
        transaction_hook: Callable[[AsyncSession, SteerReceipt], Awaitable[None]] | None = None,
    ) -> SteerReceipt:
        idempotency = SteerIdempotency(actor.principal, command_identity(idempotency_key))
        observed, replay = await self._steer_replay(actor=actor, run_id=run_id, idempotency=idempotency)
        if replay is not None:
            return replay
        source = observed.run

        async def replay_prepared() -> SteerReceipt | None:
            # Preserve this request's authorization when recovering a concurrent acceptance.
            async with short_session(self._sessions) as database:
                row = await find_steer(
                    database,
                    organization_id=source.organization_id,
                    run_id=run_id,
                    idempotency=idempotency,
                    now=assume_utc(self._clock()),
                )
                return None if row is None else steer_receipt(row, session_id=source.session_id)

        try:
            state = await self._states.read_run(source)
            accepted = await self._inputs.accept_effective(
                actor=actor,
                workspace_id=actor.workspace_id,
                submitted=input,
                effective=state.envelope.effective_agent_config,
                environment_available=source.environment_id is not None,
                retained_secret_bindings=state.envelope.secret_bindings,
            )
        except (ObjectStoreError, RunObjectError, InteractionCommandError) as error:
            # Another same-key request may have committed while this one prepared input.
            replay = await replay_prepared()
            if replay is not None:
                return replay
            if isinstance(error, InteractionCommandError):
                raise
            raise InteractionCommandError(
                "run_state_unavailable",
                "The selected Run state is unavailable.",
                category=ErrorCategory.conflict,
            ) from error
        try:
            return await self._inbox.append_steer(
                organization_id=source.organization_id,
                run_id=source.id,
                input=accepted,
                idempotency=idempotency,
                transaction_hook=transaction_hook,
            )
        except IntegrityError as error:
            if is_unique_conflict(error, constraint="uq_thread_inbox_steer_idempotency"):
                replay = await replay_prepared()
                if replay is not None:
                    return replay
            raise InteractionCommandError(
                "run_steer_conflict", "Run steering lost a concurrent mutation.", category=ErrorCategory.conflict
            ) from error
        except ThreadInboxConflict as error:
            raise InteractionCommandError(
                "run_steer_conflict",
                "The selected Run can no longer accept steer input.",
                category=ErrorCategory.conflict,
            ) from error

    @authorization_operation
    async def get_steer(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        steer_id: str,
    ) -> SteerStatus:
        async with short_session(self._sessions) as database:
            observed = await load_run_source(database, workspace_id=actor.workspace_id, run_id=run_id)
            source = observed.run
            await authorize_run_actions(
                database,
                actor=actor,
                source=source,
                session_scope=observed.session_scope,
                actions=(WorkspaceAction.run_read,),
            )
        try:
            return await self._inbox.get_steer(organization_id=source.organization_id, run_id=run_id, steer_id=steer_id)
        except ThreadInboxConflict as error:
            raise command_not_found() from error

    async def _steer_replay(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency: SteerIdempotency,
    ) -> tuple[RunSource, SteerReceipt | None]:
        now = assume_utc(self._clock())
        async with short_session(self._sessions) as database:
            observed = await load_run_source(database, workspace_id=actor.workspace_id, run_id=run_id)
            run = observed.run
            await authorize_run_actions(
                database,
                actor=actor,
                source=run,
                session_scope=observed.session_scope,
                actions=(WorkspaceAction.run_steer,),
                reuse_request_authentication=True,
            )
            entry = await find_steer(
                database,
                organization_id=run.organization_id,
                run_id=run_id,
                idempotency=idempotency,
                now=now,
            )
            return observed, None if entry is None else steer_receipt(entry, session_id=run.session_id)

    async def _interrupt_replay(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        scope: EvidenceScope,
        identity: IdempotencyIdentity,
    ) -> tuple[RunSource, InterruptReceipt | None]:
        async with short_session(self._sessions) as database:
            observed = await load_run_source(database, workspace_id=actor.workspace_id, run_id=run_id)
            run = observed.run
            await authorize_run_actions(
                database,
                actor=actor,
                source=run,
                session_scope=observed.session_scope,
                actions=(WorkspaceAction.run_interrupt,),
            )
            if observed.interrupt_key != scope_key(scope, identity.key_digest):
                return observed, None
            if run.sealed_at is None:
                raise InteractionCommandError(
                    "idempotency_evidence_invalid",
                    "The Run interruption replay evidence is invalid.",
                    category=ErrorCategory.unavailable,
                )
            return observed, InterruptReceipt(run_id=run.id, interrupted_at=assume_utc(run.sealed_at))
