"""Grants: who holds which role where, changed only by administrators or accepted invitations.

An organization always keeps an active administrator, and a service account left without grants is retired.
A grant is never edited: a role change replaces its row. The organization's members are the principals holding a
grant there and the service accounts homed in one of its workspaces.
"""

from typing import Literal

from pydantic import JsonValue
from sqlalchemy import ColumnElement, SQLColumnExpression, exists, false, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.audit import Scoped, record
from a13n_service.infra.db import Storage, lock, unique_key
from a13n_service.infra.errors import conflict, invalid, not_found
from a13n_service.infra.ids import new_object_id
from a13n_service.tenancy.access import (
    Access,
    AdminPath,
    OrganizationPath,
    administering,
    effective_oidc_grant_ids,
    lock_member,
)
from a13n_service.tenancy.authorize import Principal, Scope, WorkspaceScope
from a13n_service.tenancy.credentials import revoke_api_keys
from a13n_service.tenancy.principals import principal_summaries, principal_summary
from a13n_service.tenancy.schemas import (
    GrantCreate,
    GrantPage,
    GrantUpdate,
    GrantView,
    MemberPage,
    PrincipalSummary,
)
from a13n_service.tenancy.tables import GrantRow, OIDCGrantRow, PrincipalRow, WorkspaceRow


def _grant_view(grant: GrantRow, principal: PrincipalSummary, *, overrides_sso: bool = False) -> GrantView:
    return GrantView(
        id=grant.id,
        organization_id=grant.organization_id,
        workspace_id=grant.workspace_id,
        principal=principal,
        role=grant.role,
        overrides_sso=overrides_sso,
        created_by_id=grant.created_by_id,
        created_at=grant.created_at,
    )


def _overrides_sso_for(scope: Scoped, principal_id: str | SQLColumnExpression[str]) -> ColumnElement[bool]:
    return exists().where(
        OIDCGrantRow.organization_id == scope.organization_id,
        OIDCGrantRow.workspace_id.is_not_distinct_from(scope.workspace_id),
        OIDCGrantRow.principal_id == principal_id,
    )


async def _manual_grant_view(session: AsyncSession, grant: GrantRow, principal: PrincipalRow) -> GrantView:
    scope = Scope(grant.organization_id, grant.workspace_id)
    overrides_sso = bool(await session.scalar(select(_overrides_sso_for(scope, grant.principal_id))))
    return _grant_view(grant, principal_summary(principal), overrides_sso=overrides_sso)


async def list_grants(
    storage: Storage,
    access: Access,
    actor: Principal,
    path: AdminPath,
    *,
    limit: int,
    cursor: str | None,
) -> GrantPage:
    """Grants at exactly this scope, each with its principal, so members can be shown by name."""
    async with administering(storage, access, actor, path, action="grant.list", reading=True) as (session, scope):
        owner = scope.workspace_id or scope.organization_id
        after = cursors.id_position(cursor, "grants", owner)
        manual = select(
            GrantRow.id,
            GrantRow.principal_id,
            GrantRow.role,
            GrantRow.created_by_id,
            GrantRow.created_at,
            literal("manual").label("source"),
            _overrides_sso_for(scope, GrantRow.principal_id).label("overrides_sso"),
        ).where(
            GrantRow.organization_id == scope.organization_id,
            GrantRow.workspace_id.is_not_distinct_from(scope.workspace_id),
            GrantRow.id > after,
        )
        defaults = effective_oidc_grant_ids(access).where(
            OIDCGrantRow.organization_id == scope.organization_id,
            OIDCGrantRow.workspace_id.is_not_distinct_from(scope.workspace_id),
        )
        oidc = select(
            OIDCGrantRow.id,
            OIDCGrantRow.principal_id,
            OIDCGrantRow.role,
            OIDCGrantRow.principal_id,
            OIDCGrantRow.created_at,
            literal("oidc"),
            false(),
        ).where(OIDCGrantRow.id.in_(defaults), OIDCGrantRow.id > after)
        # Both sources must use one snapshot: restoring a default between two reads could duplicate or hide a member.
        page = manual.union_all(oidc).subquery()
        rows = (await session.execute(select(page).order_by(page.c.id).limit(limit + 1))).all()
        selected = rows[:limit]
        principals = await principal_summaries(session, (row.principal_id for row in selected))
        items = [
            GrantView(
                id=row.id,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                principal=principals[row.principal_id],
                role=row.role,
                source=row.source,
                overrides_sso=row.overrides_sso,
                created_by_id=row.created_by_id,
                created_at=row.created_at,
            )
            for row in selected
        ]
        next_cursor = cursors.encode("grants", owner, selected[-1].id) if len(rows) > limit else None
        return GrantPage(items=items, next_cursor=next_cursor)


