"""API keys: long-lived bearer secrets, each confined to exactly one workspace.

Only a login session issues keys: a key-minted key would outlive its parent's expiry and revocation. Every
issuance path goes through `issue_key`, which checks the target principal's grants in the one workspace.
"""

from pydantic import JsonValue
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.audit import record
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import Storage, lock, now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, disabled, invalid, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.tenancy.access import (
    Access,
    administering_workspace,
    principal_for,
    refuse_archived,
    require_login_session,
    workspace_scope,
)
from a13n_service.tenancy.authorize import Principal, WorkspaceScope, allowed_verbs
from a13n_service.tenancy.credentials import new_secret
from a13n_service.tenancy.principals import principal_summaries, principal_summary
from a13n_service.tenancy.schemas import ApiKey, ApiKeyPage, IssuedKey, KeyCreate, PrincipalSummary, UserKeyCreate
from a13n_service.tenancy.service_accounts import lock_service_account
from a13n_service.tenancy.tables import ApiKeyRow, PrincipalRow, WorkspaceRow

KEY_PREFIX = "a13n_"


def key_view(row: ApiKeyRow, principal: PrincipalSummary) -> ApiKey:
    return ApiKey(
        id=row.id,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        principal=principal,
        name=row.name,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        created_by_id=row.created_by_id,
        version=row.version,
        created_at=row.created_at,
    )


def _audit_details(row: ApiKeyRow, actor: Principal) -> dict[str, JsonValue]:
    return {
        "principal_id": row.principal_id,
        "credential_workspace_id": actor.confinement.workspace_id if actor.confinement else None,
    }


async def issue_key(
    session: AsyncSession,
    access: Access,
    actor: Principal,
    principal: PrincipalRow,
    scope: WorkspaceScope,
    body: KeyCreate,
) -> IssuedKey:
    """The one issuance path. The caller has authorized issuing for `principal` in `scope`."""
    # Issuing is a change: an archived workspace refuses it, and archiving waits for an issuance under way.
    workspace = await session.get_one(
        WorkspaceRow, scope.workspace_id, with_for_update={"read": True}, populate_existing=True
    )
    refuse_archived(workspace)
    if principal.status != "active":
        raise disabled(principal.kind, principal.id)
    target = await principal_for(session, access, principal.id, confinement=scope)
    if not allowed_verbs(target, scope):
        raise ServiceError("forbidden", "Principal has no grant in this workspace", {"verb": "read"})
    if body.expires_at is not None and body.expires_at <= await now(session):
        raise invalid("expires_at", "in_the_past")
    secret = KEY_PREFIX + new_secret()
    row = ApiKeyRow(
        id=new_object_id("key"),
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        principal_id=principal.id,
        name=body.name,
        secret_hash=secret_hash(secret),
        expires_at=body.expires_at,
        created_by_id=actor.id,
    )
    session.add(row)
    await session.flush()
    record(
        session,
        scope,
        actor_id=actor.id,
        action="credential.create",
        target_kind="api_key",
        target_id=row.id,
        details=_audit_details(row, actor),
    )
    return IssuedKey(key=key_view(row, principal_summary(principal)), secret=secret)


async def create_user_key(storage: Storage, access: Access, actor: Principal, body: UserKeyCreate) -> IssuedKey:
    """A key for the calling user, confined to the explicitly selected workspace."""
    require_login_session(actor)
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, body.workspace_id, "read")
        principal = await lock(session, PrincipalRow, actor.id)
        assert principal is not None
        return await issue_key(session, access, actor, principal, scope, body)


async def create_service_account_key(
    storage: Storage, access: Access, actor: Principal, workspace_id: str, account_id: str, body: KeyCreate
) -> IssuedKey:
    require_login_session(actor)
    async with administering_workspace(storage, access, actor, workspace_id, action="credential.create") as (
        session,
        scope,
    ):
        account = await lock_service_account(session, scope, account_id)
        return await issue_key(session, access, actor, account, scope, body)


async def list_user_keys(
    storage: Storage, actor: Principal, *, workspace_id: str | None, limit: int, cursor: str | None
) -> ApiKeyPage:
    """The caller's own keys; an API key sees only keys of its own workspace."""
    query = select(ApiKeyRow).where(ApiKeyRow.principal_id == actor.id)
    if actor.confinement is not None:
        query = query.where(ApiKeyRow.workspace_id == actor.confinement.workspace_id)
    if workspace_id is not None:
        query = query.where(ApiKeyRow.workspace_id == workspace_id)
    async with short_session(storage) as session:
        return await _page(session, query, cursors.query_owner(actor.id, workspace_id), limit=limit, cursor=cursor)


async def list_workspace_keys(
    storage: Storage,
    access: Access,
    actor: Principal,
    workspace_id: str,
    *,
    principal_id: str | None,
    limit: int,
    cursor: str | None,
) -> ApiKeyPage:
    """Every key confined to a workspace, for its administrators; optionally one principal's."""
    async with administering_workspace(
        storage, access, actor, workspace_id, action="credential.list", reading=True
    ) as (
        session,
        scope,
    ):
        query = select(ApiKeyRow).where(ApiKeyRow.workspace_id == scope.workspace_id)
        if principal_id is not None:
            query = query.where(ApiKeyRow.principal_id == principal_id)
        owner = cursors.query_owner(scope.workspace_id, principal_id)
        return await _page(session, query, owner, limit=limit, cursor=cursor)


async def revoke_user_key(storage: Storage, actor: Principal, key_id: str, *, if_match: str | None) -> ApiKey:
    async with transaction(storage) as session:
        row = await lock(session, ApiKeyRow, key_id)
        confined_out = (
            actor.confinement is not None and row is not None and row.workspace_id != actor.confinement.workspace_id
        )
        if row is None or row.principal_id != actor.id or confined_out:
            raise not_found("api_key", key_id)
        return await _revoke(session, row, actor, if_match)


async def revoke_workspace_key(
    storage: Storage, access: Access, actor: Principal, workspace_id: str, key_id: str, *, if_match: str | None
) -> ApiKey:
    """Administrators revoke any key confined to their workspace, never one confined elsewhere."""
    async with administering_workspace(
        storage, access, actor, workspace_id, action="credential.revoke", require_active=False
    ) as (session, scope):
        row = await lock(session, ApiKeyRow, key_id)
        if row is None or row.workspace_id != scope.workspace_id:
            raise not_found("api_key", key_id)
        return await _revoke(session, row, actor, if_match)


async def _revoke(session: AsyncSession, row: ApiKeyRow, actor: Principal, if_match: str | None) -> ApiKey:
    require_match(if_match, row.id, row.version)
    if row.revoked_at is not None:
        raise conflict("api_key", row.id, "revoked")
    row.revoked_at = await now(session)
    await session.flush()
    record(
        session,
        WorkspaceScope(row.organization_id, row.workspace_id),
        actor_id=actor.id,
        action="credential.revoke",
        target_kind="api_key",
        target_id=row.id,
        details=_audit_details(row, actor),
    )
    principals = await principal_summaries(session, [row.principal_id])
    return key_view(row, principals[row.principal_id])


async def _page(
    session: AsyncSession, query: Select[tuple[ApiKeyRow]], owner: str, *, limit: int, cursor: str | None
) -> ApiKeyPage:
    rows, next_cursor = await cursors.id_page(
        session, query, ApiKeyRow.id, kind="api_keys", owner=owner, cursor=cursor, limit=limit
    )
    principals = await principal_summaries(session, (row.principal_id for row in rows))
    return ApiKeyPage(items=[key_view(row, principals[row.principal_id]) for row in rows], next_cursor=next_cursor)
