"""Intentional human-facing Inbox items, independent of Thread unread state."""

from collections.abc import Sequence
from datetime import UTC, datetime
from secrets import token_hex
from typing import Literal

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.sqlite import insert

from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.inbox_models import InboxItem, InboxKind

from .database import DatabaseSessions, short_session, transaction
from .models import InboxItemRecord, ThreadConfigurationRecord, ThreadRecord

_ROW = (InboxItemRecord, ThreadRecord.title, ThreadRecord.archived, ThreadConfigurationRecord.project_id)


def _item(row: Sequence[object]) -> InboxItem:
    record, title, archived, project_id = row
    assert isinstance(record, InboxItemRecord)
    return InboxItem(
        item_id=record.item_id,
        thread_id=record.thread_id,
        tool_call_id=record.tool_call_id,
        kind=record.kind,
        title=record.title,
        message=record.message,
        created_at=record.created_at,
        read_at=record.read_at,
        done_at=record.done_at,
        thread_title=title if isinstance(title, str) else None,
        thread_archived=bool(archived),
        project_id=project_id if isinstance(project_id, str) else None,
    )


def _joined():
    return (
        select(*_ROW)
        .join(ThreadRecord, ThreadRecord.thread_id == InboxItemRecord.thread_id)
        .join(ThreadConfigurationRecord, ThreadConfigurationRecord.thread_id == InboxItemRecord.thread_id)
    )


class InboxRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def post(
        self, *, thread_id: str, tool_call_id: str, kind: InboxKind, title: str, message: str
    ) -> tuple[InboxItem, bool]:
        """Insert once per tool call; a replayed call returns the original item."""
        async with transaction(self._sessions) as session:
            inserted = await session.scalar(
                insert(InboxItemRecord)
                .values(
                    item_id=f"inbox_{token_hex(8)}",
                    thread_id=thread_id,
                    tool_call_id=tool_call_id,
                    kind=kind,
                    title=title,
                    message=message,
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=["thread_id", "tool_call_id"])
                .returning(InboxItemRecord.item_id)
            )
            created = inserted is not None
            row = (
                await session.execute(
                    _joined().where(
                        InboxItemRecord.thread_id == thread_id, InboxItemRecord.tool_call_id == tool_call_id
                    )
                )
            ).one()
            return _item(row), created

    async def get(self, item_id: str) -> InboxItem:
        async with short_session(self._sessions) as session:
            row = (await session.execute(_joined().where(InboxItemRecord.item_id == item_id))).one_or_none()
        if row is None:
            raise ThreadError("Inbox item does not exist.", code="inbox_item_missing")
        return _item(row)

    async def list(
        self, *, state: Literal["open", "done"], before: str | None, limit: int
    ) -> tuple[tuple[InboxItem, ...], str | None]:
        if not 1 <= limit <= 100:
            raise ThreadError("Inbox page is outside supported bounds.", code="inbox_page_invalid")
        statement = _joined().where(
            InboxItemRecord.done_at.is_(None) if state == "open" else InboxItemRecord.done_at.is_not(None)
        )
        async with short_session(self._sessions) as session:
            if before is not None:
                anchor = await session.get(InboxItemRecord, before)
                if anchor is None:
                    raise ThreadError("Inbox cursor is unavailable.", code="inbox_cursor_invalid")
                statement = statement.where(
                    or_(
                        InboxItemRecord.created_at < anchor.created_at,
                        (InboxItemRecord.created_at == anchor.created_at) & (InboxItemRecord.item_id < anchor.item_id),
                    )
                )
            rows = (
                await session.execute(
                    statement.order_by(InboxItemRecord.created_at.desc(), InboxItemRecord.item_id.desc()).limit(
                        limit + 1
                    )
                )
            ).all()
        items = tuple(_item(row) for row in rows[:limit])
        return items, items[-1].item_id if len(rows) > limit else None

    async def unread_count(self) -> int:
        async with short_session(self._sessions) as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(InboxItemRecord)
                    .where(InboxItemRecord.read_at.is_(None), InboxItemRecord.done_at.is_(None))
                )
                or 0
            )

    async def update(self, item_id: str, *, read: bool | None = None, done: bool | None = None) -> InboxItem:
        now = datetime.now(UTC)
        # Reading keeps the first observation time; Done implies read.
        first_read = func.coalesce(InboxItemRecord.read_at, now)
        values: dict[str, object] = {}
        if read is not None:
            values["read_at"] = first_read if read else None
        if done is not None:
            values["done_at"] = now if done else None
            if done and read is None:
                values["read_at"] = first_read
        if values:
            async with transaction(self._sessions) as session:
                await session.execute(
                    update(InboxItemRecord).where(InboxItemRecord.item_id == item_id).values(**values)
                )
        return await self.get(item_id)
