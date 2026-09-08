"""Workspace-bound Personal and Service Account keys with immutable secrets."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.ids import new_object_id
from a13n_service.storage import short_session
from a13n_service.temporal import utc_now

from ..auth.credentials import require_key_eligible
from ..auth.passwords import new_token, token_hash
from ..auth.sessions import require_user
from ..authorization import WorkspaceAction, authorize_workspace
from ..domain import AuthenticatedActor
from ..models import ApiKeyRecord, ServiceAccountRecord, WorkspaceRecord
from ..schemas import ApiKey, CreatedKey, CreateKeyRequest
from ..service_common import audit, identity_error, identity_transaction, not_found


async def authorize_key_management(session: AsyncSession, actor: AuthenticatedActor, key: ApiKeyRecord) -> None:
    user = await require_user(session, actor)
    if key.principal_type == "user" and key.principal_id == user.id:
        # Creation checks actual access; metadata/revocation remains possible for
        # the owner after a key has expired or its old Workspace access was removed.
        if actor.boundary_workspace_id is not None and actor.boundary_workspace_id != key.boundary_id:
            raise not_found()
        if actor.boundary_organization_id is not None and actor.boundary_organization_id != key.organization_id:
            raise not_found()
        return
    await authorize_workspace(session, actor=actor, workspace_id=key.boundary_id, action=WorkspaceAction.api_key_manage)


class ApiKeyService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self,
        actor: AuthenticatedActor,
        workspace_id: str,
        request: CreateKeyRequest,
        *,
        service_account_id: str | None = None,
    ) -> CreatedKey:
        async with short_session(self._sessions) as session:
            workspace = await session.get(WorkspaceRecord, workspace_id)
            if workspace is None or workspace.deleted_at is not None:
                raise not_found()
            organization_id = workspace.organization_id
        secret, now = new_token(), utc_now()
        expires_at = request.expires_at if "expires_at" in request.model_fields_set else now + timedelta(days=90)
        if expires_at is not None and expires_at <= now:
            raise identity_error(
                "invalid_expiry", "The key expiry must be in the future.", ErrorCategory.invalid_request
            )
        async with identity_transaction(self._sessions, organization_id) as session:
            user = await require_user(session, actor)
            if service_account_id is not None:
                await authorize_workspace(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.service_account_manage
                )
                account = await session.get(ServiceAccountRecord, service_account_id)
                if (
                    account is None
                    or account.workspace_id != workspace_id
                    or account.deleted_at is not None
                    or account.status != "active"
                ):
                    raise not_found()
                principal_type, principal_id = "service_account", account.id
            else:
                principal_type, principal_id = "user", user.id
                if actor.boundary_workspace_id is not None and actor.boundary_workspace_id != workspace_id:
                    raise not_found()
                if actor.boundary_organization_id is not None and actor.boundary_organization_id != organization_id:
                    raise not_found()
            row = ApiKeyRecord(
                id=new_object_id("key"),
                principal_type=principal_type,
                principal_id=principal_id,
                organization_id=organization_id,
                boundary_type="workspace",
                boundary_id=workspace_id,
                name=request.name,
                secret_hash=token_hash(secret),
                expires_at=expires_at,
                revoked_at=None,
                created_at=now,
                updated_at=now,
            )
            await require_key_eligible(session, row)
            session.add(row)
            audit(
                session,
                actor=actor,
                action="api_key.create",
                resource_type="api_key",
                resource_id=row.id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                now=now,
            )
            return CreatedKey(key=ApiKey.model_validate(row), bearer=f"afk_{row.id}.{secret}")

    async def get(self, actor: AuthenticatedActor, key_id: str) -> ApiKey:
        async with short_session(self._sessions) as session:
            row = await session.get(ApiKeyRecord, key_id)
            if row is None:
                raise not_found()
            await authorize_key_management(session, actor, row)
            return ApiKey.model_validate(row)

    async def revoke(self, actor: AuthenticatedActor, key_id: str) -> ApiKey:
        key = await self.get(actor, key_id)
        async with identity_transaction(self._sessions, key.organization_id) as session:
            row = await session.get(ApiKeyRecord, key_id)
            if row is None:
                raise not_found()
            await authorize_key_management(session, actor, row)
            if row.revoked_at is None:
                row.revoked_at = row.updated_at = utc_now()
                audit(
                    session,
                    actor=actor,
                    action="api_key.revoke",
                    resource_type="api_key",
                    resource_id=row.id,
                    organization_id=row.organization_id,
                    workspace_id=row.boundary_id,
                )
            return ApiKey.model_validate(row)
