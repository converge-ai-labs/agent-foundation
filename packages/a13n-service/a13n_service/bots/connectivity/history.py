"""Authorized projections of existing inbound bindings and interaction history."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.accounts.queries import require_account
from a13n_service.connectivity.cursors import CursorError, decode_cursor, encode_cursor
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.ingress.admission_models import AgentThreadBindingRecord
from a13n_service.connectivity.native_management import authorize, require_limit
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent_scoped_collection
from a13n_service.interactions.domain import RunStatus
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.storage import short_session


class BotThread(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    binding_id: str
    session_id: str
    thread_id: str
    run_id: str
    agent_id: str
    run_status: RunStatus
    updated_at: datetime


class BotThreadCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[BotThread, ...]
    next_cursor: str | None


async def list_bot_threads(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    account_id: str,
    target_id: str | None,
    limit: int,
    cursor: str | None,
) -> BotThreadCollection:
    require_limit(limit)
    async with short_session(sessions) as database:
        account = await require_account(database, account_id)
        await authorize(database, actor, account.workspace_id, WorkspaceAction.application_account_read)
        scope = {
            "kind": "bot_threads",
            "principal": actor.principal.model_dump(mode="json"),
            "workspace_id": account.workspace_id,
            "account_id": account_id,
            "target_id": target_id,
        }
        try:
            boundary = decode_cursor(cursor, scope=scope, id_prefix="bind") if cursor else None
        except CursorError as error:
            raise NativeError("invalid_cursor", str(error), category=ErrorCategory.invalid_request) from error
        if account.provider_key not in {"slack", "lark"}:
            raise NativeError("resource_not_found", "Bot not found.", category=ErrorCategory.not_found)
        # Account navigation never grants transcript authority. Intersect the same
        # Agent-scoped permissions used by the canonical Session/Thread/Run views.
        visible: frozenset[str] | None = None
        for action in (WorkspaceAction.session_read, WorkspaceAction.thread_read, WorkspaceAction.run_read):
            authorization = await authorize_agent_scoped_collection(
                database, actor=actor, workspace_id=account.workspace_id, action=action
            )
            if authorization.visible_agent_ids is not None:
                visible = (
                    authorization.visible_agent_ids if visible is None else visible & authorization.visible_agent_ids
                )
        query = (
            select(AgentThreadBindingRecord.id, ThreadRecord, RunRecord)
            .join(
                ThreadRecord,
                and_(
                    ThreadRecord.organization_id == AgentThreadBindingRecord.organization_id,
                    ThreadRecord.id == AgentThreadBindingRecord.thread_id,
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
                AgentThreadBindingRecord.account_id == account_id,
                AgentThreadBindingRecord.organization_id == account.organization_id,
                AgentThreadBindingRecord.workspace_id == account.workspace_id,
            )
            .order_by(ThreadRecord.updated_at.desc(), AgentThreadBindingRecord.id.desc())
            .limit(limit + 1)
        )
        if visible is not None:
            query = query.where(RunRecord.agent_id.in_(visible))
        if target_id is not None:
            source = aliased(RunRecord)
            selected = select(source.id).where(
                source.organization_id == ThreadRecord.organization_id,
                source.thread_id == ThreadRecord.id,
                cast(source.native_tool_contexts_json, JSONB).contains(
                    [{"kind": "inbound", "account_id": account_id, "target_id": target_id}]
                ),
            )
            if visible is not None:
                selected = selected.where(source.agent_id.in_(visible))
            # The accepted context survives batch retention and later Agent/target
            # changes. Never derive this filter from user-editable session labels.
            query = query.where(selected.exists())
        if boundary is not None:
            updated_at, binding_id = boundary
            query = query.where(
                or_(
                    ThreadRecord.updated_at < updated_at,
                    and_(ThreadRecord.updated_at == updated_at, AgentThreadBindingRecord.id < binding_id),
                )
            )
        rows = (await database.execute(query)).all()
        items = tuple(
            BotThread(
                binding_id=binding_id,
                session_id=thread.session_id,
                thread_id=thread.id,
                run_id=run.id,
                agent_id=run.agent_id,
                run_status=RunStatus(run.status),
                updated_at=thread.updated_at,
            )
            for binding_id, thread, run in rows[:limit]
        )
    next_cursor = (
        encode_cursor(updated_at=items[-1].updated_at, object_id=items[-1].binding_id, scope=scope)
        if len(rows) > limit
        else None
    )
    return BotThreadCollection(items=items, next_cursor=next_cursor)
