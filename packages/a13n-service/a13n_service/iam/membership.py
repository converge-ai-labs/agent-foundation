"""Organization and Workspace membership with atomic last-administrator protection."""

from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.ids import new_object_id
from a13n_service.storage import short_session
from a13n_service.temporal import utc_now

from .auth_models import ApiKeyRecord
from .authorization import AuthenticatedActor, WorkspaceAction, authorize_organization_admin, authorize_workspace
from .bindings import ROLE_KEYS, grant_role, remove_user_binding
from .collections import PageRequest, query_scope
from .models import RoleBindingRecord, UserRecord, WorkspaceRecord
from .schemas import Organization, Page, RoleBinding, SetRoleRequest, Workspace
from .service_common import audit, identity_error, identity_transaction, not_found, singleton_organization
from .sessions import require_user


def require_etag(row: WorkspaceRecord | RoleBindingRecord, if_match: str) -> None:
    if not etag_matches(if_match, resource_etag(row.id, row.updated_at)):
        raise identity_error(
            "precondition_failed", "The identity resource changed after it was read.", ErrorCategory.stale_version
        )


class MembershipService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def organization(self, actor: AuthenticatedActor) -> Organization:
        async with short_session(self._sessions) as session:
            user = await require_user(session, actor)
            organization = await singleton_organization(session)
            if actor.boundary_organization_id != organization.id:
                raise not_found()
            member = await session.scalar(
                select(RoleBindingRecord.id).where(
                    RoleBindingRecord.principal_type == "user",
                    RoleBindingRecord.principal_id == user.id,
                    RoleBindingRecord.organization_id == organization.id,
                    RoleBindingRecord.resource_type == "organization",
                    RoleBindingRecord.resource_id == organization.id,
                )
            )
            if member is None:
                raise not_found()
            return Organization.model_validate(organization)

    async def workspace(self, actor: AuthenticatedActor, workspace_id: str) -> Workspace:
        async with short_session(self._sessions) as session:
            await authorize_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.agent_read
            )
            row = await session.get(WorkspaceRecord, workspace_id)
            assert row is not None
            return Workspace.model_validate(row)

    async def workspaces(self, actor: AuthenticatedActor, organization_id: str, page: PageRequest) -> Page[Workspace]:
        organization = await self.organization(actor)
        if organization.id != organization_id:
            raise not_found()
        scope = query_scope(actor, "workspaces", organization_id)
        async with short_session(self._sessions) as session:
            user = await require_user(session, actor)
            admin = exists().where(
                RoleBindingRecord.principal_type == "user",
                RoleBindingRecord.principal_id == user.id,
                RoleBindingRecord.organization_id == organization_id,
                RoleBindingRecord.resource_type == "organization",
                RoleBindingRecord.resource_id == organization_id,
                RoleBindingRecord.role_key == "admin",
            )
            local = exists().where(
                RoleBindingRecord.principal_type == "user",
                RoleBindingRecord.principal_id == user.id,
                RoleBindingRecord.organization_id == organization_id,
                RoleBindingRecord.workspace_id == WorkspaceRecord.id,
            )
            rows = await session.scalars(
                select(WorkspaceRecord)
                .where(
                    WorkspaceRecord.organization_id == organization_id,
                    WorkspaceRecord.deleted_at.is_(None),
                    WorkspaceRecord.id > page.after(scope),
                    or_(admin, local),
                )
                .order_by(WorkspaceRecord.id)
                .limit(page.limit + 1)
            )
            return page.finish([Workspace.model_validate(row) for row in rows], scope)

    async def create_workspace(self, actor: AuthenticatedActor, organization_id: str, name: str) -> Workspace:
        async with identity_transaction(self._sessions, organization_id) as session:
            if await authorize_organization_admin(session, actor=actor) != organization_id:
                raise not_found()
            await self._unique_workspace(session, organization_id, name)
            now = utc_now()
            row = WorkspaceRecord(
                id=new_object_id("ws"),
                organization_id=organization_id,
                name=name,
                normalized_name=name.casefold(),
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            session.add(row)
            audit(
                session,
                actor=actor,
                action="workspace.create",
                resource_type="workspace",
                resource_id=row.id,
                organization_id=organization_id,
                workspace_id=row.id,
            )
            return Workspace.model_validate(row)

    async def update_workspace(
        self, actor: AuthenticatedActor, workspace_id: str, name: str, if_match: str
    ) -> Workspace:
        current = await self.workspace(actor, workspace_id)
        async with identity_transaction(self._sessions, current.organization_id) as session:
            await authorize_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.role_binding_manage
            )
            row = await session.get(WorkspaceRecord, workspace_id)
            if row is None or row.deleted_at is not None:
                raise not_found()
            require_etag(row, if_match)
            await self._unique_workspace(session, row.organization_id, name, excluding=row.id)
            row.name, row.normalized_name, row.updated_at = name, name.casefold(), utc_now()
            audit(
                session,
                actor=actor,
                action="workspace.update",
                resource_type="workspace",
                resource_id=row.id,
                organization_id=row.organization_id,
                workspace_id=row.id,
            )
            return Workspace.model_validate(row)

    async def delete_workspace(self, actor: AuthenticatedActor, workspace_id: str, if_match: str) -> None:
        current = await self.workspace(actor, workspace_id)
        async with identity_transaction(self._sessions, current.organization_id) as session:
            if await authorize_organization_admin(session, actor=actor) != current.organization_id:
                raise not_found()
            row = await session.get(WorkspaceRecord, workspace_id)
            if row is None or row.deleted_at is not None:
                raise not_found()
            require_etag(row, if_match)
            row.deleted_at = row.updated_at = utc_now()
            await session.execute(
                update(ApiKeyRecord)
                .where(
                    ApiKeyRecord.organization_id == row.organization_id,
                    ApiKeyRecord.boundary_id == row.id,
                    ApiKeyRecord.revoked_at.is_(None),
                )
                .values(revoked_at=row.deleted_at, updated_at=row.deleted_at)
            )
            audit(
                session,
                actor=actor,
                action="workspace.delete",
                resource_type="workspace",
                resource_id=row.id,
                organization_id=row.organization_id,
                workspace_id=row.id,
            )

    async def create_binding(
        self,
        actor: AuthenticatedActor,
        organization_id: str,
        request: SetRoleRequest,
        *,
        workspace_id: str | None = None,
    ) -> RoleBinding:
        resource_type, resource_id = (
            ("organization", organization_id) if workspace_id is None else ("workspace", workspace_id)
        )
        if request.role not in ROLE_KEYS[(resource_type, "user")]:
            raise identity_error(
                "invalid_role", "The role is not valid for this resource.", ErrorCategory.invalid_request
            )
        async with identity_transaction(self._sessions, organization_id) as session:
            user = await require_user(session, actor)
            await self._authorize_binding(session, actor, organization_id, workspace_id)
            target = await session.get(UserRecord, request.principal_id)
            if target is None or target.status != "active":
                raise not_found()
            member = await session.scalar(
                select(RoleBindingRecord.id).where(
                    RoleBindingRecord.principal_type == "user",
                    RoleBindingRecord.principal_id == target.id,
                    RoleBindingRecord.organization_id == organization_id,
                    RoleBindingRecord.resource_type == "organization",
                    RoleBindingRecord.resource_id == organization_id,
                )
            )
            if member is None:
                raise identity_error("invitation_required", "Use an invitation to add a User to the Organization.")
            existing = await session.scalar(
                select(RoleBindingRecord.id).where(
                    RoleBindingRecord.principal_type == "user",
                    RoleBindingRecord.principal_id == target.id,
                    RoleBindingRecord.resource_type == resource_type,
                    RoleBindingRecord.resource_id == resource_id,
                )
            )
            if existing is not None:
                raise identity_error("role_binding_exists", "This User already has a role on the resource.")
            row = await grant_role(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                principal_type="user",
                principal_id=target.id,
                resource_type=resource_type,
                resource_id=resource_id,
                role_key=request.role,
                created_by_user_id=user.id,
                now=utc_now(),
            )
            audit(
                session,
                actor=actor,
                action="role_binding.create",
                resource_type="role_binding",
                resource_id=row.id,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
            return RoleBinding.model_validate(row)

    async def binding(self, actor: AuthenticatedActor, binding_id: str) -> RoleBinding:
        async with short_session(self._sessions) as session:
            await require_user(session, actor)
            row = await session.get(RoleBindingRecord, binding_id)
            if row is None or row.principal_type != "user" or row.resource_type not in {"organization", "workspace"}:
                raise not_found()
            await self._authorize_binding(session, actor, row.organization_id, row.workspace_id)
            return RoleBinding.model_validate(row)

    async def change_binding(
        self, actor: AuthenticatedActor, binding_id: str, if_match: str, *, role: str | None
    ) -> RoleBinding | None:
        async with short_session(self._sessions) as session:
            row = await session.get(RoleBindingRecord, binding_id)
            if row is None:
                raise not_found()
            organization_id = row.organization_id
        async with identity_transaction(self._sessions, organization_id) as session:
            user = await require_user(session, actor)
            row = await session.get(RoleBindingRecord, binding_id)
            if row is None or row.principal_type != "user" or row.resource_type not in {"organization", "workspace"}:
                raise not_found()
            await self._authorize_binding(session, actor, organization_id, row.workspace_id)
            require_etag(row, if_match)
            if role is None:
                await remove_user_binding(session, row, utc_now())
                result = None
            else:
                if role not in ROLE_KEYS[(row.resource_type, "user")]:
                    raise identity_error(
                        "invalid_role", "The role is not valid for this resource.", ErrorCategory.invalid_request
                    )
                row = await grant_role(
                    session,
                    organization_id=organization_id,
                    workspace_id=row.workspace_id,
                    principal_type="user",
                    principal_id=row.principal_id,
                    resource_type=row.resource_type,
                    resource_id=row.resource_id,
                    role_key=role,
                    created_by_user_id=user.id,
                    now=utc_now(),
                )
                result = RoleBinding.model_validate(row)
            audit(
                session,
                actor=actor,
                action="role_binding.delete" if role is None else "role_binding.update",
                resource_type="role_binding",
                resource_id=binding_id,
                organization_id=organization_id,
                workspace_id=row.workspace_id,
            )
            return result

    @staticmethod
    async def _authorize_binding(
        session: AsyncSession, actor: AuthenticatedActor, organization_id: str, workspace_id: str | None
    ) -> None:
        if workspace_id is None:
            if await authorize_organization_admin(session, actor=actor) != organization_id:
                raise not_found()
        else:
            scope = await authorize_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.role_binding_manage
            )
            if scope.organization_id != organization_id:
                raise not_found()

    @staticmethod
    async def _unique_workspace(
        session: AsyncSession, organization_id: str, name: str, *, excluding: str | None = None
    ) -> None:
        query = select(WorkspaceRecord.id).where(
            WorkspaceRecord.organization_id == organization_id,
            WorkspaceRecord.normalized_name == name.casefold(),
            WorkspaceRecord.deleted_at.is_(None),
        )
        if excluding is not None:
            query = query.where(WorkspaceRecord.id != excluding)
        if await session.scalar(query) is not None:
            raise identity_error("workspace_name_conflict", "An active Workspace already uses this name.")
