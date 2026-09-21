"""Authorized delivery of one asynchronous result to an active Harness Run."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.iam.operation import authorization_operation
from a13n_service.interactions.control_domain import ThreadInboxEntry
from a13n_service.interactions.models import RunRecord, SessionRecord
from a13n_service.run_stream import RunDisplayStore
from a13n_service.storage import short_session

from .models import ChildRunRelationshipRecord
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
        displays: RunDisplayStore,
    ) -> None:
        self._sessions = sessions
        self._displays = displays

    @authorization_operation
    async def __call__(self, entry: ThreadInboxEntry) -> str:
        authority = await self._read_authorized(entry)
        terminal_item = await load_async_subagent_terminal_item(
            self._displays,
            organization_id=entry.organization_id,
            child=authority.child,
            expected_item_id=authority.payload.terminal_result_item_id,
        )
        await self._require_retained_source(entry, authority)
        payload = validate_async_subagent_result_authority(authority, terminal_item)
        return project_async_subagent_result(payload)

    async def _read_authorized(self, entry: ThreadInboxEntry) -> AsyncSubagentResultAuthority:
        async with short_session(self._sessions) as database:
            authority = await read_async_subagent_result_authority(database, entry)
            row = (
                await database.execute(
                    select(RunRecord, SessionRecord)
                    .join(
                        SessionRecord,
                        (SessionRecord.organization_id == RunRecord.organization_id)
                        & (SessionRecord.id == RunRecord.session_id),
                    )
                    .where(
                        RunRecord.organization_id == entry.organization_id,
                        RunRecord.id == entry.target_run_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise AsyncSubagentResultError("async result incorporation authority is incomplete")
            target, session = row
            if target.thread_id != entry.thread_id or target.session_id != authority.parent.session_id:
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

    async def _require_retained_source(self, entry: ThreadInboxEntry, authority: AsyncSubagentResultAuthority) -> None:
        """After object I/O, check provenance still exists without reloading immutable content or IAM."""

        origin = aliased(RunRecord)
        target = aliased(RunRecord)
        source = authority.relationship
        query = (
            select(ChildRunRelationshipRecord.id)
            .join(
                RunRecord,
                (RunRecord.organization_id == ChildRunRelationshipRecord.organization_id)
                & (RunRecord.id == ChildRunRelationshipRecord.child_run_id),
            )
            .join(
                origin,
                (origin.organization_id == ChildRunRelationshipRecord.organization_id)
                & (origin.id == ChildRunRelationshipRecord.parent_run_id),
            )
            .join(target, (target.organization_id == origin.organization_id) & (target.id == entry.target_run_id))
            .where(
                ChildRunRelationshipRecord.organization_id == entry.organization_id,
                ChildRunRelationshipRecord.id == source.id,
                ChildRunRelationshipRecord.subagent_name == source.subagent_name,
                ChildRunRelationshipRecord.child_thread_id == source.child_thread_id,
                RunRecord.id == authority.child.id,
                RunRecord.thread_id == source.child_thread_id,
                RunRecord.status == authority.child.status.value,
                RunRecord.session_id == authority.parent.session_id,
                origin.id == entry.origin_run_id,
                origin.thread_id == entry.thread_id,
                origin.session_id == authority.parent.session_id,
                target.thread_id == entry.thread_id,
                target.session_id == authority.parent.session_id,
            )
        )
        async with short_session(self._sessions) as database:
            if not await database.scalar(select(query.exists())):
                raise AsyncSubagentResultError("async result durable authority is incomplete")


__all__ = ["AsyncSubagentResultMaterializer"]
