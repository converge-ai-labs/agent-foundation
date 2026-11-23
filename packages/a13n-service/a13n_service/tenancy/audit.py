"""Audit reads: a workspace's or an organization's events for administrators, and each user's own trail."""

from collections.abc import Sequence

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import Storage, short_session
from a13n_service.tenancy.access import Access, AdminPath, administering, require_login_session
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.principals import principal_summaries
from a13n_service.tenancy.schemas import AuditEvent, AuditPage


async def list_audit_events(
    storage: Storage, access: Access, actor: Principal, path: AdminPath, *, limit: int, cursor: str | None
) -> AuditPage:
    """Newest first. Account-wide events (logins, password changes) belong to no tenant and never appear."""
    async with administering(storage, access, actor, path, action="audit.read", reading=True) as (session, scope):
        query = select(AuditEventRow).where(AuditEventRow.organization_id == scope.organization_id)
        if scope.workspace_id is not None:
            query = query.where(AuditEventRow.workspace_id == scope.workspace_id)
        return await _page(session, query, scope.workspace_id or scope.organization_id, limit=limit, cursor=cursor)


async def list_account_events(storage: Storage, actor: Principal, *, limit: int, cursor: str | None) -> AuditPage:
    """Newest first: what the caller did in any tenant, and what happened to their account, including the
    account-wide events no tenant feed shows (password changes, reset requests, the operator's switch)."""
    require_login_session(actor)
    # Two disjoint parts, each paged along its own index: what the caller did, and what else touched the account.
    parts = (
        select(AuditEventRow).where(AuditEventRow.actor_id == actor.id),
        select(AuditEventRow).where(
            or_(
                AuditEventRow.target_kind == "user",
                and_(AuditEventRow.target_kind == "member", AuditEventRow.action == "user.password.reset_assisted"),
            ),
            AuditEventRow.target_id == actor.id,
            AuditEventRow.actor_id.is_distinct_from(actor.id),
        ),
    )
    async with short_session(storage) as session:
        return await _page(session, parts, actor.id, limit=limit, cursor=cursor)


async def _page(
    session: AsyncSession,
    query: Select[tuple[AuditEventRow]] | Sequence[Select[tuple[AuditEventRow]]],
    owner: str,
    *,
    limit: int,
    cursor: str | None,
) -> AuditPage:
    events, next_cursor = await cursors.keyset_page(
        session,
        query,
        (AuditEventRow.occurred_at, AuditEventRow.id),
        kind="audit",
        owner=owner,
        cursor=cursor,
        limit=limit,
        newest_first=True,
    )
    actors = await principal_summaries(session, (event.actor_id for event in events if event.actor_id))
    items = [
        AuditEvent(
            id=event.id,
            organization_id=event.organization_id,
            workspace_id=event.workspace_id,
            actor_id=event.actor_id,
            actor=actors.get(event.actor_id) if event.actor_id is not None else None,
            action=event.action,
            target_kind=event.target_kind,
            target_id=event.target_id,
            outcome=event.outcome,
            details=event.details,
            occurred_at=event.occurred_at,
        )
        for event in events
    ]
    return AuditPage(items=items, next_cursor=next_cursor)
