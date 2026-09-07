"""Authorized delivery of one asynchronous result to an active Harness Run."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.interactions.control_domain import ThreadInboxEntry
from a13n_service.interactions.models import RunRecord, SessionRecord
from a13n_service.run_stream import RunReplayStore
from a13n_service.storage import short_session

from .result_payload import (
    AsyncSubagentResultAuthority,
    AsyncSubagentResultError,
    load_async_subagent_terminal_item,
    project_async_subagent_result,
    read_async_subagent_result_authority,
    validate_async_subagent_result_authority,
)


class AsyncSubagentResultMaterializer:
    """Reauthorize and project one typed result as untrusted native Agent input."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        replays: RunReplayStore,
    ) -> None:
        self._sessions = sessions
        self._replays = replays

    async def __call__(self, entry: ThreadInboxEntry) -> str:
        authority = await self._read_authorized(entry)
        terminal_item = await load_async_subagent_terminal_item(
            self._replays,
            organization_id=entry.organization_id,
            child=authority.child,
            expected_item_id=authority.payload.terminal_result_item_id,
        )
        authority = await self._read_authorized(entry)
        payload = validate_async_subagent_result_authority(authority, terminal_item)
        return project_async_subagent_result(payload)

    async def _read_authorized(self, entry: ThreadInboxEntry) -> AsyncSubagentResultAuthority:
        async with short_session(self._sessions) as database:
            authority = await read_async_subagent_result_authority(database, entry)
            target = await database.scalar(
                select(RunRecord).where(
                    RunRecord.organization_id == entry.organization_id,
                    RunRecord.id == entry.target_run_id,
                )
            )
            if target is None:
                raise AsyncSubagentResultError("async result incorporation authority is incomplete")
            session = await database.scalar(
                select(SessionRecord).where(
                    SessionRecord.organization_id == entry.organization_id,
                    SessionRecord.id == target.session_id,
                )
            )
            if (
                session is None
                or target.thread_id != entry.thread_id
                or target.session_id != authority.parent.session_id
            ):
                raise AsyncSubagentResultError("async result target is outside the parent Thread")
            actor = AuthenticatedActor(
                principal=target.to_resource().authority_principal,
                auth_method="run_authority",
                credential_id=f"run_{target.id}",
                boundary_workspace_id=session.workspace_id,
                request_id=entry.id,
            )
            try:
                for agent_id in (target.agent_id, authority.child.agent_id):
                    await authorize_agent(
                        database,
                        actor=actor,
                        workspace_id=session.workspace_id,
                        agent_id=agent_id,
                        action=WorkspaceAction.run_read,
                    )
            except AuthorizationError as error:
                raise AsyncSubagentResultError("async result incorporation is no longer authorized") from error
        return authority


__all__ = ["AsyncSubagentResultMaterializer"]
