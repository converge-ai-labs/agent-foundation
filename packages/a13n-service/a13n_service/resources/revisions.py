"""The rules every revisioned kind (agents, skills) shares: immutable revision rows, numbering, digest and
publication, head resolution, metadata changes and list filters, archived and builtin heads, revision reads and
the default pointer."""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Annotated, Any, ClassVar, Protocol

from pydantic import BaseModel, StringConstraints
from sqlalchemy import (
    CheckConstraint,
    ColumnElement,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    and_,
    func,
    or_,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.orm.attributes import flag_modified

from a13n_service.infra import cursors
from a13n_service.infra.db import Base, Storage, assign, immutable, now, rules, short_session
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.rows import audit_row, record_update
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal

# Revision numbers are PostgreSQL `integer`s; a cursor past them is refused before it reaches SQL.
_MAX_NUMBER = 2**31 - 1

# A list's `q`: text a head's name or description contains, ignoring case.
Search = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class HeadRow(Protocol):
    KIND: ClassVar[str]
    __tablename__: str
    id: Mapped[str]
    organization_id: Mapped[str]
    workspace_id: Mapped[str]
    name: Mapped[str]
    description: Mapped[str]
    default_revision_id: Mapped[str | None]
    archived_at: Mapped[datetime | None]
    version: Mapped[int]
    updated_by_id: Mapped[str]

    @property
    def builtin(self) -> bool:
        """A head the deployment owns: it runs and duplicates like any other, but refuses every change."""
        ...


class RevisionColumns(Base):
    """The columns and constraints of every revision table; its rows are never updated or deleted.

    A kind maps its own table, adds the column naming its head and declares it as `HEAD_KEY`, with the head's
    table as `HEAD_TABLE`, its revision ID prefix as `ID_PREFIX` and its own name in errors as `KIND`.
    """

    __abstract__ = True
    KIND: ClassVar[str]
    HEAD_TABLE: ClassVar[str]
    HEAD_KEY: ClassVar[str]
    ID_PREFIX: ClassVar[str]

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    number: Mapped[int]
    config: Mapped[dict] = mapped_column(JSONB)
    digest: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None]
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @declared_attr.directive
    @classmethod
    def __table_args__(cls) -> tuple[Any, ...]:
        return (
            UniqueConstraint(cls.HEAD_KEY, "number"),
            UniqueConstraint(cls.HEAD_KEY, "id"),
            UniqueConstraint("workspace_id", "id"),
            ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
            ForeignKeyConstraint(
                ["workspace_id", cls.HEAD_KEY], [f"{cls.HEAD_TABLE}.workspace_id", f"{cls.HEAD_TABLE}.id"]
            ),
            CheckConstraint("number > 0", name="number"),
            rules(immutable(cls.__tablename__)),
        )


def config_digest(value: object) -> str:
    """SHA-256 of the canonical JSON a revision stores; runs record it as the revision's identity."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def resolve_head[H: HeadRow](
    session: AsyncSession, table: type[H], workspace_id: str, head_id: str, *, lock: bool = False
) -> H:
    """A head of the workspace by its ID. A locked head also refreshes any stale copy already loaded in this
    session."""
    query = select(table).where(table.workspace_id == workspace_id, table.id == head_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(query)
    if row is None:
        raise not_found(table.KIND, head_id)
    return row


def head_filter(table: type[HeadRow], *, q: str | None, archived: bool | None) -> ColumnElement[bool]:
    """The list filters every kind shares: `q` matches the name or the description by case-insensitive substring;
    `archived` keeps only archived heads, or only open ones when False."""
    conditions = []
    if q is not None:
        conditions.append(or_(*(column.icontains(q, autoescape=True) for column in (table.name, table.description))))
    if archived is not None:
        conditions.append(table.archived_at.is_not(None) if archived else table.archived_at.is_(None))
    return and_(true(), *conditions)


def require_open(head: HeadRow, if_match: str | None) -> None:
    """The head matches the caller's ETag and accepts changes: an archived head changes only by unarchiving, and a
    builtin one never."""
    require_match(if_match, head.id, head.version)
    if head.archived_at is not None:
        raise conflict(head.KIND, head.id, "archived")
    _refuse_builtin(head)


async def open_head[H: HeadRow](
    session: AsyncSession, table: type[H], workspace_id: str, head_id: str, if_match: str | None
) -> H:
    """The locked head, once it accepts changes."""
    head = await resolve_head(session, table, workspace_id, head_id, lock=True)
    require_open(head, if_match)
    return head


async def update_head(session: AsyncSession, actor: Principal, head: HeadRow, values: Mapping[str, object]) -> None:
    """Apply a metadata change to an open, locked head."""
    if record_update(session, actor, head, assign(head, values)):
        await session.flush()


async def set_archived(
    session: AsyncSession, actor: Principal, head: HeadRow, *, archived: bool, if_match: str | None
) -> None:
    """Archive or unarchive the locked head. An archived head keeps its revisions readable and pinned; it refuses
    new runs, revisions and pins."""
    require_match(if_match, head.id, head.version)
    _refuse_builtin(head)
    if (head.archived_at is not None) != archived:
        head.archived_at = await now(session) if archived else None
        head.updated_by_id = actor.id
        audit_row(session, actor, head, "archive" if archived else "unarchive")
        await session.flush()


def _refuse_builtin(head: HeadRow) -> None:
    if head.builtin:
        raise conflict(head.KIND, head.id, "builtin")


async def find_revision[R: RevisionColumns](session: AsyncSession, table: type[R], head_id: str, revision_id: str) -> R:
    """A revision of the head; a revision of any other head is not found."""
    row = await session.scalar(
        select(table).where(table.__table__.c[table.HEAD_KEY] == head_id, table.id == revision_id)
    )
    if row is None:
        raise not_found(table.KIND, revision_id)
    return row


async def get_revision[H: HeadRow, R: RevisionColumns, V: BaseModel](
    storage: Storage,
    actor: Principal,
    heads: type[H],
    table: type[R],
    view: Callable[[H, R], V],
    workspace_id: str,
    head_id: str,
    revision_id: str,
) -> V:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        head = await resolve_head(session, heads, scope.workspace_id, head_id)
        return view(head, await find_revision(session, table, head.id, revision_id))


async def list_revisions[H: HeadRow, R: RevisionColumns, V: BaseModel](
    storage: Storage,
    actor: Principal,
    heads: type[H],
    table: type[R],
    view: Callable[[H, R], V],
    workspace_id: str,
    head_id: str,
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[V], str | None]:
    """One page of the head's revisions, newest first."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        head = await resolve_head(session, heads, scope.workspace_id, head_id)
        rows, next_cursor = await revision_page(session, table, head.id, cursor=cursor, limit=limit)
        return [view(head, row) for row in rows], next_cursor


