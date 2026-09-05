"""Serialize Model naming within an Organization's overlapping namespaces."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam.models import OrganizationRecord
from a13n_service.iam.resource_scope import visible_workspace

from .domain import normalize_key
from .models import ModelRecord
from .service_common import ModelError


async def require_available_key(
    session: AsyncSession, *, organization_id: str, workspace_id: str | None, key: str
) -> None:
    # All Model creates acquire the same parent lock before checking or inserting.
    # A unique index alone cannot express the overlap between org and workspace keys.
    await session.scalar(
        select(OrganizationRecord.id).where(OrganizationRecord.id == organization_id).with_for_update()
    )
    query = select(ModelRecord.id).where(
        ModelRecord.organization_id == organization_id,
        ModelRecord.normalized_key == normalize_key(key),
    )
    if workspace_id is not None:
        query = query.where(visible_workspace(ModelRecord.workspace_id, workspace_id))
    if await session.scalar(query.limit(1)) is not None:
        raise ModelError(
            "model_key_conflict", "A Model with this key already exists in an overlapping scope.", status_code=409
        )