async def create_grant(
    storage: Storage, access: Access, actor: Principal, path: AdminPath, body: GrantCreate
) -> GrantView:
    """Give a role to a principal already in the organization; new people join through invitations."""
    async with administering(storage, access, actor, path, action="grant.create") as (session, scope):
        access.check_role(body.role)
        principal = await lock_organization_member(session, body.principal_id, scope.organization_id)
        grant = await add_grant(session, principal, scope, body.role, actor_id=actor.id)
        return await _manual_grant_view(session, grant, principal)


async def change_role(
    storage: Storage, access: Access, actor: Principal, path: AdminPath, grant_id: str, body: GrantUpdate
) -> GrantView:
    """Give the grant's principal another role at the same scope. The grant is replaced, so the result has a new
    ID and the old one is gone: a later change or removal naming it is `not_found`."""
    async with administering(storage, access, actor, path, action="grant.update") as (session, scope):
        access.check_role(body.role)
        grant = await _grant_at(session, scope, grant_id)
        principal = await lock_member(session, grant.principal_id)
        if grant.role != body.role:
            grant = await replace_grant(session, access, grant, principal, body.role, actor_id=actor.id)
        return await _manual_grant_view(session, grant, principal)


async def list_members(
    storage: Storage,
    access: Access,
    actor: Principal,
    organization_id: str,
    *,
    kind: Literal["user", "service_account"] | None,
    limit: int,
    cursor: str | None,
) -> MemberPage:
    """The organization's members, for its administrators, such as to choose whom to grant a workspace role."""
    async with administering(
        storage, access, actor, OrganizationPath(organization_id), action="member.list", reading=True
    ) as (session, scope):
        query = select(PrincipalRow).where(member_of(scope.organization_id))
        if kind is not None:
            query = query.where(PrincipalRow.kind == kind)
        rows, next_cursor = await cursors.id_page(
            session,
            query,
            PrincipalRow.id,
            kind="members",
            owner=cursors.query_owner(scope.organization_id, kind),
            cursor=cursor,
            limit=limit,
        )
    return MemberPage(items=[principal_summary(row) for row in rows], next_cursor=next_cursor)


def member_of(*organization_ids: str) -> ColumnElement[bool]:
    """Membership in any of these organizations, correlated to the selected principal."""
    return or_(
        exists().where(GrantRow.principal_id == PrincipalRow.id, GrantRow.organization_id.in_(organization_ids)),
        exists().where(
            OIDCGrantRow.principal_id == PrincipalRow.id, OIDCGrantRow.organization_id.in_(organization_ids)
        ),
        exists().where(
            WorkspaceRow.id == PrincipalRow.home_workspace_id, WorkspaceRow.organization_id.in_(organization_ids)
        ),
    )