async def revision_page[R: RevisionColumns](
    session: AsyncSession, table: type[R], head_id: str, *, cursor: str | None, limit: int
) -> tuple[Sequence[R], str | None]:
    """One page of the head's revisions, newest first, and the cursor after its last one."""
    kind = table.__tablename__
    query = select(table).where(table.__table__.c[table.HEAD_KEY] == head_id)
    if cursor is not None:
        position = cursors.decode(cursor, kind, head_id)
        if len(position) != 1 or type(position[0]) is not int or not 0 < position[0] <= _MAX_NUMBER:
            raise ServiceError("invalid_cursor", "Invalid collection cursor")
        query = query.where(table.number < position[0])
    rows = (await session.scalars(query.order_by(table.number.desc()).limit(limit + 1))).all()
    if len(rows) <= limit:
        return rows, None
    return rows[:limit], cursors.encode(kind, head_id, rows[limit - 1].number)


async def publish[R: RevisionColumns](
    session: AsyncSession,
    head: HeadRow,
    table: type[R],
    config: BaseModel,
    *,
    actor: Principal,
    note: str | None,
    make_default: bool = True,
    **columns: object,
) -> tuple[R, bool]:
    """Append `config` as the head's next revision and stamp the head; the new revision becomes the default when
    `make_default` says so or the head has none.

    A configuration whose digest equals the current default's is no new revision: publishing it changes nothing
    and returns the default revision (False). The caller holds the head's row lock, or inserted the head in this
    transaction, so numbers never race. `columns` carries the kind's own revision columns, such as a skill's
    `package_ref`.
    """
    value = config.model_dump(mode="json")
    digest = config_digest(value)
    if head.default_revision_id is not None:
        current = await session.get_one(table, head.default_revision_id)
        if current.digest == digest:
            return current, False
    latest = await session.scalar(
        select(func.coalesce(func.max(table.number), 0)).where(table.__table__.c[table.HEAD_KEY] == head.id)
    )
    revision = table(
        id=new_object_id(table.ID_PREFIX),
        organization_id=head.organization_id,
        workspace_id=head.workspace_id,
        number=(latest or 0) + 1,
        config=value,
        digest=digest,
        note=note,
        created_by_id=actor.id,
        **{table.HEAD_KEY: head.id},
        **columns,
    )
    session.add(revision)
    if make_default or head.default_revision_id is None:
        head.default_revision_id = revision.id
    # A new revision changes the head's ETag even when its default and last editor stay the same.
    head.updated_by_id = actor.id
    flag_modified(head, "updated_by_id")
    return revision, True


def set_default(head: HeadRow, revision: RevisionColumns, *, actor: Principal) -> bool:
    """Point the head at `revision`; False, changing nothing, when it already is the default."""
    if head.default_revision_id == revision.id:
        return False
    head.default_revision_id = revision.id
    head.updated_by_id = actor.id
    return True
