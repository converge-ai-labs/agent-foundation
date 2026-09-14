"""Agent avatar metadata with short authorization and publication transactions."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.ids import new_object_id
from a13n_service.object_retention.persistence import require_object_publications
from a13n_service.profile_images import read_image, write_image
from a13n_service.storage import ObjectNotFound, ObjectStore, short_session, transaction
from a13n_service.temporal import utc_now

from .domain import Agent
from .errors import agent_not_found
from .models import AgentRecord
from .persistence import (
    authorize_agent_scope,
    lock_agent,
    new_agent_audit,
    require_custom_mutable,
    require_etag,
    touch_agent,
)


def image_key(organization_id: str, workspace_id: str, agent_id: str, image_id: str) -> str:
    return f"organizations/{organization_id}/workspaces/{workspace_id}/agents/{agent_id}/avatar/{image_id}/content.webp"


class AgentImages:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def replace(
        self, *, actor: AuthenticatedActor, agent_id: str, content: bytes | None, if_match: str, objects: ObjectStore
    ) -> Agent:
        async with transaction(self._sessions) as session:
            workspace = await authorize_agent_scope(
                session, actor=actor, agent_id=agent_id, action=WorkspaceAction.agent_update
            )
            record = await lock_agent(session, workspace.organization_id, workspace.workspace_id, agent_id)
            require_custom_mutable(record)
            require_etag(record, if_match)
        image_id = new_object_id("img") if content is not None else None
        key = image_key(workspace.organization_id, workspace.workspace_id, agent_id, image_id) if image_id else None
        if key is not None and content is not None:
            await write_image(objects, key, content)
        async with transaction(self._sessions) as session:
            workspace = await authorize_agent_scope(
                session, actor=actor, agent_id=agent_id, action=WorkspaceAction.agent_update
            )
            record = await lock_agent(session, workspace.organization_id, workspace.workspace_id, agent_id)
            require_custom_mutable(record)
            require_etag(record, if_match)
            if key is not None:
                await require_object_publications(session, [key])
            record.image_id = image_id
            now = utc_now()
            touch_agent(record, actor=actor, now=now)
            session.add(
                new_agent_audit(
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    action="agent.image_update",
                    agent_id=agent_id,
                    now=now,
                )
            )
            return record.to_resource()

    async def read(self, *, actor: AuthenticatedActor, agent_id: str, image_id: str, objects: ObjectStore) -> bytes:
        async with short_session(self._sessions) as session:
            workspace = await authorize_agent_scope(
                session, actor=actor, agent_id=agent_id, action=WorkspaceAction.agent_read
            )
            current_image_id = await session.scalar(
                select(AgentRecord.image_id).where(
                    AgentRecord.id == agent_id,
                    AgentRecord.workspace_id == workspace.workspace_id,
                    AgentRecord.organization_id == workspace.organization_id,
                )
            )
            if current_image_id != image_id:
                raise agent_not_found()
        try:
            return await read_image(
                objects, image_key(workspace.organization_id, workspace.workspace_id, agent_id, image_id)
            )
        except ObjectNotFound as error:
            raise agent_not_found() from error
