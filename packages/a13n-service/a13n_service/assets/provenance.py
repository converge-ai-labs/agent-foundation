"""Current source-Run authorization for public Asset projections."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, SessionRecord

from .domain import Asset
from .errors import asset_not_found
from .models import AssetRecord


async def require_readable_run(session: AsyncSession, *, actor: AuthenticatedActor, run_id: str) -> None:
    agent_id = await session.scalar(
        select(RunRecord.agent_id)
        .join(
            SessionRecord,
            (SessionRecord.id == RunRecord.session_id) & (SessionRecord.organization_id == RunRecord.organization_id),
        )
        .where(RunRecord.id == run_id, SessionRecord.workspace_id == actor.workspace_id)
    )
    if agent_id is None or not await _can_read_agent_runs(session, actor, agent_id):
        raise asset_not_found()


async def project_assets(
    session: AsyncSession, *, actor: AuthenticatedActor, records: tuple[AssetRecord, ...]
) -> tuple[Asset, ...]:
    attempts = tuple(record.source_run_attempt_id for record in records if record.source_run_attempt_id is not None)
    if not attempts:
        return tuple(record.to_resource() for record in records)
    sources = (
        await session.execute(
            select(RunAttemptRecord.id, RunRecord.id, RunRecord.agent_id)
            .join(
                RunRecord,
                (RunRecord.id == RunAttemptRecord.run_id)
                & (RunRecord.organization_id == RunAttemptRecord.organization_id),
            )
            .join(
                SessionRecord,
                (SessionRecord.id == RunRecord.session_id)
                & (SessionRecord.organization_id == RunRecord.organization_id),
            )
            .where(RunAttemptRecord.id.in_(attempts), SessionRecord.workspace_id == actor.workspace_id)
        )
    ).all()
    readable = {
        agent_id: await _can_read_agent_runs(session, actor, agent_id)
        for agent_id in {source.agent_id for source in sources}
    }
    by_attempt = {attempt_id: run_id for attempt_id, run_id, agent_id in sources if readable[agent_id]}
    return tuple(record.to_resource(source_run_id=by_attempt.get(record.source_run_attempt_id)) for record in records)


async def _can_read_agent_runs(session: AsyncSession, actor: AuthenticatedActor, agent_id: str) -> bool:
    try:
        await authorize_agent(
            session, actor=actor, workspace_id=actor.workspace_id, agent_id=agent_id, action=WorkspaceAction.run_read
        )
    except AuthorizationError:
        return False
    return True
