"""Resolve public resource references inside the credential's namespace."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import AuthenticatedActor
from .models import OrganizationRecord, WorkspaceRecord
from .service_common import not_found


async def organization_id(session: AsyncSession, actor: AuthenticatedActor, reference: str) -> str:
    boundary = actor.boundary_organization_id
    if boundary is None:
        boundary = await session.scalar(
            select(WorkspaceRecord.organization_id).where(WorkspaceRecord.id == actor.workspace_id)
        )
    column = OrganizationRecord.id if "_" in reference else OrganizationRecord.key
    result = await session.scalar(
        select(OrganizationRecord.id).where(
            OrganizationRecord.id == boundary,
            column == reference,
        )
    )
    if result is None:
        raise not_found()
    return result


async def workspace_id(session: AsyncSession, actor: AuthenticatedActor, reference: str) -> str:
    column = WorkspaceRecord.id if "_" in reference else WorkspaceRecord.key
    scope = (
        WorkspaceRecord.id == actor.boundary_workspace_id
        if actor.boundary_workspace_id is not None
        else WorkspaceRecord.organization_id == actor.boundary_organization_id
    )
    result = await session.scalar(
        select(WorkspaceRecord.id).where(
            scope,
            column == reference,
            WorkspaceRecord.deleted_at.is_(None),
        )
    )
    if result is None:
        raise not_found()
    return result