async def lock_organization_member(session: AsyncSession, principal_id: str, organization_id: str) -> PrincipalRow:
    """A member of the organization, locked as `lock_member` does; anyone else is not found, so no identity from
    outside the organization is revealed."""
    principal = await session.scalar(
        select(PrincipalRow)
        .where(PrincipalRow.id == principal_id, member_of(organization_id))
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if principal is None:
        raise not_found("principal", principal_id)
    return principal


async def _grant_at(session: AsyncSession, scope: Scoped, grant_id: str) -> GrantRow:
    """A grant at exactly this scope; one elsewhere is not found."""
    grant = await session.get(GrantRow, grant_id)
    if grant is None or (grant.organization_id, grant.workspace_id) != (scope.organization_id, scope.workspace_id):
        raise not_found("grant", grant_id)
    return grant


async def add_grant(
    session: AsyncSession,
    principal: PrincipalRow,
    scope: Scoped,
    role: str,
    *,
    actor_id: str,
    details: dict[str, JsonValue] | None = None,
) -> GrantRow:
    """Insert and audit one grant; service accounts are granted only in their home workspace."""
    if principal.kind == "service_account" and principal.home_workspace_id != scope.workspace_id:
        raise invalid("principal_id", "service_account_outside_home_workspace")
    grant = GrantRow(
        id=new_object_id("rb"),
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        principal_id=principal.id,
        role=role,
        created_by_id=actor_id,
    )
    session.add(grant)
    with unique_key("grant", "uq_grants_scope", principal.id):
        await session.flush()
    await session.refresh(grant)
    record(
        session,
        scope,
        actor_id=actor_id,
        action="grant.create",
        target_kind="grant",
        target_id=grant.id,
        details={"principal_id": principal.id, "role": role, **(details or {})},
    )
    return grant


async def delete_grant(storage: Storage, access: Access, actor: Principal, path: AdminPath, grant_id: str) -> None:
    """Remove access; like revoking a key, this is offboarding and allowed in an archived workspace too."""
    async with administering(storage, access, actor, path, action="grant.delete", require_active=False) as (
        session,
        scope,
    ):
        grant = await _grant_at(session, scope, grant_id)
        # Principal before grant: the lock order of every membership change.
        principal = await lock_member(session, grant.principal_id)
        await remove_grant(session, access, grant, actor_id=actor.id)
        await retire_ungranted_account(session, principal, actor_id=actor.id)


async def remove_grant(
    session: AsyncSession,
    access: Access,
    grant: GrantRow,
    *,
    actor_id: str,
    details: dict[str, JsonValue] | None = None,
    replacement: str | None = None,
) -> None:
    """Delete one grant under the organization lock, or replace it with the role `replacement`; an organization
    always keeps an active administrator, so only a change that takes away `admin` needs another one."""
    admin_roles = access.roles_with("admin")
    if (
        grant.workspace_id is None
        and grant.role in admin_roles
        and replacement not in admin_roles
        and not await other_administrator(session, access, grant.organization_id, grant.principal_id)
    ):
        raise conflict("grant", grant.id, "last_organization_admin")
    await session.delete(grant)
    await session.flush()
    record(
        session,
        Scope(grant.organization_id, grant.workspace_id),
        actor_id=actor_id,
        action="grant.delete",
        target_kind="grant",
        target_id=grant.id,
        details={"principal_id": grant.principal_id, "role": grant.role, **(details or {})},
    )


async def replace_grant(
    session: AsyncSession,
    access: Access,
    held: GrantRow,
    principal: PrincipalRow,
    role: str,
    *,
    actor_id: str,
    details: dict[str, JsonValue] | None = None,
) -> GrantRow:
    """Change a role by replacing the grant's row in the caller's transaction, so the principal is never observed
    without a grant and never retired; the new grant records the one it replaces."""
    await remove_grant(session, access, held, actor_id=actor_id, details=details, replacement=role)
    scope = Scope(held.organization_id, held.workspace_id)
    return await add_grant(
        session, principal, scope, role, actor_id=actor_id, details={"replaces": held.id, **(details or {})}
    )


async def other_administrator(session: AsyncSession, access: Access, organization_id: str, principal_id: str) -> bool:
    """Whether a different active principal holds a manual organization admin grant."""
    found = await session.scalar(
        select(GrantRow.id)
        .join(PrincipalRow, PrincipalRow.id == GrantRow.principal_id)
        .where(
            GrantRow.organization_id == organization_id,
            GrantRow.workspace_id.is_(None),
            GrantRow.role.in_(access.roles_with("admin")),
            GrantRow.principal_id != principal_id,
            PrincipalRow.status == "active",
        )
        .limit(1)
    )
    return found is not None


async def retire_ungranted_account(session: AsyncSession, principal: PrincipalRow, *, actor_id: str) -> None:
    """A service account without grants is disabled and keeps no live keys; its identity stays for history."""
    if principal.kind != "service_account":
        return
    if await session.scalar(select(GrantRow.id).where(GrantRow.principal_id == principal.id).limit(1)):
        return
    revoked = await revoke_api_keys(session, principal.id)
    if principal.status == "disabled" and not revoked:
        return
    # A status change locks the principal FOR UPDATE, as every disable does (see `principal_for`).
    await lock(session, PrincipalRow, principal.id)
    principal.status = "disabled"
    home = await session.get_one(WorkspaceRow, principal.home_workspace_id)
    record(
        session,
        WorkspaceScope(home.organization_id, home.id),
        actor_id=actor_id,
        action="service_account.disable",
        target_kind="service_account",
        target_id=principal.id,
        details={"reason": "no_grants", "revoked_keys": revoked},
    )
