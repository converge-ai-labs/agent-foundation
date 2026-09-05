"""Authorized Native interaction resource queries."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    InvalidCollectionCursorError,
    decode_collection_cursor,
    encode_collection_cursor,
)
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_agent_scoped_collection,
    authorize_workspace,
)
from a13n_service.interactions import RunLineageKind, RunStatus
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, SessionRecord, ThreadRecord
from a13n_service.lifecycle import LifecycleEntityType
from a13n_service.lifecycle.reconciliation import load_owning_run
from a13n_service.public_errors import PublicError
from a13n_service.run_stream import RetainedReplayUnavailable, RunReplayIntegrityError, RunReplayStore
from a13n_service.storage import ObjectStoreError, short_session
from a13n_service.temporal import assume_utc, optional_assume_utc


class NativeQueryError(PublicError):
    """Safe Native interaction query failure."""


class _Resource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionResource(_Resource):
    id: str
    workspace_id: str
    created_at: datetime
    updated_at: datetime


class ThreadResource(_Resource):
    id: str
    version: int
    queue_version: int
    session_id: str
    role: str
    origin_kind: str
    origin_thread_id: str | None
    origin_run_id: str | None
    head_run_id: str | None
    current_run_id: str | None
    default_environment_id: str | None
    created_at: datetime
    updated_at: datetime


class RunResource(_Resource):
    id: str
    version: int
    session_id: str
    thread_id: str
    parent_run_id: str | None
    retry_of_run_id: str | None
    lineage_kind: RunLineageKind
    trigger_type: str
    agent_id: str
    agent_revision_id: str
    effective_agent_config_digest: str
    runtime_lock_digest: str
    environment_id: str | None
    environment_access: str | None
    status: RunStatus
    wait_reason: str | None
    input_kind: str
    input: JsonValue | None
    input_text: str | None
    output: JsonValue | None
    output_text: str | None
    failure: JsonValue | None
    pending: JsonValue | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    waiting_at: datetime | None
    completed_at: datetime | None
    sealed_at: datetime | None


class RunAttemptResource(_Resource):
    id: str
    version: int
    run_id: str
    attempt_number: int
    fence: int
    status: str
    replaces_run_attempt_id: str | None
    recovery_reason: str | None
    worker_build_id: str
    harness_run_id: str | None
    yield_reason: str | None
    failure: JsonValue | None
    created_at: datetime
    claimed_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class PendingActionResource(_Resource):
    call_id: str
    kind: str
    tool_name: str | None
    provider_type: str | None
    presentation: JsonValue | None


class ItemResource(_Resource):
    id: str
    kind: str
    state: str
    parent_item_id: str | None
    first_stream_id: str
    last_stream_id: str
    content: JsonValue


class RunLineageEntry(_Resource):
    run_id: str
    session_id: str
    thread_id: str
    parent_run_id: str | None
    lineage_kind: RunLineageKind
    status: RunStatus
    depth_from_head: int
    created_at: datetime


class RunLineage(_Resource):
    head_run_id: str
    items: tuple[RunLineageEntry, ...]


class SessionCollection(_Resource):
    items: tuple[SessionResource, ...]
    next_cursor: str | None


class ThreadCollection(_Resource):
    items: tuple[ThreadResource, ...]
    next_cursor: str | None


class RunCollection(_Resource):
    items: tuple[RunResource, ...]
    next_cursor: str | None


class RunAttemptCollection(_Resource):
    items: tuple[RunAttemptResource, ...]
    next_cursor: str | None


class PendingActionCollection(_Resource):
    items: tuple[PendingActionResource, ...]


class ItemCollection(_Resource):
    items: tuple[ItemResource, ...]
    next_cursor: str | None


class NativeInteractionQueries:
    """Read safe interaction projections with current IAM authority."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], replay: RunReplayStore) -> None:
        self._sessions = sessions
        self._replay = replay

    async def list_sessions(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
    ) -> SessionCollection:
        scope = _scope(actor, "sessions", workspace_id)
        boundary = _cursor_boundary(cursor, scope=scope, kind="sessions")
        async with short_session(self._sessions) as database:
            authorization = await _authorize_collection(
                database, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.session_read
            )
            query = (
                select(SessionRecord)
                .where(
                    SessionRecord.tenant_id == authorization.workspace.organization_id,
                    SessionRecord.workspace_id == workspace_id,
                )
                .order_by(SessionRecord.updated_at.desc(), SessionRecord.id.desc())
                .limit(limit + 1)
            )
            if authorization.visible_agent_ids is not None:
                query = query.where(
                    select(RunRecord.id)
                    .where(
                        RunRecord.tenant_id == SessionRecord.tenant_id,
                        RunRecord.session_id == SessionRecord.id,
                        RunRecord.agent_id.in_(authorization.visible_agent_ids),
                    )
                    .exists()
                )
            if boundary is not None:
                updated_at, resource_id = boundary
                query = query.where(
                    or_(
                        SessionRecord.updated_at < updated_at,
                        and_(SessionRecord.updated_at == updated_at, SessionRecord.id < resource_id),
                    )
                )
            records = tuple((await database.scalars(query)).all())
        page, next_cursor = _page(records, limit=limit, scope=scope, kind="sessions")
        return SessionCollection(items=tuple(_session(item) for item in page), next_cursor=next_cursor)

    async def get_thread(self, *, actor: AuthenticatedActor, thread_id: str) -> ThreadResource:
        async with short_session(self._sessions) as database:
            thread, run, workspace_id = await _load_thread(database, actor=actor, thread_id=thread_id)
            await _authorize_agent(
                database,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=run.agent_id if run else None,
                action=WorkspaceAction.thread_read,
            )
            return _thread(thread)

    async def list_threads(
        self,
        *,
        actor: AuthenticatedActor,
        session_id: str,
        limit: int,
        cursor: str | None,
    ) -> ThreadCollection:
        scope = _scope(actor, "threads", session_id)
        boundary = _cursor_boundary(cursor, scope=scope, kind="threads")
        async with short_session(self._sessions) as database:
            session = await database.scalar(
                select(SessionRecord).where(
                    SessionRecord.id == session_id,
                    SessionRecord.workspace_id == actor.boundary_workspace_id,
                )
            )
            if session is None:
                raise _not_found()
            authorization = await _authorize_collection(
                database,
                actor=actor,
                workspace_id=session.workspace_id,
                action=WorkspaceAction.thread_read,
            )
            query = (
                select(ThreadRecord)
                .outerjoin(
                    RunRecord,
                    and_(
                        RunRecord.tenant_id == ThreadRecord.tenant_id,
                        RunRecord.id == ThreadRecord.current_run_id,
                    ),
                )
                .where(ThreadRecord.tenant_id == session.tenant_id, ThreadRecord.session_id == session.id)
                .order_by(ThreadRecord.updated_at.desc(), ThreadRecord.id.desc())
                .limit(limit + 1)
            )
            if authorization.visible_agent_ids is not None:
                query = query.where(RunRecord.agent_id.in_(authorization.visible_agent_ids))
            if boundary is not None:
                updated_at, resource_id = boundary
                query = query.where(
                    or_(
                        ThreadRecord.updated_at < updated_at,
                        and_(ThreadRecord.updated_at == updated_at, ThreadRecord.id < resource_id),
                    )
                )
            records = tuple((await database.scalars(query)).all())
        page, next_cursor = _page(records, limit=limit, scope=scope, kind="threads")
        return ThreadCollection(items=tuple(_thread(item) for item in page), next_cursor=next_cursor)

    async def get_run(self, *, actor: AuthenticatedActor, run_id: str) -> RunResource:
        async with short_session(self._sessions) as database:
            run = await _load_run(database, actor=actor, run_id=run_id)
            await _authorize_agent(
                database,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                agent_id=run.agent_id,
                action=WorkspaceAction.run_read,
            )
            return _run(run)

    async def list_runs(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        thread_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> RunCollection:
        selected_scope = workspace_id or thread_id
        if selected_scope is None:
            raise ValueError("one Run collection scope is required")
        actual_workspace = actor.boundary_workspace_id
        scope = _scope(actor, "runs", selected_scope)
        boundary = _cursor_boundary(cursor, scope=scope, kind="runs")
        async with short_session(self._sessions) as database:
            if thread_id is not None:
                _, current_run, thread_workspace_id = await _load_thread(
                    database,
                    actor=actor,
                    thread_id=thread_id,
                )
                await _authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=thread_workspace_id,
                    agent_id=current_run.agent_id if current_run else None,
                    action=WorkspaceAction.run_read,
                )
            authorization = await _authorize_collection(
                database,
                actor=actor,
                workspace_id=actual_workspace,
                action=WorkspaceAction.run_read,
            )
            query = select(RunRecord).join(
                SessionRecord,
                and_(
                    SessionRecord.tenant_id == RunRecord.tenant_id,
                    SessionRecord.id == RunRecord.session_id,
                ),
            )
            if workspace_id is not None:
                if workspace_id != actual_workspace:
                    raise _not_found()
                query = query.where(SessionRecord.workspace_id == workspace_id)
            else:
                query = query.where(
                    SessionRecord.workspace_id == actual_workspace,
                    RunRecord.thread_id == thread_id,
                )
            if authorization.visible_agent_ids is not None:
                query = query.where(RunRecord.agent_id.in_(authorization.visible_agent_ids))
            query = query.order_by(RunRecord.created_at.desc(), RunRecord.id.desc()).limit(limit + 1)
            if boundary is not None:
                created_at, resource_id = boundary
                query = query.where(
                    or_(
                        RunRecord.created_at < created_at,
                        and_(RunRecord.created_at == created_at, RunRecord.id < resource_id),
                    )
                )
            records = tuple((await database.scalars(query)).all())
        page, next_cursor = _page(records, limit=limit, scope=scope, kind="runs", timestamp="created_at")
        return RunCollection(items=tuple(_run(item) for item in page), next_cursor=next_cursor)

    async def list_attempts(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        limit: int,
        cursor: str | None,
    ) -> RunAttemptCollection:
        scope = _scope(actor, "run_attempts", run_id)
        boundary = _cursor_boundary(cursor, scope=scope, kind="run_attempts")
        async with short_session(self._sessions) as database:
            run = await _load_run(database, actor=actor, run_id=run_id)
            await _authorize_agent(
                database,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                agent_id=run.agent_id,
                action=WorkspaceAction.run_read,
            )
            query = (
                select(RunAttemptRecord)
                .where(RunAttemptRecord.tenant_id == run.tenant_id, RunAttemptRecord.run_id == run.id)
                .order_by(RunAttemptRecord.created_at.desc(), RunAttemptRecord.id.desc())
                .limit(limit + 1)
            )
            if boundary is not None:
                created_at, resource_id = boundary
                query = query.where(
                    or_(
                        RunAttemptRecord.created_at < created_at,
                        and_(RunAttemptRecord.created_at == created_at, RunAttemptRecord.id < resource_id),
                    )
                )
            records = tuple((await database.scalars(query)).all())
        page, next_cursor = _page(
            records,
            limit=limit,
            scope=scope,
            kind="run_attempts",
            timestamp="created_at",
        )
        return RunAttemptCollection(items=tuple(_attempt(item) for item in page), next_cursor=next_cursor)

    async def get_attempt(self, *, actor: AuthenticatedActor, run_attempt_id: str) -> RunAttemptResource:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunAttemptRecord, RunRecord)
                    .join(
                        RunRecord,
                        and_(
                            RunRecord.tenant_id == RunAttemptRecord.tenant_id,
                            RunRecord.id == RunAttemptRecord.run_id,
                        ),
                    )
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.tenant_id == RunRecord.tenant_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .where(
                        RunAttemptRecord.id == run_attempt_id,
                        SessionRecord.workspace_id == actor.boundary_workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise _not_found()
            attempt, run = row
            await _authorize_agent(
                database,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                agent_id=run.agent_id,
                action=WorkspaceAction.run_read,
            )
            return _attempt(attempt)

    async def pending_actions(self, *, actor: AuthenticatedActor, run_id: str) -> PendingActionCollection:
        async with short_session(self._sessions) as database:
            run = await _load_run(database, actor=actor, run_id=run_id)
            await _authorize_agent(
                database,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                agent_id=run.agent_id,
                action=WorkspaceAction.run_read,
            )
            resource = run.to_resource()
        if resource.pending is None:
            return PendingActionCollection(items=())
        return PendingActionCollection(
            items=tuple(
                PendingActionResource(
                    call_id=item.call_id,
                    kind=item.kind.value,
                    tool_name=item.tool_name,
                    provider_type=item.provider_type,
                    presentation=item.presentation,
                )
                for item in resource.pending.calls
            )
        )

    async def items(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        limit: int,
        cursor: str | None,
    ) -> ItemCollection:
        scope = _scope(actor, "items", run_id)
        offset = _offset_cursor(cursor, scope=scope, kind="items")
        async with short_session(self._sessions) as database:
            run = await _load_run(database, actor=actor, run_id=run_id)
            await _authorize_agent(
                database,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                agent_id=run.agent_id,
                action=WorkspaceAction.run_read,
            )
            tenant_id = run.tenant_id
        try:
            snapshot = await self._replay.read(tenant_id, run_id)
        except (ObjectStoreError, RetainedReplayUnavailable, RunReplayIntegrityError) as error:
            raise NativeQueryError(
                "items_unavailable",
                "Retained Items are unavailable for this Run.",
                status_code=409,
            ) from error
        values = snapshot.items[offset : offset + limit + 1]
        page = values[:limit]
        next_cursor = (
            encode_collection_cursor({"offset": offset + limit}, scope=scope, kind="items")
            if len(values) > limit
            else None
        )
        return ItemCollection(
            items=tuple(ItemResource.model_validate(item.model_dump()) for item in page),
            next_cursor=next_cursor,
        )

    async def lineage(self, *, actor: AuthenticatedActor, run_id: str) -> RunLineage:
        async with short_session(self._sessions) as database:
            current = await _load_run(database, actor=actor, run_id=run_id)
            reverse: list[RunRecord] = []
            visited: set[str] = set()
            for _ in range(1000):
                if current.id in visited:
                    raise _lineage_invalid("cycle")
                visited.add(current.id)
                await _authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=current.agent_id,
                    action=WorkspaceAction.run_read,
                )
                reverse.append(current)
                if current.parent_run_id is None:
                    break
                parent = await database.scalar(
                    select(RunRecord).where(
                        RunRecord.tenant_id == current.tenant_id,
                        RunRecord.id == current.parent_run_id,
                    )
                )
                if parent is None:
                    raise _lineage_invalid("missing_parent")
                current = parent
            else:
                raise _lineage_invalid("max_depth")
        total = len(reverse)
        return RunLineage(
            head_run_id=run_id,
            items=tuple(
                RunLineageEntry(
                    run_id=record.id,
                    session_id=record.session_id,
                    thread_id=record.thread_id,
                    parent_run_id=record.parent_run_id,
                    lineage_kind=RunLineageKind(record.lineage_kind),
                    status=RunStatus(record.status),
                    depth_from_head=total - index - 1,
                    created_at=assume_utc(record.created_at),
                )
                for index, record in enumerate(reversed(reverse))
            ),
        )


