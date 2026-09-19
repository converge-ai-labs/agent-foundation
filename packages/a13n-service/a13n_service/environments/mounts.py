"""Append-only Run mount acceptance, original receipts, and bounded reads."""

from __future__ import annotations

import logging
from datetime import datetime

from a13n_harness.providers.environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.durable_operations.idempotency import IdempotencyConflict, IdempotencyIdentity
from a13n_service.durable_operations.requests import evidence_record, load_receipt, request_identity, request_scope
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction
from a13n_service.iam.authorization import authorize_persisted_workspace_principal_action
from a13n_service.iam.resource_scope import actor_scope
from a13n_service.interactions.access import authorize_interaction, authorize_retained_execution
from a13n_service.interactions.errors import command_not_found, idempotency_conflict
from a13n_service.interactions.inbox import ThreadControlSignalPublisher
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, SessionRecord, ThreadRecord
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, assume_utc, next_updated_at, require_aware_utc, utc_now

from .access import authorize_environment_resource
from .cursors import decode_cursor, encode_cursor
from .domain import Collection, ExistingEnvironmentSelection
from .errors import EnvironmentManagementError, invalid_environment
from .models import EnvironmentProviderRecord, EnvironmentRecord
from .mount_domain import AddEnvironmentMountRequest, RunEnvironmentMount
from .mount_models import RunEnvironmentMountRecord
from .selection import intersect_access, resolve_selection
from .websocket.admission import OnlineAdmission, OnlineEvidence

logger = logging.getLogger("a13n_service.environments.mounts")
_OPERATION = "run.environment_mount.add"


class RunEnvironmentMountService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        admission: OnlineAdmission,
        *,
        signals: ThreadControlSignalPublisher | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._admission = admission
        self._signals = signals
        self._clock = clock

    async def add(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        request: AddEnvironmentMountRequest,
    ) -> RunEnvironmentMount:
        identity = request_identity(idempotency_key, request)

        async def accept(database: AsyncSession, online: OnlineEvidence) -> tuple[RunEnvironmentMount, str, str]:
            run, workspace_id = await _load_run(database, actor, run_id, WorkspaceAction.run_steer)
            # All mount names and lifecycle changes serialize behind Thread -> Run.
            thread = await database.get(ThreadRecord, run.thread_id, with_for_update=True, populate_existing=True)
            run, workspace_id = await _load_run(database, actor, run_id, WorkspaceAction.run_steer, lock=True)
            now = assume_utc(self._clock())
            replay = await _replay(database, actor, workspace_id, run_id, identity, now)
            if replay is not None:
                return replay, run.organization_id, run.thread_id
            if thread is None or thread.current_run_id != run.id or run.status not in {"accepted", "running"}:
                raise _conflict("run_not_mountable", "Only the current accepted or running Run accepts mounts.")
            if await database.get(RunEnvironmentMountRecord, (run_id, request.name)) is not None:
                raise _conflict("environment_mount_conflict", "The Run already has a mount with this name.")
            await authorize_retained_execution(database, source=run.to_resource(), workspace_id=workspace_id)
            await authorize_persisted_workspace_principal_action(
                database,
                principal=run.to_resource().authority_principal,
                organization_id=run.organization_id,
                workspace_id=workspace_id,
                action=WorkspaceAction.environment_use,
            )
            await authorize_environment_resource(
                database,
                actor=actor,
                organization_id=run.organization_id,
                workspace_id=workspace_id,
                action=WorkspaceAction.environment_use,
            )
            selected = await resolve_selection(
                database,
                workspace_id=workspace_id,
                choice=ExistingEnvironmentSelection(environment_id=request.environment_id),
            )
            if not isinstance(selected, EnvironmentRecord):
                raise TypeError("An existing Environment selection must resolve to an Environment")
            # Lifecycle commands also lock this row. Refresh after any lock wait.
            environment = await database.get(
                EnvironmentRecord, selected.id, with_for_update=True, populate_existing=True
            )
            if environment is None:
                raise invalid_environment("Environment was removed before mount acceptance")
            provider = await database.get(EnvironmentProviderRecord, environment.provider_id, populate_existing=True)
            if provider is None or not provider.enabled:
                raise invalid_environment("Environment Provider is unavailable")
            if environment.operation_id is not None or (
                environment.ownership == "external" and environment.status == "deleted"
            ):
                raise _conflict("environment_unavailable", "Environment is unavailable for mount acceptance.")
            if intersect_access(request.access.value, environment.access) != request.access.value:
                raise invalid_environment("Mount access exceeds the Environment access ceiling")
            if provider.type == WEBSOCKET_PROVIDER_KEY:
                online.require(run.organization_id, environment.id)
            previous = await database.scalar(
                select(func.max(RunEnvironmentMountRecord.created_at)).where(RunEnvironmentMountRecord.run_id == run_id)
            )
            created_at = next_updated_at(previous, now) if previous is not None else now
            receipt = RunEnvironmentMount(
                run_id=run_id,
                name=request.name,
                environment_id=environment.id,
                access=request.access,
                created_at=created_at,
                accepting_principal=actor.principal,
            )
            database.add(
                RunEnvironmentMountRecord(
                    run_id=run_id,
                    name=request.name,
                    organization_id=run.organization_id,
                    workspace_id=workspace_id,
                    environment_id=environment.id,
                    access=request.access.value,
                    created_at=created_at,
                    principal_type=actor.principal.principal_type.value,
                    principal_id=actor.principal.principal_id,
                    application_status="pending",
                )
            )
            database.add(
                evidence_record(
                    actor=actor,
                    organization_id=run.organization_id,
                    workspace_id=workspace_id,
                    operation=_OPERATION,
                    scope_id=run_id,
                    identity=identity,
                    result_kind="run_environment_mount",
                    result_ref=request.name,
                    now=now,
                    response=receipt,
                )
            )
            return receipt, run.organization_id, run.thread_id

        try:
            receipt, organization_id, thread_id = await self._admission.commit(accept)
        except EnvironmentManagementError:
            # Another caller may commit the same request while we observe Redis.
            # Even an offline response must yield to that original receipt.
            async with short_session(self._sessions) as database:
                run, workspace_id = await _load_run(database, actor, run_id, WorkspaceAction.run_steer)
                replay = await _replay(database, actor, workspace_id, run_id, identity, assume_utc(self._clock()))
            if replay is None:
                raise
            receipt, organization_id, thread_id = replay, run.organization_id, run.thread_id
        if self._signals is not None:
            try:
                await self._signals.publish(organization_id=organization_id, thread_id=thread_id)
            except Exception:
                logger.warning(
                    "environment_mount_signal_failed",
                    extra={"event": "environment_mount_signal_failed", "run_id": run_id},
                    exc_info=True,
                )
        return receipt

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Collection[RunEnvironmentMount]:
        if not 1 <= limit <= 100:
            raise invalid_environment("Mount list limit must be between 1 and 100")
        async with short_session(self._sessions) as database:
            _, workspace_id = await _load_run(database, actor, run_id, WorkspaceAction.run_read)
            scope = {"collection": "run_environment_mounts", "workspace_id": workspace_id, "run_id": run_id}
            query = (
                select(RunEnvironmentMountRecord, RunAttemptRecord)
                .join(RunRecord, RunRecord.id == RunEnvironmentMountRecord.run_id)
                .outerjoin(RunAttemptRecord, RunAttemptRecord.id == RunRecord.current_run_attempt_id)
                .where(RunEnvironmentMountRecord.run_id == run_id)
            )
            if cursor is not None:
                try:
                    position = require_aware_utc(datetime.fromisoformat(decode_cursor(cursor, scope=scope)))
                except ValueError as error:
                    raise invalid_environment("Mount cursor is invalid") from error
                query = query.where(RunEnvironmentMountRecord.created_at > position)
            rows = (await database.execute(query.order_by(RunEnvironmentMountRecord.created_at).limit(limit + 1))).all()
            return Collection(
                items=tuple(_project(row, attempt) for row, attempt in rows[:limit]),
                next_cursor=encode_cursor(assume_utc(rows[limit - 1][0].created_at).isoformat(), scope=scope)
                if len(rows) > limit
                else None,
            )


