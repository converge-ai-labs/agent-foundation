"""Role grant validation and mutation inside the Organization's short write lock."""

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.ids import new_object_id

from ..auth.credentials import require_key_eligible
from ..authorization import (
    WorkspaceAction,
    authorize_organization_admin,
    authorize_organization_admin_principal,
    authorize_persisted_workspace_principal_action,
    authorize_workspace,
    require_ordinary_agent,
)
from ..domain import AuthenticatedActor, AuthorizationError, PrincipalRef, PrincipalType
from ..models import ApiKeyRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from ..role_rules import validate_binding
from ..schemas import Grant
from ..service_common import identity_error, not_found

_ROLE_ORDER = {"member": 0, "viewer": 1, "runner": 2, "builder": 3, "admin": 4}


async def authorize_grants(
    session: AsyncSession, actor: AuthenticatedActor, organization_id: str, grants: list[Grant]
) -> None:
    for grant in grants:
        if grant.resource_type == "organization":
            if (
                grant.resource_id != organization_id
                or await authorize_organization_admin(session, actor=actor) != organization_id
            ):
                raise not_found()
        else:
            scope = await authorize_workspace(
                session,
                actor=actor,
                workspace_id=grant.resource_id,
                action=WorkspaceAction.invitation_manage,
            )
            if scope.organization_id != organization_id:
                raise not_found()


async def authorize_inviter_grants(
    session: AsyncSession, user_id: str, organization_id: str, grants: list[Grant]
) -> None:
    principal = PrincipalRef(principal_type=PrincipalType.user, principal_id=user_id)
    for grant in grants:
        if grant.resource_type == "organization":
            if grant.resource_id != organization_id:
                raise not_found()
            await authorize_organization_admin_principal(session, principal=principal, organization_id=organization_id)
        else:
            await authorize_persisted_workspace_principal_action(
                session,
                principal=principal,
                organization_id=organization_id,
                workspace_id=grant.resource_id,
                action=WorkspaceAction.invitation_manage,
            )


async def validate_grants(session: AsyncSession, organization_id: str, grants: list[Grant]) -> None:
    for grant in grants:
        if grant.resource_type == "organization":
            if grant.resource_id != organization_id:
                raise not_found()
        else:
            workspace = await session.get(WorkspaceRecord, grant.resource_id)
            if workspace is None or workspace.deleted_at is not None or workspace.organization_id != organization_id:
                raise not_found()


async def grant_role(
    session: AsyncSession,
    *,
    organization_id: str,
    principal_type: str,
    principal_id: str,
    resource_type: str,
    resource_id: str,
    workspace_id: str | None,
    role_key: str,
    created_by_user_id: str,
    now: datetime,
    additive: bool = False,
) -> RoleBindingRecord:
    if resource_type == "agent":
        await require_ordinary_agent(session, agent_id=resource_id)
    row = await session.scalar(
        select(RoleBindingRecord).where(
            RoleBindingRecord.principal_type == principal_type,
            RoleBindingRecord.principal_id == principal_id,
            RoleBindingRecord.resource_type == resource_type,
            RoleBindingRecord.resource_id == resource_id,
            RoleBindingRecord.organization_id == organization_id,
        )
    )
    if row is None:
        row = RoleBindingRecord(
            id=new_object_id("rb"),
            organization_id=organization_id,
            workspace_id=workspace_id,
            principal_type=principal_type,
            principal_id=principal_id,
            resource_type=resource_type,
            resource_id=resource_id,
            role_key=role_key,
            created_by_user_id=created_by_user_id,
            created_at=now,
            updated_at=now,
        )
        validate_binding(row)
        session.add(row)
    else:
        validate_binding(row)
        if not additive or _ROLE_ORDER[role_key] > _ROLE_ORDER[row.role_key]:
            if row.resource_type == "organization" and row.role_key == "admin" and role_key != "admin":
                await preserve_admin(session, organization_id, row.principal_id)
            row.role_key, row.updated_at = role_key, now
            validate_binding(row)
    await session.flush()
    return row


async def preserve_admin(session: AsyncSession, organization_id: str, user_id: str) -> None:
    other = await session.scalar(
        select(RoleBindingRecord.id)
        .join(UserRecord, UserRecord.id == RoleBindingRecord.principal_id)
        .where(
            RoleBindingRecord.organization_id == organization_id,
            RoleBindingRecord.resource_type == "organization",
            RoleBindingRecord.resource_id == organization_id,
            RoleBindingRecord.principal_type == "user",
            RoleBindingRecord.role_key == "admin",
            RoleBindingRecord.principal_id != user_id,
            UserRecord.status == "active",
        )
        .limit(1)
    )
    if other is None:
        raise identity_error("last_admin_required", "The last active Organization Admin cannot be removed.")


async def remove_user_binding(session: AsyncSession, row: RoleBindingRecord, now: datetime) -> None:
    validate_binding(row)
    if row.principal_type != "user":
        raise not_found()
    if row.resource_type == "organization" and row.role_key == "admin":
        await preserve_admin(session, row.organization_id, row.principal_id)
    principal = (
        RoleBindingRecord.principal_type == "user",
        RoleBindingRecord.principal_id == row.principal_id,
        RoleBindingRecord.organization_id == row.organization_id,
    )
    if row.resource_type == "organization":
        await session.execute(delete(RoleBindingRecord).where(*principal))
        await session.execute(
            update(ApiKeyRecord)
            .where(
                ApiKeyRecord.principal_type == "user",
                ApiKeyRecord.principal_id == row.principal_id,
                ApiKeyRecord.organization_id == row.organization_id,
                ApiKeyRecord.revoked_at.is_(None),
            )
            .values(revoked_at=now, updated_at=now)
        )
    else:
        await session.delete(row)
        await session.flush()
        # Direct Agent grants can still authorize a Personal key in this Workspace.
        for key in await session.scalars(
            select(ApiKeyRecord).where(
                ApiKeyRecord.principal_type == "user",
                ApiKeyRecord.principal_id == row.principal_id,
                ApiKeyRecord.organization_id == row.organization_id,
                ApiKeyRecord.boundary_id == row.workspace_id,
                ApiKeyRecord.revoked_at.is_(None),
            )
        ):
            try:
                await require_key_eligible(session, key)
            except AuthorizationError:
                key.revoked_at = key.updated_at = now