async def _load_run(database: AsyncSession, *, actor: AuthenticatedActor, run_id: str) -> RunRecord:
    run = await load_owning_run(
        database,
        workspace_id=actor.boundary_workspace_id,
        resource_type=LifecycleEntityType.run,
        resource_id=run_id,
    )
    if run is None:
        raise _not_found()
    return run


async def _load_thread(
    database: AsyncSession, *, actor: AuthenticatedActor, thread_id: str
) -> tuple[ThreadRecord, RunRecord | None, str]:
    row = (
        await database.execute(
            select(ThreadRecord, RunRecord, SessionRecord.workspace_id)
            .join(
                SessionRecord,
                and_(
                    SessionRecord.tenant_id == ThreadRecord.tenant_id,
                    SessionRecord.id == ThreadRecord.session_id,
                ),
            )
            .outerjoin(
                RunRecord,
                and_(
                    RunRecord.tenant_id == ThreadRecord.tenant_id,
                    RunRecord.id == ThreadRecord.current_run_id,
                ),
            )
            .where(ThreadRecord.id == thread_id, SessionRecord.workspace_id == actor.boundary_workspace_id)
        )
    ).one_or_none()
    if row is None:
        raise _not_found()
    thread, run, workspace_id = row
    return thread, run, workspace_id


