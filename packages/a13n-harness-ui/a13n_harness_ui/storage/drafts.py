"""One current CRDT value per WebUI composer, never an edit or execution log."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from a13n_harness_ui.errors import StoreError

from .database import DatabaseSessions, short_session, transaction
from .models import WebuiDraftRecord

DRAFT_RETENTION = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class StoredDraft:
    thread_id: str
    draft_id: str
    document_update: bytes
    options_update: bytes
    unsent_since: datetime | None
    updated_at: datetime


class DraftRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def get(self, thread_id: str) -> StoredDraft | None:
        async with short_session(self._sessions) as session:
            row = await session.get(WebuiDraftRecord, thread_id)
            return (
                None
                if row is None
                else StoredDraft(
                    thread_id=row.thread_id,
                    draft_id=row.draft_id,
                    document_update=row.document_update,
                    options_update=row.options_update,
                    unsent_since=row.unsent_since,
                    updated_at=row.updated_at,
                )
            )

    async def save(self, value: StoredDraft) -> None:
        try:
            async with transaction(self._sessions) as session:
                row = await session.get(WebuiDraftRecord, value.thread_id)
                if row is None:
                    row = WebuiDraftRecord(thread_id=value.thread_id)
                    session.add(row)
                row.draft_id = value.draft_id
                row.document_update = value.document_update
                row.options_update = value.options_update
                row.unsent_since = value.unsent_since
                row.updated_at = value.updated_at
        except SQLAlchemyError as exc:
            raise StoreError(
                "Couldn't save the shared draft. Your unacknowledged edits remain in this browser.",
                code="draft_save_failed",
            ) from exc

    async def unsent(self) -> tuple[tuple[str, str, datetime], ...]:
        async with short_session(self._sessions) as session:
            rows = await session.execute(
                select(WebuiDraftRecord.thread_id, WebuiDraftRecord.draft_id, WebuiDraftRecord.unsent_since)
                .where(WebuiDraftRecord.unsent_since.is_not(None))
                .where(WebuiDraftRecord.updated_at >= datetime.now(UTC) - DRAFT_RETENTION)
            )
            return tuple((thread_id, draft_id, since) for thread_id, draft_id, since in rows)

    async def prune(self, *, active: tuple[str, ...], limit: int = 100) -> tuple[str, ...]:
        async with transaction(self._sessions) as session:
            candidates = (
                select(WebuiDraftRecord.thread_id)
                .where(WebuiDraftRecord.updated_at < datetime.now(UTC) - DRAFT_RETENTION)
                .where(WebuiDraftRecord.thread_id.not_in(active))
                .order_by(WebuiDraftRecord.updated_at, WebuiDraftRecord.thread_id)
                .limit(limit)
            )
            removed = tuple((await session.scalars(candidates)).all())
            if removed:
                await session.execute(delete(WebuiDraftRecord).where(WebuiDraftRecord.thread_id.in_(removed)))
            return removed
