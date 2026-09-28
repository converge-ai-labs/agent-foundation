"""Service accounts: program identities with an immutable home workspace, granted and keyed only there.

Deleting one retires it (grants removed, keys revoked, disabled); the principal row stays for history. Retiring
or disabling one is offboarding, allowed in an archived workspace too; every other change needs an active one.
"""

from collections.abc import Sequence

from pydantic import JsonValue
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, lock
from a13n_service.infra.errors import conflict, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.tenancy.access import Access, administering_workspace
from a13n_service.tenancy.authorize import Principal, WorkspaceScope
from a13n_service.tenancy.grants import add_grant, remove_grant, replace_grant, retire_ungranted_account
from a13n_service.tenancy.schemas import ServiceAccount, ServiceAccountCreate, ServiceAccountPage, ServiceAccountUpdate
from a13n_service.tenancy.tables import GrantRow, PrincipalRow


def _view(row: PrincipalRow, scope: WorkspaceScope, role: str | None) -> ServiceAccount:
    # The `account_description` check keeps it set on every service account.
    assert row.description is not None
    return ServiceAccount(
        id=row.id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        name=row.name,
        description=row.description,
        status="active" if row.status == "active" else "disabled",
        role=role,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _accounts(scope: WorkspaceScope) -> Select[tuple[PrincipalRow]]:
    return select(PrincipalRow).where(
        PrincipalRow.kind == "service_account", PrincipalRow.home_workspace_id == scope.workspace_id
    )


async def _views(session: AsyncSession, scope: WorkspaceScope, rows: Sequence[PrincipalRow]) -> list[ServiceAccount]:
    """Each account with the role of its home grant, if it still has one."""
    roles = await session.execute(
        select(GrantRow.principal_id, GrantRow.role).where(
            GrantRow.workspace_id == scope.workspace_id, GrantRow.principal_id.in_([row.id for row in rows])
        )
    )
    role_of = dict(roles.tuples().all())
    return [_view(row, scope, role_of.get(row.id)) for row in rows]


async def create_service_account(
    storage: Storage, access: Access, actor: Principal, workspace_id: str, body: ServiceAccountCreate
) -> ServiceAccount:
    async with administering_workspace(storage, access, actor, workspace_id, action="service_account.create") as (
        session,
        scope,
    ):
        access.check_role(body.role)
        row = PrincipalRow(
            id=new_object_id("sa"),
            kind="service_account",
            name=body.name,
            description=body.description,
            home_workspace_id=scope.workspace_id,
        )
        session.add(row)
        await session.flush()
        await add_grant(session, row, scope, body.role, actor_id=actor.id)
        record(
            session,
            scope,
            actor_id=actor.id,
            action="service_account.create",
            target_kind="service_account",
            target_id=row.id,
        )
        return _view(row, scope, body.role)


async def list_service_accounts(
    storage: Storage, access: Access, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> ServiceAccountPage:
    async with administering_workspace(
        storage, access, actor, workspace_id, action="service_account.list", reading=True
    ) as (session, scope):
        rows, next_cursor = await cursors.id_page(
            session,
            _accounts(scope),
            PrincipalRow.id,
            kind="service_accounts",
            owner=scope.workspace_id,
            cursor=cursor,
            limit=limit,
        )
        return ServiceAccountPage(items=await _views(session, scope, rows), next_cursor=next_cursor)


async def get_service_account(
    storage: Storage, access: Access, actor: Principal, workspace_id: str, account_id: str
) -> ServiceAccount:
    async with administering_workspace(
        storage, access, actor, workspace_id, action="service_account.read", reading=True
    ) as (session, scope):
        row = await session.scalar(_accounts(scope).where(PrincipalRow.id == account_id))
        if row is None:
            raise not_found("service_account", account_id)
        [view] = await _views(session, scope, [row])
        return view


async def update_service_account(
    storage: Storage,
    access: Access,
    actor: Principal,
    workspace_id: str,
    account_id: str,
    body: ServiceAccountUpdate,
    *,
    if_match: str | None,
) -> ServiceAccount:
    async with administering_workspace(
        storage, access, actor, workspace_id, action="service_account.update", require_active=not _only_disables(body)
    ) as (session, scope):
        if body.role is not None:
            access.check_role(body.role)
        row = await _lock_account(session, scope, account_id, if_match)
        grant = await session.scalar(
            select(GrantRow)
            .where(GrantRow.principal_id == row.id, GrantRow.workspace_id == scope.workspace_id)
            .with_for_update()
        )
        changed: list[JsonValue] = []
        if body.name is not None and body.name != row.name:
            row.name = body.name
            changed.append("name")
        if body.description is not None and body.description != row.description:
            row.description = body.description
            changed.append("description")
        if body.role is not None and (grant is None or grant.role != body.role):
            grant = (
                await add_grant(session, row, scope, body.role, actor_id=actor.id)
                if grant is None
                else await replace_grant(session, access, grant, row, body.role, actor_id=actor.id)
            )
            changed.append("role")
        if body.status is not None and body.status != row.status:
            if body.status == "active" and grant is None:
                raise conflict("service_account", row.id, "no_grant")
            row.status = body.status
            changed.append("status")
        if changed:
            await session.flush()
            record(
                session,
                scope,
                actor_id=actor.id,
                action="service_account.update",
                target_kind="service_account",
                target_id=row.id,
                details={"fields": changed},
            )
        return _view(row, scope, grant.role if grant else None)


async def delete_service_account(
    storage: Storage, access: Access, actor: Principal, workspace_id: str, account_id: str, *, if_match: str | None
) -> ServiceAccount:
    """Retire: remove its grants, revoke its keys and disable it. History keeps the identity."""
    async with administering_workspace(
        storage, access, actor, workspace_id, action="service_account.delete", require_active=False
    ) as (session, scope):
        row = await _lock_account(session, scope, account_id, if_match)
        for grant in (await session.scalars(select(GrantRow).where(GrantRow.principal_id == row.id))).all():
            await remove_grant(session, access, grant, actor_id=actor.id)
        await retire_ungranted_account(session, row, actor_id=actor.id)
        await session.flush()
        return _view(row, scope, None)


def _only_disables(body: ServiceAccountUpdate) -> bool:
    return body.model_dump(exclude_none=True) == {"status": "disabled"}


async def lock_service_account(session: AsyncSession, scope: WorkspaceScope, account_id: str) -> PrincipalRow:
    """A service account homed in the scope's workspace, locked."""
    row = await lock(session, PrincipalRow, account_id)
    if row is None or row.kind != "service_account" or row.home_workspace_id != scope.workspace_id:
        raise not_found("service_account", account_id)
    return row


async def _lock_account(
    session: AsyncSession, scope: WorkspaceScope, account_id: str, if_match: str | None
) -> PrincipalRow:
    row = await lock_service_account(session, scope, account_id)
    require_match(if_match, row.id, row.version)
    return row
