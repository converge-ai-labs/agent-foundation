"""Immutable voice turns and a separate compare-and-select transcript head."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from a13n_harness_ui.errors import StoreConflictError, StoreIntegrityError

from .contracts import StoredContract
from .database import DatabaseSessions, short_session, transaction
from .models import LiveTranscriptHeadRecord
from .objects import ImmutableObjectStore, ObjectKind, ObjectRef


class VoiceTurn(StoredContract):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=256 * 1024)
    occurred_at: datetime


class StoredVoiceTranscript(StoredContract):
    thread_id: str
    previous: ObjectRef | None = None
    turn: VoiceTurn


class VoiceTranscriptRepository:
    def __init__(self, sessions: DatabaseSessions, objects: ImmutableObjectStore) -> None:
        self._sessions = sessions
        self._objects = objects

    async def head(self, thread_id: str) -> ObjectRef | None:
        async with short_session(self._sessions) as session:
            row = await session.get(LiveTranscriptHeadRecord, thread_id)
            return None if row is None else self._reference(row)

    async def append(self, thread_id: str, turn: VoiceTurn) -> None:
        previous = await self.head(thread_id)
        value = StoredVoiceTranscript(thread_id=thread_id, previous=previous, turn=turn)
        published = await self._objects.publish_model(object_kind=ObjectKind.live_transcript, value=value)
        async with transaction(self._sessions) as session:
            row = await session.get(LiveTranscriptHeadRecord, thread_id)
            current = None if row is None else self._reference(row)
            if current != previous:
                raise StoreConflictError(
                    "Voice transcript changed during publication.", code="live_transcript_conflict"
                )
            if row is None:
                row = LiveTranscriptHeadRecord(thread_id=thread_id)
                session.add(row)
            row.object_schema_version = published.ref.object_schema_version
            row.logical_digest = published.ref.logical_digest

    async def recent(self, thread_id: str, *, limit: int = 64) -> tuple[VoiceTurn, ...]:
        reference = await self.head(thread_id)
        turns: list[VoiceTurn] = []
        for _ in range(limit):
            if reference is None:
                break
            value = await self._objects.read_model(reference, StoredVoiceTranscript)
            if value.thread_id != thread_id or reference.object_kind is not ObjectKind.live_transcript:
                raise StoreIntegrityError(
                    "Voice transcript belongs to a different Thread.", code="live_transcript_invalid"
                )
            turns.append(value.turn)
            reference = value.previous
        return tuple(reversed(turns))

    @staticmethod
    def _reference(row: LiveTranscriptHeadRecord) -> ObjectRef:
        return ObjectRef(
            object_kind=ObjectKind.live_transcript,
            object_schema_version=row.object_schema_version,
            logical_digest=row.logical_digest,
        )