async def _authorize_agent(
    database: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    agent_id: str | None,
    action: WorkspaceAction,
) -> None:
    try:
        if agent_id is None:
            await authorize_workspace(database, actor=actor, workspace_id=workspace_id, action=action)
            return
        await authorize_agent(
            database,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=agent_id,
            action=action,
        )
    except AuthorizationError as error:
        raise _not_found() from error


async def _authorize_collection(database: AsyncSession, **kwargs):
    try:
        return await authorize_agent_scoped_collection(database, **kwargs)
    except AuthorizationError as error:
        raise _not_found() from error


def _session(record: SessionRecord) -> SessionResource:
    return SessionResource(
        id=record.id,
        workspace_id=record.workspace_id,
        created_at=assume_utc(record.created_at),
        updated_at=assume_utc(record.updated_at),
    )


def _thread(record: ThreadRecord) -> ThreadResource:
    return ThreadResource(
        id=record.id,
        version=record.version,
        queue_version=record.queue_version,
        session_id=record.session_id,
        role=record.role,
        origin_kind=record.origin_kind,
        origin_thread_id=record.origin_thread_id,
        origin_run_id=record.origin_run_id,
        head_run_id=record.head_run_id,
        current_run_id=record.current_run_id,
        default_environment_id=record.default_environment_id,
        created_at=assume_utc(record.created_at),
        updated_at=assume_utc(record.updated_at),
    )


