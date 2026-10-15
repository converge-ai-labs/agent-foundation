"""Indexed, replaceable inspection data; never a source for execution restore."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from anyio import to_thread
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.sqlite import insert

from a13n_harness_ui.errors import ThreadError

from .database import DatabaseSessions, short_session, transaction
from .models import ThreadInspectionRecord, ThreadRecord, TranscriptEntryRecord, TranscriptTurnRecord

INSPECTION_VERSION = 6


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
    entry_offset: int = 0
    turn_offset: int = 0


def _header_key(record: ThreadInspectionRecord | None) -> tuple[str, int] | None:
    return (record.source_id, record.version) if record is not None else None


def _changed_rows(
    thread_id: str,
    data: InspectionData,
    entries: Sequence[tuple[int, str]],
    turns: Sequence[tuple[int, str, int, int, str]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    old_entries = dict(entries)
    old_turns = {position: InspectionTurn(turn_id, start, end, value) for position, turn_id, start, end, value in turns}
    return (
        [
            {"thread_id": thread_id, "position": position, "value_json": value}
            for position, value in enumerate(data.entries, data.entry_offset)
            if old_entries.get(position) != value
        ],
        [
            {
                "thread_id": thread_id,
                "position": position,
                "turn_id": turn.turn_id,
                "input_position": turn.input_position,
                "end_position": turn.end_position,
                "value_json": turn.value_json,
            }
            for position, turn in enumerate(data.turns, data.turn_offset)
            if old_turns.get(position) != turn
        ],
    )


class InspectionRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def header(self, thread_id: str, source_id: str) -> InspectionHeader | None:
        async with short_session(self._sessions) as session:
            record = await session.get(ThreadInspectionRecord, thread_id)
            if record is None or record.source_id != source_id or record.version != INSPECTION_VERSION:
                return None
            return InspectionHeader(record.source_id, record.message_count, record.turn_count, record.metadata_json)

    async def publish(
        self, thread_id: str, source_id: str, data: InspectionData, *, expected_source: str | None = None
    ) -> bool:
        """Prepare a delta without the writer lock, then publish one atomic source."""
        target = (source_id, INSPECTION_VERSION)
        while True:
            async with short_session(self._sessions) as session:
                # SQLite's legacy transaction mode does not begin on SELECT.
                # Read the header and its rows from one coherent snapshot.
                await session.execute(text("BEGIN"))
                thread = await session.get(ThreadRecord, thread_id)
                if (
                    thread is None
                    or (thread.continuation_digest or f"initial:{thread.initial_state_digest}") != source_id
                ):
                    return False
                previous = _header_key(await session.get(ThreadInspectionRecord, thread_id))
                if previous == target:
                    return True
                if (data.entry_offset or data.turn_offset) and previous != (expected_source, INSPECTION_VERSION):
                    # A missing/stale derived prefix rebuilds on inspection, never
                    # from execution authority or an unverified index base.
                    return False
                entries = (
                    (
                        await session.execute(
                            select(TranscriptEntryRecord.position, TranscriptEntryRecord.value_json).where(
                                TranscriptEntryRecord.thread_id == thread_id,
                                TranscriptEntryRecord.position >= data.entry_offset,
                            )
                        )
                    )
                    .tuples()
                    .all()
                )
                turns = (
                    (
                        await session.execute(
                            select(
                                TranscriptTurnRecord.position,
                                TranscriptTurnRecord.turn_id,
                                TranscriptTurnRecord.input_position,
                                TranscriptTurnRecord.end_position,
                                TranscriptTurnRecord.value_json,
                            ).where(
                                TranscriptTurnRecord.thread_id == thread_id,
                                TranscriptTurnRecord.position >= data.turn_offset,
                            )
                        )
                    )
                    .tuples()
                    .all()
                )
            changed_entries, changed_turns = await to_thread.run_sync(_changed_rows, thread_id, data, entries, turns)
            async with transaction(self._sessions) as session:
                thread = await session.get(ThreadRecord, thread_id)
                if (
                    thread is None
                    or (thread.continuation_digest or f"initial:{thread.initial_state_digest}") != source_id
                ):
                    return False
                current = _header_key(await session.get(ThreadInspectionRecord, thread_id))
                if current == target:
                    return True
                if current != previous:
                    # Another process replaced the index while the delta was
                    # prepared. Release the writer before reading that new base.
                    continue
                header = insert(ThreadInspectionRecord).values(
                    thread_id=thread_id,
                    source_id=source_id,
                    version=INSPECTION_VERSION,
                    message_count=data.entry_offset + len(data.entries),
                    turn_count=data.turn_offset + len(data.turns),
                    metadata_json=data.metadata_json,
                )
                await session.execute(
                    header.on_conflict_do_update(
                        index_elements=[ThreadInspectionRecord.thread_id],
                        set_={
                            "source_id": header.excluded.source_id,
                            "version": header.excluded.version,
                            "message_count": header.excluded.message_count,
                            "turn_count": header.excluded.turn_count,
                            "metadata_json": header.excluded.metadata_json,
                        },
                    )
                )
                await session.execute(
                    delete(TranscriptEntryRecord).where(
                        TranscriptEntryRecord.thread_id == thread_id,
                        TranscriptEntryRecord.position >= data.entry_offset + len(data.entries),
                    )
                )
                await session.execute(
                    delete(TranscriptTurnRecord).where(
                        TranscriptTurnRecord.thread_id == thread_id,
                        TranscriptTurnRecord.position >= data.turn_offset + len(data.turns),
                    )
                )
                entry = insert(TranscriptEntryRecord)
                entry = entry.on_conflict_do_update(
                    index_elements=[TranscriptEntryRecord.thread_id, TranscriptEntryRecord.position],
                    set_={"value_json": entry.excluded.value_json},
                )
                turn = insert(TranscriptTurnRecord)
                turn = turn.on_conflict_do_update(
                    index_elements=[TranscriptTurnRecord.thread_id, TranscriptTurnRecord.position],
                    set_={
                        "turn_id": turn.excluded.turn_id,
                        "input_position": turn.excluded.input_position,
                        "end_position": turn.excluded.end_position,
                        "value_json": turn.excluded.value_json,
                    },
                )
                # Submit only changed rows, not a long unchanged JSON prefix.
                # Batches stay bounded and no partially published index is visible.
                for start in range(0, len(changed_entries), 100):
                    await session.execute(entry, changed_entries[start : start + 100])
                for start in range(0, len(changed_turns), 100):
                    await session.execute(turn, changed_turns[start : start + 100])
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
