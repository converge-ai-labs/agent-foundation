"""Read current Service credential eligibility for requests and stream continuations."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.temporal import assume_utc, utc_now

from .auth_models import ApiKeyRecord, AuthSessionRecord
from .authorization import AuthenticatedActor, AuthorizationError
from .models import RoleBindingRecord, ServiceAccountRecord, UserRecord, WorkspaceRecord


def expired(expires_at: datetime | None) -> bool:
    return expires_at is not None and assume_utc(expires_at) <= utc_now()


async def require_key_eligible(session: AsyncSession, key: ApiKeyRecord) -> None:
    if key.revoked_at is not None or expired(key.expires_at) or key.boundary_type != "workspace":
        raise AuthorizationError("credential_invalid")
    workspace = await session.get(WorkspaceRecord, key.boundary_id)
    if workspace is None or workspace.deleted_at is not None or workspace.organization_id != key.organization_id:
        raise AuthorizationError("credential_invalid")
    if key.principal_type == "user":
        user = await session.get(UserRecord, key.principal_id)
        if user is None or user.status != "active":
            raise AuthorizationError("credential_invalid")
        grants = (
            await session.scalars(
                select(RoleBindingRecord).where(
                    RoleBindingRecord.principal_type == "user",
                    RoleBindingRecord.principal_id == user.id,
                    RoleBindingRecord.organization_id == key.organization_id,
                )
            )
        ).all()
        from .bindings import validate_binding

        for grant in grants:
            validate_binding(grant)
        member = any(g.resource_type == "organization" and g.resource_id == key.organization_id for g in grants)
        access = any(
            (g.resource_type == "organization" and g.resource_id == key.organization_id and g.role_key == "admin")
            or (g.workspace_id == key.boundary_id and g.resource_type in {"workspace", "agent"})
            for g in grants
        )
        if not member or not access:
            raise AuthorizationError("credential_invalid")
    elif key.principal_type == "service_account":
        account = await session.get(ServiceAccountRecord, key.principal_id)
        if (
            account is None
            or account.status != "active"
            or account.deleted_at is not None
            or account.workspace_id != key.boundary_id
            or account.organization_id != key.organization_id
        ):
            raise AuthorizationError("credential_invalid")
        role = await session.scalar(
            select(RoleBindingRecord.role_key).where(
                RoleBindingRecord.principal_type == "service_account",
                RoleBindingRecord.principal_id == account.id,
                RoleBindingRecord.organization_id == key.organization_id,
                RoleBindingRecord.workspace_id == key.boundary_id,
                RoleBindingRecord.resource_type == "workspace",
                RoleBindingRecord.resource_id == key.boundary_id,
            )
        )
        if role not in {"viewer", "runner", "builder"}:
            raise AuthorizationError("credential_invalid")
    else:
        raise AuthorizationError("credential_invalid")


async def require_current_credential(session: AsyncSession, actor: AuthenticatedActor) -> None:
    if actor.credential_source != "service":
        return
    if actor.auth_method == "session":
        row = await session.get(AuthSessionRecord, actor.credential_id)
        if (
            row is None
            or row.user_id != actor.principal.principal_id
            or row.revoked_at is not None
            or expired(row.expires_at)
        ):
            raise AuthorizationError("credential_invalid")
    elif actor.auth_method == "api_key":
        key = await session.get(ApiKeyRecord, actor.credential_id)
        if (
            key is None
            or key.principal_id != actor.principal.principal_id
            or key.principal_type != actor.principal.principal_type.value
            or key.boundary_id != actor.boundary_workspace_id
        ):
            raise AuthorizationError("credential_invalid")
        await require_key_eligible(session, key)
    else:
        raise AuthorizationError("credential_invalid")
