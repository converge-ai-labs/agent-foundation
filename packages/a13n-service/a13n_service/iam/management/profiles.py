"""Profile settings and authorized Console identity projections."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session
from a13n_service.temporal import utc_now

from ..auth.sessions import require_user
from ..authorization import (
    WorkspaceAction,
    authorize_organization_admin,
    authorize_workspace,
    organization_role,
    workspace_permissions,
)
from ..domain import AuthenticatedActor
from ..models import ApiKeyRecord, OrganizationRecord, RoleBindingRecord, SecurityAuditRecord, UserRecord
from ..profile_schemas import OrganizationPermissions, Permissions, SecurityEvent
from ..schemas import ApiKey, Organization, Page, User
from ..service_common import audit, identity_transaction, not_found, require_etag, singleton_organization
from .collections import PageRequest, query_scope


class ProfileService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def update_user(self, actor: AuthenticatedActor, name: str, if_match: str) -> User:
        async with short_session(self._sessions) as session:
            organization_id = (await singleton_organization(session)).id
        async with identity_transaction(self._sessions, organization_id) as session:
            user = await require_user(session, actor, browser=True)
            require_etag(user, if_match)
            user.name, user.updated_at = name, utc_now()
            audit(
                session,
                actor=actor,
                action="user_profile.update",
                resource_type="user",
                resource_id=user.id,
                organization_id=None,
            )
            return User.model_validate(user)

    async def update_organization(
        self, actor: AuthenticatedActor, organization_id: str, name: str, if_match: str
    ) -> Organization:
        async with identity_transaction(self._sessions, organization_id) as session:
            if await authorize_organization_admin(session, actor=actor) != organization_id:
                raise not_found()
            row = await session.get(OrganizationRecord, organization_id)
            assert row is not None
            require_etag(row, if_match)
            row.name, row.updated_at = name, utc_now()
            audit(
                session,
                actor=actor,
                action="organization.update",
                resource_type="organization",
                resource_id=row.id,
                organization_id=row.id,
            )
            return Organization.model_validate(row)

    async def permissions(self, actor: AuthenticatedActor, workspace_id: str) -> Permissions:
        async with short_session(self._sessions) as session:
            actions, admin = await workspace_permissions(session, actor=actor, workspace_id=workspace_id)
            return Permissions(actions=sorted(actions), organization_admin=admin)

    async def organization_permissions(
        self, actor: AuthenticatedActor, organization_id: str
    ) -> OrganizationPermissions:
        async with short_session(self._sessions) as session:
            await require_user(session, actor, browser=True)
            if actor.boundary_organization_id != organization_id:
                raise not_found()
            role = await organization_role(session, principal=actor.principal, organization_id=organization_id)
            return OrganizationPermissions(organization_admin=role == "admin")

    async def members(self, actor: AuthenticatedActor, workspace_id: str, page: PageRequest) -> Page[User]:
        scope = query_scope(actor, "workspace_members", workspace_id)
        async with short_session(self._sessions) as session:
            await authorize_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.role_binding_manage
            )
            rows = await session.scalars(
                select(UserRecord)
                .join(RoleBindingRecord, RoleBindingRecord.principal_id == UserRecord.id)
                .where(
                    RoleBindingRecord.principal_type == "user",
                    RoleBindingRecord.resource_type == "workspace",
                    RoleBindingRecord.resource_id == workspace_id,
                    UserRecord.id > page.after(scope),
                )
                .order_by(UserRecord.id)
                .limit(page.limit + 1)
            )
            return page.finish([User.model_validate(row) for row in rows], scope)

    async def member_keys(self, actor: AuthenticatedActor, workspace_id: str, page: PageRequest) -> Page[ApiKey]:
        scope = query_scope(actor, "workspace_api_keys", workspace_id)
        async with short_session(self._sessions) as session:
            boundary = await authorize_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.api_key_manage
            )
            rows = await session.scalars(
                select(ApiKeyRecord)
                .where(
                    ApiKeyRecord.organization_id == boundary.organization_id,
                    ApiKeyRecord.boundary_type == "workspace",
                    ApiKeyRecord.boundary_id == workspace_id,
                    ApiKeyRecord.principal_type == "user",
                    ApiKeyRecord.id > page.after(scope),
                )
                .order_by(ApiKeyRecord.id)
                .limit(page.limit + 1)
            )
            return page.finish([ApiKey.model_validate(row) for row in rows], scope)

    async def security_events(
        self,
        actor: AuthenticatedActor,
        page: PageRequest,
        *,
        organization_id: str | None = None,
        workspace_id: str | None = None,
    ) -> Page[SecurityEvent]:
        scope = query_scope(actor, "security_events", workspace_id or organization_id or actor.principal.principal_id)
        async with short_session(self._sessions) as session:
            query = select(SecurityAuditRecord)
            if workspace_id is not None:
                boundary = await authorize_workspace(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.security_audit_read
                )
                query = query.where(
                    SecurityAuditRecord.organization_id == boundary.organization_id,
                    SecurityAuditRecord.workspace_id == workspace_id,
                )
            elif organization_id is not None:
                if await authorize_organization_admin(session, actor=actor) != organization_id:
                    raise not_found()
                query = query.where(SecurityAuditRecord.organization_id == organization_id)
            else:
                user = await require_user(session, actor, browser=True)
                query = query.where(SecurityAuditRecord.actor_type == "user", SecurityAuditRecord.actor_id == user.id)
            rows = await session.scalars(
                query.where(SecurityAuditRecord.id > page.after(scope))
                .order_by(SecurityAuditRecord.id)
                .limit(page.limit + 1)
            )
            return page.finish([SecurityEvent.model_validate(row) for row in rows], scope)
