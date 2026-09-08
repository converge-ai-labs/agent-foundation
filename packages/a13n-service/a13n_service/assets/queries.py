"""Project immutable Asset provenance inside its stored Workspace boundary."""

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.interactions.models import RunAttemptRecord, RunRecord, SessionRecord

from .domain import Asset
from .errors import asset_not_found
from .models import AssetRecord


def asset_query(*, organization_id: str, workspace_id: str):
    return (
        select(AssetRecord, RunAttemptRecord.run_id)
        .outerjoin(
            RunAttemptRecord,
            and_(
                RunAttemptRecord.organization_id == AssetRecord.organization_id,
                RunAttemptRecord.id == AssetRecord.source_run_attempt_id,
            ),
        )
        .outerjoin(
            RunRecord,
            and_(
                RunRecord.organization_id == RunAttemptRecord.organization_id, RunRecord.id == RunAttemptRecord.run_id
            ),
        )
        .outerjoin(
            SessionRecord,
            and_(SessionRecord.organization_id == RunRecord.organization_id, SessionRecord.id == RunRecord.session_id),
        )
        .where(
            AssetRecord.organization_id == organization_id,
            AssetRecord.workspace_id == workspace_id,
            AssetRecord.deleted_at.is_(None),
            or_(
                AssetRecord.source_kind == "upload",
                and_(AssetRecord.source_kind == "run_output", SessionRecord.workspace_id == workspace_id),
            ),
        )
    )


async def require_active_asset(
    database: AsyncSession, *, organization_id: str, workspace_id: str, asset_id: str
) -> Asset:
    row = (
        await database.execute(
            asset_query(organization_id=organization_id, workspace_id=workspace_id).where(AssetRecord.id == asset_id)
        )
    ).one_or_none()
    if row is None:
        raise asset_not_found()
    record, source_run_id = row
    return record.to_resource(source_run_id=source_run_id)
