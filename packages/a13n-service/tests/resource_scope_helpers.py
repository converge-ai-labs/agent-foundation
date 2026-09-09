"""Identity setup shared by organization resource integration tests."""

from dataclasses import replace
from datetime import UTC, datetime

from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.models import RoleBindingRecord, WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def organization_admin(
    sessions: async_sessionmaker[AsyncSession], actor: AuthenticatedActor
) -> AuthenticatedActor:
    async with transaction(sessions) as session:
        binding = (
            await session.scalars(
                select(RoleBindingRecord).where(
                    RoleBindingRecord.resource_type == "organization",
                    RoleBindingRecord.principal_id == actor.principal.principal_id,
                )
            )
        ).one()
        binding.role_key = "admin"
        organization_id = binding.organization_id
    return replace(actor, boundary_workspace_id=None, boundary_organization_id=organization_id)


async def sibling_workspace(sessions: async_sessionmaker[AsyncSession], admin: AuthenticatedActor) -> str:
    assert admin.boundary_organization_id is not None
    workspace_id = new_object_id("ws")
    now = datetime.now(UTC)
    async with transaction(sessions) as session:
        session.add(
            WorkspaceRecord(
                id=workspace_id,
                organization_id=admin.boundary_organization_id,
                name=workspace_id,
                key=workspace_id.replace("_", "-"),
                created_at=now,
                updated_at=now,
            )
        )
    return workspace_id