def _run(record: RunRecord) -> RunResource:
    resource = record.to_resource()
    return RunResource(
        id=resource.id,
        version=resource.version,
        session_id=resource.session_id,
        thread_id=resource.thread_id,
        parent_run_id=resource.parent_run_id,
        retry_of_run_id=resource.retry_of_run_id,
        lineage_kind=resource.lineage_kind,
        trigger_type=resource.trigger_type,
        agent_id=resource.agent_id,
        agent_revision_id=resource.agent_revision_id,
        effective_agent_config_digest=resource.effective_agent_config_digest,
        runtime_lock_digest=resource.runtime_lock_digest,
        environment_id=resource.environment_id,
        environment_access=resource.environment_access,
        status=resource.status,
        wait_reason=None if resource.wait_reason is None else resource.wait_reason.value,
        input_kind=resource.input_kind.value,
        input=resource.input,
        input_text=resource.input_text,
        output=resource.output,
        output_text=resource.output_text,
        failure=None if resource.failure is None else resource.failure.model_dump(mode="json"),
        pending=None if resource.pending is None else resource.pending.model_dump(mode="json"),
        created_at=resource.created_at,
        updated_at=resource.updated_at,
        started_at=resource.started_at,
        waiting_at=resource.waiting_at,
        completed_at=resource.completed_at,
        sealed_at=resource.sealed_at,
    )


