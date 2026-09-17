"""Indexed, replaceable inspection data; never a source for execution restore."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, insert, select

from a13n_harness_ui.errors import ThreadError

from .database import DatabaseSessions, short_session, transaction
from .models import ThreadInspectionRecord, ThreadRecord, TranscriptEntryRecord, TranscriptTurnRecord

INSPECTION_VERSION = 2


@dataclass(frozen=True)
class InspectionHeader:
    source_id: str
    message_count: int
    turn_count: int
    metadata_json: str


@dataclass(frozen=True)
class InspectionTurn:
    turn_id: str
    input_position: int
    end_position: int
    value_json: str


@dataclass(frozen=True)
class InspectionData:
    metadata_json: str
    entries: tuple[str, ...]
    turns: tuple[InspectionTurn, ...]


class InspectionRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def header(self, thread_id: str, source_id: str) -> InspectionHeader | None:
        async with short_session(self._sessions) as session:
            record = await session.get(ThreadInspectionRecord, thread_id)
            if record is None or record.source_id != source_id or record.version != INSPECTION_VERSION:
                return None
            return InspectionHeader(record.source_id, record.message_count, record.turn_count, record.metadata_json)

    async def publish(self, thread_id: str, source_id: str, data: InspectionData) -> bool:
        """Atomically replace only the still-selected source, discarding its old derived rows."""
        async with transaction(self._sessions) as session:
            thread = await session.get(ThreadRecord, thread_id)
            if thread is None:
                return False
            selected = thread.continuation_digest or f"initial:{thread.initial_state_digest}"
            if selected != source_id:
                return False
            await session.execute(delete(ThreadInspectionRecord).where(ThreadInspectionRecord.thread_id == thread_id))
            await session.execute(
                insert(ThreadInspectionRecord).values(
                    thread_id=thread_id,
                    source_id=source_id,
                    version=INSPECTION_VERSION,
                    message_count=len(data.entries),
                    turn_count=len(data.turns),
                    metadata_json=data.metadata_json,
                )
            )
            # Bound each driver submission, without exposing a partially published index.
            for start in range(0, len(data.entries), 100):
                await session.execute(
                    insert(TranscriptEntryRecord),
                    [
                        {"thread_id": thread_id, "position": position, "value_json": value}
                        for position, value in enumerate(data.entries[start : start + 100], start)
                    ],
                )
            for start in range(0, len(data.turns), 100):
                await session.execute(
                    insert(TranscriptTurnRecord),
                    [
                        {
                            "thread_id": thread_id,
                            "position": position,
                            "turn_id": turn.turn_id,
                            "input_position": turn.input_position,
                            "end_position": turn.end_position,
                            "value_json": turn.value_json,
                        }
                        for position, turn in enumerate(data.turns[start : start + 100], start)
                    ],
                )
        return True

    async def entries(self, thread_id: str, source_id: str, positions: tuple[int, ...]) -> tuple[str, ...]:
        if not positions:
            return ()
        async with short_session(self._sessions) as session:
            values = tuple(
                await session.scalars(
                    select(TranscriptEntryRecord.value_json)
                    .join(ThreadInspectionRecord)
                    .where(
                        ThreadInspectionRecord.thread_id == thread_id,
                        ThreadInspectionRecord.source_id == source_id,
                        TranscriptEntryRecord.position.in_(positions),
                    )
                    .order_by(TranscriptEntryRecord.position)
                )
            )
        if len(values) != len(set(positions)):
            raise ThreadError("The selected history changed.", code="thread_history_continuation_changed")
        return values

    async def turns(
        self,
        thread_id: str,
        source_id: str,
        *,
        position: int = 0,
        limit: int = 100,
        lower: int | None = None,
        upper: int | None = None,
        turn_id: str | None = None,
    ) -> tuple[str, ...]:
        statement = (
            select(TranscriptTurnRecord.value_json)
            .join(ThreadInspectionRecord)
            .where(ThreadInspectionRecord.thread_id == thread_id, ThreadInspectionRecord.source_id == source_id)
        )
        if lower is not None:
            statement = statement.where(TranscriptTurnRecord.end_position > lower)
        if upper is not None:
            statement = statement.where(TranscriptTurnRecord.input_position < upper)
        if turn_id is not None:
            statement = statement.where(TranscriptTurnRecord.turn_id == turn_id)
        statement = statement.where(TranscriptTurnRecord.position >= position)
        async with short_session(self._sessions) as session:
            values = tuple(await session.scalars(statement.order_by(TranscriptTurnRecord.position).limit(limit)))
            # Empty turns can mean an empty history, not necessarily a cutover.
            selected = await session.scalar(
                select(ThreadInspectionRecord.source_id).where(ThreadInspectionRecord.thread_id == thread_id)
            )
        if selected != source_id:
            raise ThreadError("The selected history changed.", code="thread_history_continuation_changed")
        return values
