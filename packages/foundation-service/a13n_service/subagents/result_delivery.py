"""Authorized delivery of one asynchronous result to an active Harness Run."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.interactions.control_domain import ThreadInboxEntry
from a13n_service.interactions.models import RunRecord, SessionRecord
from a13n_service.storage import short_session

from .result_payload import (
    AsyncSubagentResultError,
    project_async_subagent_result,
    validate_async_subagent_result_authority,
)


class AsyncSubagentResultMaterializer:
    """Reauthorize and project one typed result as untrusted native Agent input."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def __call__(self, entry: ThreadInboxEntry) -> str:
        async with short_session(self._sessions) as database:
            payload = await validate_async_subagent_result_authority(database, entry)
            child = await database.scalar(
                select(RunRecord).where(
                    RunRecord.tenant_id == entry.tenant_id,
                    RunRecord.id == payload.child_run_id,
                )
            )
            target = await database.scalar(
                select(RunRecord).where(
                    RunRecord.tenant_id == entry.tenant_id,
                    RunRecord.id == entry.target_run_id,
                )
            )
            if child is None or target is None:
                raise AsyncSubagentResultError("async result incorporation authority is incomplete")
            session = await database.scalar(
                select(SessionRecord).where(
                    SessionRecord.tenant_id == entry.tenant_id,
                    SessionRecord.id == target.session_id,
                )
            )
            if session is None or target.thread_id != entry.thread_id:
                raise AsyncSubagentResultError("async result target is outside the parent Thread")
            actor = AuthenticatedActor(
                principal=target.to_resource().authority_principal,
                auth_method="run_authority",
                credential_id=f"run_{target.id}",
                boundary_workspace_id=session.workspace_id,
                request_id=entry.id,
            )
            try:
                for agent_id in (target.agent_id, child.agent_id):
                    await authorize_agent(
                        database,
                        actor=actor,
                        workspace_id=session.workspace_id,
                        agent_id=agent_id,
                        action=WorkspaceAction.run_read,
                    )
            except AuthorizationError as error:
                raise AsyncSubagentResultError("async result incorporation is no longer authorized") from error
        return project_async_subagent_result(payload)


__all__ = ["AsyncSubagentResultMaterializer"]
