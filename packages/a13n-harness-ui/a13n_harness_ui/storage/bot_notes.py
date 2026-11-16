"""Agent-managed Markdown slots, independent of Bot execution and Thread control."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotNote
from a13n_harness_ui.errors import ThreadError

from .database import DatabaseSessions, short_session, transaction
from .models import BotNoteRecord, ThreadConfigurationRecord, ThreadRecord


async def _target(session: AsyncSession, thread_id: str) -> None:
    thread = await session.get(ThreadRecord, thread_id)
    configuration = await session.get(ThreadConfigurationRecord, thread_id)
    if (
        thread is None
        or thread.parent_thread_id is not None
        or thread.memory_scope is not None
        or configuration is None
        or configuration.agent_source_kind in {"live", "bot"}
        or thread_id == BOT_THREAD_ID
    ):
        raise ThreadError("Bot Notes require an existing ordinary root Thread.", code="bot_note_target_invalid")


def _authorize(caller: str, target: str) -> None:
    if caller != BOT_THREAD_ID and caller != target:
        raise ThreadError("A Thread can access only its own Bot Note.", code="bot_note_access_denied")


def _note(row: BotNoteRecord | None, thread_id: str) -> BotNote:
    return (
        BotNote(thread_id=thread_id)
        if row is None
        else BotNote(
            thread_id=row.thread_id,
            content=row.content,
            updated_at=row.updated_at,
            last_writer_thread_id=row.last_writer_thread_id,
        )
    )


class BotNoteRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions
        self.on_change: Callable[[str], Awaitable[None]] | None = None

    async def read(self, *, caller: str, thread_id: str) -> BotNote:
        _authorize(caller, thread_id)
        async with short_session(self._sessions) as session:
            await _target(session, thread_id)
            return _note(await session.get(BotNoteRecord, thread_id), thread_id)

    async def write(self, *, caller: str, thread_id: str, content: str) -> BotNote:
        _authorize(caller, thread_id)
        if len(content.encode("utf-8")) > 16 * 1024:
            raise ThreadError("Bot Notes are limited to 16 KiB UTF-8.", code="bot_note_too_large")
        async with transaction(self._sessions) as session:
            await _target(session, thread_id)
            row = await session.get(BotNoteRecord, thread_id)
            if not content.strip():
                if row is None:
                    return BotNote(thread_id=thread_id)
                await session.execute(delete(BotNoteRecord).where(BotNoteRecord.thread_id == thread_id))
                result = BotNote(thread_id=thread_id)
            else:
                if row is not None and row.content == content:
                    return _note(row, thread_id)
                now = datetime.now(UTC)
                await session.execute(
                    insert(BotNoteRecord)
                    .values(thread_id=thread_id, content=content, updated_at=now, last_writer_thread_id=caller)
                    .on_conflict_do_update(
                        index_elements=["thread_id"],
                        set_={"content": content, "updated_at": now, "last_writer_thread_id": caller},
                    )
                )
                result = BotNote(thread_id=thread_id, content=content, updated_at=now, last_writer_thread_id=caller)
        if self.on_change is not None:
            await self.on_change(thread_id)
        return result

    async def list(self, *, caller: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        if caller != BOT_THREAD_ID:
            raise ThreadError("Only Bot can list Bot Notes.", code="bot_note_access_denied")
        if not 1 <= limit <= 100 or offset < 0:
            raise ThreadError("Invalid Bot Notes page.", code="bot_note_page_invalid")
        async with short_session(self._sessions) as session:
            rows = (
                await session.execute(
                    select(BotNoteRecord, ThreadRecord, ThreadConfigurationRecord)
                    .join(ThreadRecord, ThreadRecord.thread_id == BotNoteRecord.thread_id)
                    .join(ThreadConfigurationRecord, ThreadConfigurationRecord.thread_id == BotNoteRecord.thread_id)
                    .order_by(BotNoteRecord.updated_at.desc(), BotNoteRecord.thread_id)
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()
            return {
                "notes": [
                    {
                        "thread_id": note.thread_id,
                        "title": thread.title,
                        "project_id": configuration.project_id,
                        "archived": thread.archived,
                        "summary": note.content[:512],
                        "updated_at": note.updated_at.isoformat(),
                        "last_writer_thread_id": note.last_writer_thread_id,
                    }
                    for note, thread, configuration in rows[:limit]
                ],
                "next_offset": offset + limit if len(rows) > limit else None,
            }
