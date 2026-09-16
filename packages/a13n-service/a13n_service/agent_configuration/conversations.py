"""Configuration projections over canonical Session and Thread identities."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import StrictModel
from a13n_service.application_errors import ErrorCategory
from a13n_service.collection_cursors import decode_time_cursor, encode_time_cursor
from a13n_service.durable_operations.requests import evidence_record, load_replay, request_identity
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction
from a13n_service.iam.authorization import read_principal_permissions
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.interactions.domain import Thread, new_session_id, new_thread_id
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .authorization import authorize_session, authorize_target
from .context import ConfigurationApplicationReceipt
from .domain import ConfigurationDraft
from .models import ConfigurationDraftRecord
from .persistence import create_draft, failure, not_found
from .requests import CreateConfigurationThreadRequest, CreateSessionRequest


class ConfigurationSessionView(StrictModel):
    id: str
    organization_id: str
    workspace_id: str
    owner_user_id: str
    target_agent_id: str | None
    root_thread_id: str
    created_at: datetime
    updated_at: datetime


class ConfigurationThreadView(StrictModel):
    thread: Thread
    active_draft_id: str | None
    latest_draft: ConfigurationDraft
    previous_application_receipt: ConfigurationApplicationReceipt | None


class ConfigurationSessionCollection(StrictModel):
    items: tuple[ConfigurationSessionView, ...]
    next_cursor: str | None


class ConfigurationThreadCollection(StrictModel):
    items: tuple[ConfigurationThreadView, ...]
    next_cursor: str | None


class ConfigurationConversations:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, clock: Clock = utc_now) -> None:
        self._sessions, self._clock = sessions, clock

    async def create_session(
        self,
        *,
        actor: AuthenticatedActor,
        request: CreateSessionRequest,
        idempotency_key: str,
    ) -> ConfigurationSessionView:
        identity = request_identity(idempotency_key, request)
        async with transaction(self._sessions, sqlite_immediate=True) as session:
            try:
                await authorize_target(session, actor=actor, target_agent_id=request.target_agent_id)
            except AuthorizationError as error:
                raise not_found() from error
            replay = await load_replay(
                session,
                actor=actor,
                operation="configuration.session.create",
                scope_id=actor.workspace_id,
                identity=identity,
                now=self._clock(),
            )
            if replay is not None:
                return replay.restore(ConfigurationSessionView)
            workspace = await session.get(WorkspaceRecord, actor.workspace_id)
            assert workspace is not None
            now = self._clock()
            conversation = SessionRecord(
                id=new_session_id(),
                organization_id=workspace.organization_id,
                workspace_id=workspace.id,
                configuration_owner_user_id=actor.principal.principal_id,
                configuration_target_agent_id=request.target_agent_id,
                labels={},
                created_at=now,
                updated_at=now,
            )
            session.add(conversation)
            await session.flush()
            thread = ThreadRecord(
                id=new_thread_id(),
                organization_id=workspace.organization_id,
                session_id=conversation.id,
                version=1,
                queue_version=0,
                role="root",
                origin_kind="new",
                labels={},
                created_at=now,
                updated_at=now,
            )
            session.add(thread)
            await session.flush()
            await create_draft(session, conversation=conversation, thread=thread, source=request.source, now=now)
            result = session_view(conversation, root_thread_id=thread.id)
            session.add(
                evidence_record(
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.id,
                    operation="configuration.session.create",
                    scope_id=workspace.id,
                    identity=identity,
                    result_kind="session",
                    result_ref=conversation.id,
                    now=now,
                    response=result,
                )
            )
            await session.flush()
            return result

    async def get_session(self, *, actor: AuthenticatedActor, session_id: str) -> ConfigurationSessionView:
        async with short_session(self._sessions) as session:
            try:
                conversation = await authorize_session(
                    session, actor=actor, session_id=session_id, write=False, action=WorkspaceAction.session_read
                )
            except AuthorizationError as error:
                raise not_found() from error
            root = await session.scalar(
                select(ThreadRecord.id).where(ThreadRecord.session_id == session_id, ThreadRecord.role == "root")
            )
            if root is None:
                raise not_found()
            return session_view(conversation, root_thread_id=root)

    async def get_thread(self, *, actor: AuthenticatedActor, thread_id: str) -> ConfigurationThreadView:
        async with short_session(self._sessions) as session:
            thread = await session.get(ThreadRecord, thread_id)
            if thread is None:
                raise not_found()
            try:
                await authorize_session(
                    session, actor=actor, session_id=thread.session_id, write=False, action=WorkspaceAction.thread_read
                )
            except AuthorizationError as error:
                raise not_found() from error
            return await thread_view(session, thread)

    async def create_thread(
        self,
        *,
        actor: AuthenticatedActor,
        session_id: str,
        request: CreateConfigurationThreadRequest,
        idempotency_key: str,
    ) -> ConfigurationThreadView:
        identity = request_identity(idempotency_key, request)
        async with transaction(self._sessions, sqlite_immediate=True) as session:
            try:
                conversation = await authorize_session(session, actor=actor, session_id=session_id, lock=True)
            except AuthorizationError as error:
                raise not_found() from error
            replay = await load_replay(
                session,
                actor=actor,
                operation="configuration.thread.create",
                scope_id=session_id,
                identity=identity,
                now=self._clock(),
            )
            if replay is not None:
                return replay.restore(ConfigurationThreadView)
            source = await session.get(RunRecord, request.fork_from_run_id)
            if source is None or source.session_id != session_id or source.configuration_context is None:
                raise not_found()
            if source.status != "completed":
                raise failure("run_not_forkable", "A new editing approach requires a completed configuration Run.")
            now = self._clock()
            thread = ThreadRecord(
                id=new_thread_id(),
                organization_id=conversation.organization_id,
                session_id=session_id,
                version=1,
                queue_version=0,
                role="child",
                origin_kind="fork",
                origin_thread_id=source.thread_id,
                origin_run_id=source.id,
                labels={},
                created_at=now,
                updated_at=now,
            )
            session.add(thread)
            await session.flush()
            await create_draft(session, conversation=conversation, thread=thread, source=request.source, now=now)
            await session.flush()
            result = await thread_view(session, thread)
            session.add(
                evidence_record(
                    actor=actor,
                    organization_id=conversation.organization_id,
                    workspace_id=conversation.workspace_id,
                    operation="configuration.thread.create",
                    scope_id=session_id,
                    identity=identity,
                    result_kind="thread",
                    result_ref=thread.id,
                    now=now,
                    response=result,
                )
            )
            return result

    async def list_sessions(
        self, *, actor: AuthenticatedActor, limit: int, cursor: str | None
    ) -> ConfigurationSessionCollection:
        scope: dict[str, object] = {"workspace_id": actor.workspace_id, "owner": actor.principal.principal_id}
        async with short_session(self._sessions) as session:
            workspace = await session.get(WorkspaceRecord, actor.workspace_id)
            if workspace is None:
                raise not_found()
            try:
                permissions = await read_principal_permissions(
                    session,
                    principal=actor.principal,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.id,
                )
            except AuthorizationError as error:
                raise not_found() from error
            required = {WorkspaceAction.agent_read, WorkspaceAction.session_read}
            target_ids = [key for key, _ in permissions.agent_actions if required.issubset(permissions.for_agent(key))]
            target_access = (
                SessionRecord.configuration_target_agent_id.is_not(None)
                if required.issubset(permissions.workspace_actions)
                else SessionRecord.configuration_target_agent_id.in_(target_ids)
            )
            create_access = {WorkspaceAction.agent_create, WorkspaceAction.session_read}.issubset(
                permissions.workspace_actions
            )
            query = (
                select(SessionRecord, ThreadRecord.id)
                .join(ThreadRecord, and_(ThreadRecord.session_id == SessionRecord.id, ThreadRecord.role == "root"))
                .where(
                    SessionRecord.workspace_id == workspace.id,
                    SessionRecord.configuration_owner_user_id == actor.principal.principal_id,
                    or_(
                        target_access,
                        and_(SessionRecord.configuration_target_agent_id.is_(None), literal(create_access)),
                    ),
                )
            )
            after = cursor_boundary(cursor, scope=scope, prefix="sess_")
            if after is not None:
                query = query.where(
                    or_(
                        SessionRecord.updated_at < after[0],
                        and_(SessionRecord.updated_at == after[0], SessionRecord.id < after[1]),
                    )
                )
            rows = (
                await session.execute(
                    query.order_by(SessionRecord.updated_at.desc(), SessionRecord.id.desc()).limit(limit + 1)
                )
            ).all()
            page = rows[:limit]
            return ConfigurationSessionCollection(
                items=tuple(session_view(row[0], root_thread_id=row[1]) for row in page),
                next_cursor=encode_time_cursor(page[-1][0].updated_at, page[-1][0].id, scope=scope)
                if len(rows) > limit
                else None,
            )

    async def list_threads(
        self, *, actor: AuthenticatedActor, session_id: str, limit: int, cursor: str | None
    ) -> ConfigurationThreadCollection:
        scope: dict[str, object] = {"session_id": session_id, "owner": actor.principal.principal_id}
        async with short_session(self._sessions) as session:
            try:
                await authorize_session(
                    session, actor=actor, session_id=session_id, write=False, action=WorkspaceAction.thread_read
                )
            except AuthorizationError as error:
                raise not_found() from error
            query = select(ThreadRecord).where(ThreadRecord.session_id == session_id)
            after = cursor_boundary(cursor, scope=scope, prefix="thread_")
            if after is not None:
                query = query.where(
                    or_(
                        ThreadRecord.updated_at < after[0],
                        and_(ThreadRecord.updated_at == after[0], ThreadRecord.id < after[1]),
                    )
                )
            rows = tuple(
                await session.scalars(
                    query.order_by(ThreadRecord.updated_at.desc(), ThreadRecord.id.desc()).limit(limit + 1)
                )
            )
            page = rows[:limit]
            return ConfigurationThreadCollection(
                items=tuple([await thread_view(session, row) for row in page]),
                next_cursor=encode_time_cursor(page[-1].updated_at, page[-1].id, scope=scope)
                if len(rows) > limit
                else None,
            )


def cursor_boundary(cursor: str | None, *, scope: dict[str, object], prefix: str):
    try:
        return None if cursor is None else decode_time_cursor(cursor, scope=scope, id_prefix=prefix)
    except ValueError as error:
        raise failure(
            "invalid_cursor", "The collection cursor is invalid.", category=ErrorCategory.invalid_request
        ) from error


def session_view(record: SessionRecord, *, root_thread_id: str) -> ConfigurationSessionView:
    assert record.configuration_owner_user_id is not None
    return ConfigurationSessionView(
        id=record.id,
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        owner_user_id=record.configuration_owner_user_id,
        target_agent_id=record.configuration_target_agent_id,
        root_thread_id=root_thread_id,
        created_at=assume_utc(record.created_at),
        updated_at=assume_utc(record.updated_at),
    )


async def thread_view(session: AsyncSession, record: ThreadRecord) -> ConfigurationThreadView:
    draft = await session.get(ConfigurationDraftRecord, record.configuration_latest_draft_id)
    if draft is None:
        raise not_found()
    predecessor = (
        None
        if draft.predecessor_draft_id is None
        else await session.get(ConfigurationDraftRecord, draft.predecessor_draft_id)
    )
    receipt = draft.application_receipt or (None if predecessor is None else predecessor.application_receipt)
    return ConfigurationThreadView(
        thread=record.to_resource(),
        active_draft_id=record.configuration_active_draft_id,
        latest_draft=draft.to_resource(),
        previous_application_receipt=None
        if receipt is None
        else ConfigurationApplicationReceipt.model_validate(receipt),
    )