def _attempt(record: RunAttemptRecord) -> RunAttemptResource:
    return RunAttemptResource(
        id=record.id,
        version=record.version,
        run_id=record.run_id,
        attempt_number=record.attempt_number,
        fence=record.fence,
        status=record.status,
        replaces_run_attempt_id=record.replaces_run_attempt_id,
        recovery_reason=record.recovery_reason,
        worker_build_id=record.worker_build_id,
        harness_run_id=record.harness_run_id,
        yield_reason=record.yield_reason,
        failure=record.failure_json,
        created_at=assume_utc(record.created_at),
        claimed_at=assume_utc(record.claimed_at),
        started_at=optional_assume_utc(record.started_at),
        finished_at=optional_assume_utc(record.finished_at),
        updated_at=assume_utc(record.updated_at),
    )


def _scope(actor: AuthenticatedActor, resource: str, parent: str) -> dict[str, object]:
    return {
        "resource": resource,
        "parent": parent,
        "principal_type": actor.principal.principal_type.value,
        "principal_id": actor.principal.principal_id,
    }


def _cursor_boundary(cursor: str | None, *, scope: dict[str, object], kind: str) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    try:
        payload = decode_collection_cursor(cursor, scope=scope, kind=kind)
        timestamp = datetime.fromisoformat(str(payload["timestamp"]))
        resource_id = str(payload["id"])
        if timestamp.tzinfo is None or not resource_id:
            raise ValueError
        return timestamp, resource_id
    except (InvalidCollectionCursorError, CollectionCursorMismatchError, KeyError, ValueError) as error:
        raise NativeQueryError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error


def _offset_cursor(cursor: str | None, *, scope: dict[str, object], kind: str) -> int:
    if cursor is None:
        return 0
    try:
        payload = decode_collection_cursor(cursor, scope=scope, kind=kind)
        offset = payload["offset"]
        if not isinstance(offset, int) or offset < 0:
            raise ValueError
        return offset
    except (InvalidCollectionCursorError, CollectionCursorMismatchError, KeyError, ValueError) as error:
        raise NativeQueryError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error


def _page(
    records: tuple,
    *,
    limit: int,
    scope: dict[str, object],
    kind: str,
    timestamp: Literal["created_at", "updated_at"] = "updated_at",
) -> tuple[tuple, str | None]:
    page = records[:limit]
    if len(records) <= limit:
        return page, None
    last = page[-1]
    value = assume_utc(getattr(last, timestamp))
    return page, encode_collection_cursor(
        {"timestamp": value.isoformat(), "id": last.id},
        scope=scope,
        kind=kind,
    )


def _not_found() -> NativeQueryError:
    return NativeQueryError("resource_not_found", "The requested resource was not found.", status_code=404)


def _lineage_invalid(reason: str) -> NativeQueryError:
    return NativeQueryError(
        "run_lineage_invalid",
        "The Run lineage is invalid.",
        status_code=409,
        details={"reason": reason},
    )


__all__ = [
    "ItemCollection",
    "NativeInteractionQueries",
    "NativeQueryError",
    "PendingActionCollection",
    "RunAttemptCollection",
    "RunAttemptResource",
    "RunCollection",
    "RunLineage",
    "RunResource",
    "SessionCollection",
    "ThreadCollection",
    "ThreadResource",
]