async def _load_run(
    database: AsyncSession,
    actor: AuthenticatedActor,
    run_id: str,
    action: WorkspaceAction,
    *,
    lock: bool = False,
) -> tuple[RunRecord, str]:
    try:
        scope = await actor_scope(database, actor)
        query = (
            select(RunRecord, SessionRecord.workspace_id)
            .join(SessionRecord, RunRecord.session_id == SessionRecord.id)
            .where(RunRecord.id == run_id, RunRecord.organization_id == scope.organization_id)
        )
        if scope.workspace_id is not None:
            query = query.where(SessionRecord.workspace_id == scope.workspace_id)
        if lock:
            query = query.with_for_update(of=RunRecord).execution_options(populate_existing=True)
        result = (await database.execute(query)).one_or_none()
        if result is None:
            raise command_not_found()
        run, workspace_id = result
        await authorize_interaction(
            database,
            actor=actor,
            workspace_id=workspace_id,
            session_id=run.session_id,
            agent_id=run.agent_id,
            action=action,
        )
        return run, workspace_id
    except AuthorizationError as error:
        raise command_not_found() from error


async def _replay(
    database: AsyncSession,
    actor: AuthenticatedActor,
    workspace_id: str,
    run_id: str,
    identity: IdempotencyIdentity,
    now: datetime,
) -> RunEnvironmentMount | None:
    try:
        receipt = await load_receipt(
            database,
            scope=request_scope(actor, workspace_id=workspace_id, operation=_OPERATION, scope_id=run_id),
            identity=identity,
            now=now,
        )
    except IdempotencyConflict as error:
        raise idempotency_conflict() from error
    return receipt.restore(RunEnvironmentMount) if receipt is not None else None


def _project(row: RunEnvironmentMountRecord, attempt: RunAttemptRecord | None) -> RunEnvironmentMount:
    current = (
        attempt is not None
        and row.applied_attempt_id == attempt.id
        and row.applied_attempt_fence == attempt.attempt_number
    )
    return RunEnvironmentMount.model_validate(
        {
            "run_id": row.run_id,
            "name": row.name,
            "environment_id": row.environment_id,
            "access": row.access,
            "created_at": row.created_at,
            "accepting_principal": {"principal_type": row.principal_type, "principal_id": row.principal_id},
            "use_started_at": row.use_started_at,
            "applied_attempt_id": row.applied_attempt_id if current else None,
            "applied_attempt_fence": row.applied_attempt_fence if current else None,
            "application_status": row.application_status if current else "pending",
            "observed_at": row.observed_at if current else None,
            "error": row.error if current else None,
        }
    )


def _conflict(code: str, message: str) -> EnvironmentManagementError:
    return EnvironmentManagementError(code, message, category=ErrorCategory.conflict)
