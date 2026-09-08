"""Admin-managed non-human identities and their one Workspace role."""

from collections.abc import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.ids import new_object_id
from a13n_service.storage import short_session
from a13n_service.temporal import utc_now

from ..auth.sessions import require_user
from ..authorization import WorkspaceAction, authorize_workspace
from ..domain import AuthenticatedActor
from ..models import ApiKeyRecord, RoleBindingRecord, ServiceAccountRecord
from ..schemas import CreateServiceAccountRequest, ServiceAccount, UpdateServiceAccountRequest
from ..service_common import audit, identity_error, identity_transaction, not_found, require_version
from .bindings import grant_role


async def account_resources(session: AsyncSession, rows: Sequence[ServiceAccountRecord]) -> list[ServiceAccount]:
    if not rows:
        return []
    roles = {
        binding.principal_id: binding.role_key
        for binding in await session.scalars(
            select(RoleBindingRecord)
            .join(ServiceAccountRecord, ServiceAccountRecord.id == RoleBindingRecord.principal_id)
            .where(
                RoleBindingRecord.principal_type == "service_account",
                RoleBindingRecord.principal_id.in_([row.id for row in rows]),
                RoleBindingRecord.resource_type == "workspace",
                RoleBindingRecord.organization_id == ServiceAccountRecord.organization_id,
                RoleBindingRecord.workspace_id == ServiceAccountRecord.workspace_id,
                RoleBindingRecord.resource_id == RoleBindingRecord.workspace_id,
            )
        )
    }
    result = []
    for row in rows:
        role = roles.get(row.id)
        if role not in {"viewer", "runner", "builder"}:
            raise identity_error("service_account_role_invalid", "The Service Account has no valid Workspace role.")
        result.append(
            ServiceAccount(
                id=row.id,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
                name=row.name,
                description=row.description,
                status=row.status,
                version=row.version,
                created_at=row.created_at,
                updated_at=row.updated_at,
                deleted_at=row.deleted_at,
                role=role,
            )
        )
    return result


async def account_resource(session: AsyncSession, row: ServiceAccountRecord) -> ServiceAccount:
    return (await account_resources(session, [row]))[0]


class ServiceAccountService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self, actor: AuthenticatedActor, workspace_id: str, request: CreateServiceAccountRequest
    ) -> ServiceAccount:
        async with short_session(self._sessions) as session:
            scope = await authorize_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.service_account_manage
            )
        async with identity_transaction(self._sessions, scope.organization_id) as session:
            user = await require_user(session, actor)
            await authorize_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.service_account_manage
            )
            await self._unique_name(session, workspace_id, request.name)
            now = utc_now()
            row = ServiceAccountRecord(
                id=new_object_id("sa"),
                organization_id=scope.organization_id,
                workspace_id=workspace_id,
                name=request.name,
                normalized_name=request.name.casefold(),
                description=request.description,
                status="active",
                version=1,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            session.add(row)
            await session.flush()
            await grant_role(
                session,
                organization_id=row.organization_id,
                workspace_id=workspace_id,
                principal_type="service_account",
                principal_id=row.id,
                resource_type="workspace",
                resource_id=workspace_id,
                role_key=request.role,
                created_by_user_id=user.id,
                now=now,
            )
            audit(
                session,
                actor=actor,
                action="service_account.create",
                resource_type="service_account",
                resource_id=row.id,
                organization_id=row.organization_id,
                workspace_id=workspace_id,
            )
            return await account_resource(session, row)

    async def get(self, actor: AuthenticatedActor, account_id: str) -> ServiceAccount:
        async with short_session(self._sessions) as session:
            return await account_resource(session, await self.require(session, actor, account_id))

    async def update(
        self, actor: AuthenticatedActor, account_id: str, request: UpdateServiceAccountRequest
    ) -> ServiceAccount:
        current = await self.get(actor, account_id)
        async with identity_transaction(self._sessions, current.organization_id) as session:
            row = await self.require(session, actor, account_id)
            user = await require_user(session, actor)
            require_version(row.version, request.expected_version)
            await self._unique_name(session, row.workspace_id, request.name, excluding=row.id)
            row.name, row.normalized_name, row.description = request.name, request.name.casefold(), request.description
            row.status, row.updated_at = request.status, utc_now()
            row.version += 1
            await grant_role(
                session,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
                principal_type="service_account",
                principal_id=row.id,
                resource_type="workspace",
                resource_id=row.workspace_id,
                role_key=request.role,
                created_by_user_id=user.id,
                now=row.updated_at,
            )
            audit(
                session,
                actor=actor,
                action="service_account.update",
                resource_type="service_account",
                resource_id=row.id,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
            )
            return await account_resource(session, row)

    async def delete(self, actor: AuthenticatedActor, account_id: str, expected_version: int) -> None:
        current = await self.get(actor, account_id)
        async with identity_transaction(self._sessions, current.organization_id) as session:
            row = await self.require(session, actor, account_id)
            require_version(row.version, expected_version)
            row.deleted_at = row.updated_at = utc_now()
            row.version += 1
            await session.execute(
                update(ApiKeyRecord)
                .where(
                    ApiKeyRecord.principal_type == "service_account",
                    ApiKeyRecord.principal_id == row.id,
                    ApiKeyRecord.revoked_at.is_(None),
                )
                .values(revoked_at=row.deleted_at, updated_at=row.deleted_at)
            )
            await session.execute(
                delete(RoleBindingRecord).where(
                    RoleBindingRecord.principal_type == "service_account",
                    RoleBindingRecord.principal_id == row.id,
                    RoleBindingRecord.organization_id == row.organization_id,
                )
            )
            audit(
                session,
                actor=actor,
                action="service_account.delete",
                resource_type="service_account",
                resource_id=row.id,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
            )

    @staticmethod
    async def require(session: AsyncSession, actor: AuthenticatedActor, account_id: str) -> ServiceAccountRecord:
        await require_user(session, actor)
        row = await session.get(ServiceAccountRecord, account_id)
        if row is None or row.deleted_at is not None:
            raise not_found()
        await authorize_workspace(
            session, actor=actor, workspace_id=row.workspace_id, action=WorkspaceAction.service_account_manage
        )
        return row

    @staticmethod
    async def _unique_name(
        session: AsyncSession, workspace_id: str, name: str, *, excluding: str | None = None
    ) -> None:
        query = select(ServiceAccountRecord.id).where(
            ServiceAccountRecord.workspace_id == workspace_id,
            ServiceAccountRecord.normalized_name == name.casefold(),
            ServiceAccountRecord.deleted_at.is_(None),
        )
        if excluding is not None:
            query = query.where(ServiceAccountRecord.id != excluding)
        if await session.scalar(query) is not None:
            raise identity_error("service_account_name_conflict", "An active Service Account already uses this name.")
