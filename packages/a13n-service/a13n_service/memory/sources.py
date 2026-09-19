"""Exact completed-work references remain bound to their admitted corpus and audience."""

from a13n_harness.providers.memory.documents import MemoryDocumentError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent
from a13n_service.interactions.models import RunRecord, SessionRecord

from .models import RunMemoryStorageRecord


async def authorize_sources(
    session: AsyncSession, *, actor: AuthenticatedActor, storage_id: str, workspace_id: str, references: tuple[str, ...]
) -> None:
    for reference in references:
        if not reference.startswith("run://"):
            raise MemoryDocumentError("memory_source_unavailable")
        run_id = reference.removeprefix("run://")
        source = await session.get(RunRecord, run_id)
        parent = await session.get(SessionRecord, source.session_id) if source else None
        binding = await session.scalar(
            select(RunMemoryStorageRecord.run_id).where(
                RunMemoryStorageRecord.run_id == run_id,
                RunMemoryStorageRecord.storage_id == storage_id,
            )
        )
        if (
            source is None
            or source.status != "completed"
            or parent is None
            or parent.workspace_id != workspace_id
            or binding is None
        ):
            raise MemoryDocumentError("memory_source_unavailable")
        await authorize_agent(
            session, actor=actor, workspace_id=workspace_id, agent_id=source.agent_id, action=WorkspaceAction.run_read
        )
