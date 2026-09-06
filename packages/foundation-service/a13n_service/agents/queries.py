"""Agent management queries."""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    authorize_agent_collection,
)
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.storage import transaction

from .cursors import (
    AgentCursorError,
    decode_agent_cursor,
    decode_revision_cursor,
    encode_agent_cursor,
    encode_revision_cursor,
)
from .domain import (
    Agent,
    AgentCollection,
    AgentRevision,
    AgentRevisionCollection,
    AgentSource,
)
from .errors import (
    AgentError,
    agent_revision_not_found,
    map_authorization_error,
)
from .models import AgentRecord, AgentRevisionRecord
from .persistence import (
    authorize_agent_scope,
    load_agent,
)


class AgentQueries:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def get(self, *, actor: AuthenticatedActor, agent_id: str) -> Agent:
        async with transaction(self._sessions) as session:
            workspace = await authorize_agent_scope(
                session,
                actor=actor,
                agent_id=agent_id,
                action=WorkspaceAction.agent_read,
            )
            return await load_agent(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                agent_id=agent_id,
            )

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
        enabled: bool | None,
        source: AgentSource | None,
        include_archived: bool,
    ) -> AgentCollection:
        scope = {
            "workspace_id": workspace_id,
            "enabled": enabled,
            "source": source.value if source is not None else None,
            "include_archived": include_archived,
        }
        try:
            after = decode_agent_cursor(cursor, scope=scope) if cursor is not None else None
        except AgentCursorError as error:
            raise AgentError(
                "invalid_cursor", "The collection cursor is invalid.", category=ErrorCategory.invalid_request
            ) from error
        try:
            async with transaction(self._sessions) as session:
                authorization = await authorize_agent_collection(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                )
                workspace = authorization.workspace
                query = select(AgentRecord).where(
                    AgentRecord.organization_id == workspace.organization_id,
                    AgentRecord.workspace_id == workspace_id,
                )
                if authorization.visible_agent_ids is not None:
                    query = query.where(AgentRecord.id.in_(authorization.visible_agent_ids))
                if enabled is not None:
                    query = query.where(AgentRecord.enabled == enabled)
                if not include_archived:
                    query = query.where(AgentRecord.archived_at.is_(None))
                if source is not None:
                    query = query.where(AgentRecord.source == source.value)
                if after is not None:
                    updated_at, agent_id = after
                    query = query.where(
                        or_(
                            AgentRecord.updated_at < updated_at,
                            and_(AgentRecord.updated_at == updated_at, AgentRecord.id < agent_id),
                        )
                    )
                rows = tuple(
                    (
                        await session.scalars(
                            query.order_by(AgentRecord.updated_at.desc(), AgentRecord.id.desc()).limit(limit + 1)
                        )
                    ).all()
                )
                page = rows[:limit]
                next_cursor = None
                if len(rows) > limit and page:
                    next_cursor = encode_agent_cursor(
                        updated_at=page[-1].updated_at,
                        agent_id=page[-1].id,
                        scope=scope,
                    )
                return AgentCollection(
                    items=tuple(record.to_resource() for record in page),
                    next_cursor=next_cursor,
                )
        except AuthorizationError as error:
            raise map_authorization_error(error) from error

    async def list_revisions(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        limit: int,
        cursor: str | None,
    ) -> AgentRevisionCollection:
        scope: dict[str, object] = {"agent_id": agent_id}
        try:
            after = decode_revision_cursor(cursor, scope=scope) if cursor is not None else None
        except AgentCursorError as error:
            raise AgentError(
                "invalid_cursor", "The collection cursor is invalid.", category=ErrorCategory.invalid_request
            ) from error
        async with transaction(self._sessions) as session:
            workspace = await authorize_agent_scope(
                session,
                actor=actor,
                agent_id=agent_id,
                action=WorkspaceAction.agent_read,
            )
            query = select(AgentRevisionRecord).where(
                AgentRevisionRecord.organization_id == workspace.organization_id,
                AgentRevisionRecord.workspace_id == workspace.workspace_id,
                AgentRevisionRecord.agent_id == agent_id,
            )
            if after is not None:
                number, revision_id = after
                query = query.where(
                    or_(
                        AgentRevisionRecord.version < number,
                        and_(
                            AgentRevisionRecord.version == number,
                            AgentRevisionRecord.id < revision_id,
                        ),
                    )
                )
            rows = tuple(
                (
                    await session.scalars(
                        query.order_by(
                            AgentRevisionRecord.version.desc(),
                            AgentRevisionRecord.id.desc(),
                        ).limit(limit + 1)
                    )
                ).all()
            )
            page = rows[:limit]
            next_cursor = None
            if len(rows) > limit and page:
                next_cursor = encode_revision_cursor(
                    version=page[-1].version,
                    revision_id=page[-1].id,
                    scope=scope,
                )
            return AgentRevisionCollection(items=tuple(item.to_resource() for item in page), next_cursor=next_cursor)

    async def get_revision(
        self,
        *,
        actor: AuthenticatedActor,
        revision_id: str,
        agent_id: str | None = None,
    ) -> AgentRevision:
        async with transaction(self._sessions) as session:
            revision = await session.scalar(select(AgentRevisionRecord).where(AgentRevisionRecord.id == revision_id))
            if revision is None or (agent_id is not None and revision.agent_id != agent_id):
                raise agent_revision_not_found()
            await authorize_agent_scope(
                session,
                actor=actor,
                agent_id=revision.agent_id,
                action=WorkspaceAction.agent_read,
            )
            return revision.to_resource()
