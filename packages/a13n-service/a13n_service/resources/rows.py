"""What every resource kind does with its rows: find one in scope, apply a partial change, audit it, and report a
key another row already holds.

A row class names its resource kind in `KIND`, which its errors and audit events carry.
"""

from collections.abc import Sequence
from typing import ClassVar, Protocol

from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from a13n_service.infra.audit import record
from a13n_service.infra.errors import disabled, not_found
from a13n_service.tenancy.access import refuse_archived
from a13n_service.tenancy.authorize import (
    ExecutionAuthority,
    Principal,
    Scope,
    Verb,
    WorkspaceScope,
    allowed_verbs,
    authorize,
)
from a13n_service.tenancy.tables import WorkspaceRow


class Row(Protocol):
    """A row of one workspace."""

    KIND: ClassVar[str]
    id: Mapped[str]
    organization_id: Mapped[str]
    workspace_id: Mapped[str]


class EditedRow(Row, Protocol):
    updated_by_id: Mapped[str]


class _Switchable(Row, Protocol):
    enabled: Mapped[bool]


async def find_row[R: Row](
    session: AsyncSession,
    actor: Principal,
    row_type: type[R],
    scope: WorkspaceScope,
    row_id: str,
    verb: Verb,
    *,
    lock: bool = False,
    require_active: bool = True,
) -> R:
    """Row `row_id` of the workspace, on which the actor may `verb`.

    A row the actor cannot read is not found, revealing nothing. Every verb but `read` is refused on a row of an
    archived workspace unless `require_active=False` (offboarding): resource rows apply that rule here, as
    `workspace_scope` does for workspace paths.
    """
    query = select(row_type).where(row_type.id == row_id, row_type.workspace_id == scope.workspace_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(query)
    if row is None or "read" not in allowed_verbs(actor, scope):
        raise not_found(row_type.KIND, row_id)
    authorize(actor, scope, verb)
    if require_active and verb != "read":
        refuse_archived(await session.get_one(WorkspaceRow, scope.workspace_id))
    return row


async def usable_row[R: _Switchable](
    session: AsyncSession,
    actor: Principal,
    row_type: type[R],
    scope: WorkspaceScope,
    row_id: str,
    *,
    verb: Verb,
    authority: ExecutionAuthority | None,
) -> R:
    """An enabled row of the workspace the actor may `verb`, read in the caller's session."""
    row = await session.scalar(
        select(row_type).where(row_type.id == row_id, row_type.workspace_id == scope.workspace_id)
    )
    if row is None:
        raise not_found(row_type.KIND, row_id)
    authorize(actor, scope, verb, authority=authority)
    if not row.enabled:
        raise disabled(row_type.KIND, row_id)
    return row


def given(body: BaseModel, *names: str) -> dict[str, object]:
    """The named fields of a partial update that carry a value; one left out or null keeps the row's."""
    return {name: value for name in names if (value := getattr(body, name)) is not None}


def record_update(session: AsyncSession, actor: Principal, row: EditedRow, changed: Sequence[str]) -> bool:
    """Stamp and audit an update of the `changed` fields; one that changes nothing keeps the version and records
    nothing (False)."""
    if not changed:
        return False
    row.updated_by_id = actor.id
    audit_row(session, actor, row, "update", {"fields": list(changed)})
    return True


def audit_row(
    session: AsyncSession, actor: Principal, row: Row, verb: str, details: dict[str, JsonValue] | None = None
) -> None:
    """Record `<kind>.<verb>` on the row, in its own scope and the changing transaction."""
    record(
        session,
        _scope(row),
        actor_id=actor.id,
        action=f"{row.KIND}.{verb}",
        target_kind=row.KIND,
        target_id=row.id,
        details=details,
    )


def _scope(row: Row) -> Scope:
    return Scope(row.organization_id, row.workspace_id)
