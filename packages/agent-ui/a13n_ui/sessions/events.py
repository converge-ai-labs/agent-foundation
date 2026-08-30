"""Immutable AG-UI event segments, replay, and process-local subscriptions."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from uuid import uuid4

import zstandard
from ag_ui.core import Event
from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    Lock,
    WouldBlock,
    create_memory_object_stream,
    to_thread,
)
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator
from sqlalchemy import select

from a13n_ui.errors import EventStoreError, StoreIntegrityError
from a13n_ui.storage.database import short_session, transaction
from a13n_ui.storage.models import EventSegmentRecord, SessionPresentationRecord, SessionRecord
from a13n_ui.storage.runtime import LocalStore

_DIGEST = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_EVENT_ADAPTER = TypeAdapter(Event)
_SEGMENT_SCHEMA_VERSION = "1"
_MAX_SEGMENT_EVENTS = 10_000
_MAX_REPLAY_EVENTS = 100_000


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PresentationStreamRef(_StrictModel):
    """Presentation-only correlation for one root Harness stream."""

    kind: Literal["root"] = "root"


class StoredAguiEvent(_StrictModel):
    """One Host-correlated typed AG-UI event."""

    record_type: Literal["agui_event"] = "agui_event"
    event_id: str = Field(pattern=r"^event-[0-9a-f]{32}$")
    presentation_sequence: int = Field(gt=0)
    session_id: str = Field(pattern=r"^session-[0-9a-f]{16,64}$")
    thread_id: str = Field(min_length=1, max_length=128)
    turn_id: str | None = Field(default=None, pattern=r"^turn-[0-9a-f]{16,64}$")
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    observed_at: datetime
    stream: PresentationStreamRef = PresentationStreamRef()
    event: Event

    @field_validator("observed_at")
    @classmethod
    def _normalize_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value.astimezone(UTC)


class AguiSegmentHeader(_StrictModel):
    """Self-describing first line of one compressed event segment."""

    record_type: Literal["segment_header"] = "segment_header"
    schema_version: Literal["1"] = "1"
    session_id: str = Field(pattern=r"^session-[0-9a-f]{16,64}$")
    first_sequence: int = Field(gt=0)
    last_sequence: int = Field(gt=0)
    event_count: int = Field(gt=0, le=_MAX_SEGMENT_EVENTS)
    previous_segment_digest: _DIGEST | None = None
    logical_digest: _DIGEST
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _contiguous_range(self) -> AguiSegmentHeader:
        if self.last_sequence - self.first_sequence + 1 != self.event_count:
            raise ValueError("segment range and event_count must agree")
        return self


class PresentationDelivery(_StrictModel):
    """One replay or live delivery with explicit durability origin."""

    origin: Literal["replay", "live"]
    stored: StoredAguiEvent


class EventSubscription:
    """Gap-free replay-to-live cutover selected under the Session append lock."""

    def __init__(self, *, replay_through: int, receive: MemoryObjectReceiveStream[PresentationDelivery]) -> None:
        self.replay_through = replay_through
        self.receive = receive


class SessionEventStore:
    """Publish file-first immutable segments and fan out registered events."""

    def __init__(self, store: LocalStore) -> None:
        self._store = store
        self._locks: dict[str, Lock] = {}
        self._subscribers: dict[str, set[MemoryObjectSendStream[PresentationDelivery]]] = {}

    async def initialize(self) -> None:
        """Verify registered event history before accepting commands."""

        async with short_session(self._store.database.sessions) as database_session:
            rows = tuple(
                (
                    await database_session.execute(
                        select(EventSegmentRecord).order_by(
                            EventSegmentRecord.session_id,
                            EventSegmentRecord.first_sequence,
                        )
                    )
                ).scalars()
            )
        previous_by_session: dict[str, EventSegmentRecord] = {}
        for row in rows:
            try:
                header, _events = await self._read_file(row.relative_path)
                _verify_index(row, header)
                previous = previous_by_session.get(row.session_id)
                if previous is not None and (
                    row.first_sequence != previous.last_sequence + 1
                    or row.previous_segment_digest != previous.logical_digest
                ):
                    raise EventStoreError(
                        "A registered AG-UI segment chain has a gap.",
                        code="event_segment_chain_invalid",
                    )
                previous_by_session[row.session_id] = row
            except (EventStoreError, StoreIntegrityError) as exc:
                await self._store.record_recovery_diagnostic(
                    code=exc.code,
                    detail=f"segment-{row.logical_digest[:16]}",
                )

    async def append(
        self,
        *,
        session_id: str,
        thread_id: str,
        turn_id: str | None,
        run_id: str | None,
        events: Sequence[Event],
    ) -> tuple[StoredAguiEvent, ...]:
        """Publish and register one bounded contiguous segment."""

        values = tuple(events)
        if not values:
            return ()
        if len(values) > _MAX_SEGMENT_EVENTS:
            raise EventStoreError("An AG-UI event batch is too large.", code="event_batch_too_large")
        for event in values:
            try:
                _EVENT_ADAPTER.validate_python(event, strict=True)
            except ValidationError as exc:
                raise EventStoreError("An AG-UI event is invalid.", code="agui_event_invalid") from exc

        lock = self._lock_for(session_id)
        async with lock:
            next_sequence, previous_digest = await self._presentation_head(session_id)
            now = datetime.now(UTC)
            stored = tuple(
                StoredAguiEvent(
                    event_id=f"event-{uuid4().hex}",
                    presentation_sequence=next_sequence + index,
                    session_id=session_id,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    run_id=run_id,
                    observed_at=now,
                    event=_EVENT_ADAPTER.validate_python(event, strict=True),
                )
                for index, event in enumerate(values)
            )
            created_at = datetime.now(UTC)
            digest = _segment_digest(
                session_id=session_id,
                first_sequence=stored[0].presentation_sequence,
                last_sequence=stored[-1].presentation_sequence,
                previous_segment_digest=previous_digest,
                created_at=created_at,
                events=stored,
            )
            header = AguiSegmentHeader(
                session_id=session_id,
                first_sequence=stored[0].presentation_sequence,
                last_sequence=stored[-1].presentation_sequence,
                event_count=len(stored),
                previous_segment_digest=previous_digest,
                logical_digest=digest,
                created_at=created_at,
            )
            relative_path = _relative_segment_path(header)
            await self._publish_file(relative_path, header, stored)
            await self._register_segment(relative_path, header)
            await self._broadcast(session_id, stored)
            return tuple(item.model_copy(deep=True) for item in stored)

    async def replay(
        self,
        session_id: str,
        *,
        after_sequence: int = 0,
        through_sequence: int | None = None,
        limit: int = 10_000,
    ) -> tuple[PresentationDelivery, ...]:
        """Read verified retained history over one finite cursor range."""

        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if through_sequence is not None and through_sequence < after_sequence:
            raise ValueError("through_sequence cannot precede after_sequence")
        if not 1 <= limit <= _MAX_REPLAY_EVENTS:
            raise ValueError(f"replay limit must be between 1 and {_MAX_REPLAY_EVENTS}")
        async with short_session(self._store.database.sessions) as database_session:
            statement = (
                select(EventSegmentRecord)
                .where(
                    EventSegmentRecord.session_id == session_id,
                    EventSegmentRecord.last_sequence > after_sequence,
                )
                .order_by(EventSegmentRecord.first_sequence)
            )
            if through_sequence is not None:
                statement = statement.where(EventSegmentRecord.first_sequence <= through_sequence)
            rows = tuple((await database_session.execute(statement)).scalars())

        selected: list[StoredAguiEvent] = []
        expected = after_sequence + 1
        prior_digest: str | None = None
        for row in rows:
            header, events = await self._read_file(row.relative_path)
            _verify_index(row, header)
            if row.first_sequence <= after_sequence:
                expected = max(expected, after_sequence + 1)
            elif row.first_sequence != expected:
                raise EventStoreError(
                    "Retained AG-UI history has a sequence gap.",
                    code="event_replay_gap",
                    details={"expected_sequence": expected},
                )
            if prior_digest is not None and header.previous_segment_digest != prior_digest:
                raise EventStoreError("Retained AG-UI segment linkage is invalid.", code="event_segment_chain_invalid")
            prior_digest = header.logical_digest
            for item in events:
                if item.presentation_sequence <= after_sequence:
                    continue
                if through_sequence is not None and item.presentation_sequence > through_sequence:
                    break
                if item.presentation_sequence != expected:
                    raise EventStoreError(
                        "Retained AG-UI history has a sequence gap.",
                        code="event_replay_gap",
                        details={"expected_sequence": expected},
                    )
                selected.append(item)
                expected += 1
                if len(selected) >= limit:
                    return tuple(PresentationDelivery(origin="replay", stored=item) for item in selected)
        return tuple(PresentationDelivery(origin="replay", stored=item) for item in selected)

    @asynccontextmanager
    async def subscribe(
        self,
        session_id: str,
        *,
        capacity: int = 256,
    ) -> AsyncGenerator[EventSubscription]:
        """Select a replay watermark and register live delivery without a cutover gap."""

        if not 1 <= capacity <= 100_000:
            raise ValueError("subscription capacity must be between 1 and 100000")
        lock = self._lock_for(session_id)
        send, receive = create_memory_object_stream[PresentationDelivery](capacity)
        async with lock:
            next_sequence, _digest = await self._presentation_head(session_id)
            self._subscribers.setdefault(session_id, set()).add(send)
        try:
            yield EventSubscription(replay_through=next_sequence - 1, receive=receive)
        finally:
            async with lock:
                subscribers = self._subscribers.get(session_id)
                if subscribers is not None:
                    subscribers.discard(send)
                    if not subscribers:
                        self._subscribers.pop(session_id, None)
            await send.aclose()
            await receive.aclose()

    async def close(self) -> None:
        subscribers = tuple(send for values in self._subscribers.values() for send in values)
        self._subscribers.clear()
        for send in subscribers:
            await send.aclose()

    async def _presentation_head(self, session_id: str) -> tuple[int, str | None]:
        async with short_session(self._store.database.sessions) as database_session:
            session_row = await database_session.get(SessionRecord, session_id)
            presentation = await database_session.get(SessionPresentationRecord, session_id)
        if session_row is None or presentation is None:
            raise EventStoreError("The selected Session event stream does not exist.", code="event_session_missing")
        return presentation.next_sequence, presentation.last_segment_digest

    async def _register_segment(self, relative_path: str, header: AguiSegmentHeader) -> None:
        async with transaction(self._store.database.sessions) as database_session:
            presentation = await database_session.get(SessionPresentationRecord, header.session_id)
            if presentation is None:
                raise EventStoreError("The selected Session event stream does not exist.", code="event_session_missing")
            if (
                presentation.next_sequence != header.first_sequence
                or presentation.last_segment_digest != header.previous_segment_digest
            ):
                raise EventStoreError(
                    "The Session event stream changed during publication.", code="event_stream_conflict"
                )
            database_session.add(
                EventSegmentRecord(
                    session_id=header.session_id,
                    first_sequence=header.first_sequence,
                    last_sequence=header.last_sequence,
                    event_count=header.event_count,
                    previous_segment_digest=header.previous_segment_digest,
                    logical_digest=header.logical_digest,
                    relative_path=relative_path,
                    created_at=header.created_at,
                )
            )
            presentation.next_sequence = header.last_sequence + 1
            presentation.last_segment_digest = header.logical_digest

    async def _publish_file(
        self,
        relative_path: str,
        header: AguiSegmentHeader,
        events: tuple[StoredAguiEvent, ...],
    ) -> None:
        payload = _encode_segment(header, events)
        if len(payload) > self._store.settings.max_object_bytes:
            raise EventStoreError("An AG-UI event segment is too large.", code="event_segment_too_large")
        destination = self._store.layout.sessions / PurePosixPath(relative_path)
        stage = self._store.layout.staging / f"event-segment-{uuid4().hex}.jsonl.zst"

        def publish() -> None:
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            compressed = zstandard.ZstdCompressor(level=3, write_checksum=True).compress(payload)
            try:
                with stage.open("xb") as handle:
                    handle.write(compressed)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(stage, 0o600)
                try:
                    os.link(stage, destination)
                except FileExistsError:
                    existing = destination.read_bytes()
                    if existing != compressed:
                        raise EventStoreError(
                            "An event segment path already contains different content.",
                            code="event_segment_conflict",
                        ) from None
                if os.name != "nt":
                    directory_fd = os.open(destination.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            finally:
                stage.unlink(missing_ok=True)

        try:
            await to_thread.run_sync(publish)
        except EventStoreError:
            raise
        except OSError as exc:
            raise EventStoreError(
                "An AG-UI event segment could not be published.", code="event_publish_failed"
            ) from exc

    async def _read_file(self, relative_path: str) -> tuple[AguiSegmentHeader, tuple[StoredAguiEvent, ...]]:
        path = _safe_segment_path(self._store.layout.sessions, relative_path)

        def read() -> bytes:
            try:
                compressed = path.read_bytes()
                return zstandard.ZstdDecompressor().decompress(
                    compressed,
                    max_output_size=self._store.settings.max_object_bytes,
                )
            except (OSError, zstandard.ZstdError) as exc:
                raise EventStoreError(
                    "An AG-UI event segment is missing or corrupt.", code="event_segment_corrupt"
                ) from exc

        payload = await to_thread.run_sync(read)
        try:
            lines = payload.splitlines()
            if len(lines) < 2:
                raise ValueError("event segment has no records")
            header = AguiSegmentHeader.model_validate_json(lines[0])
            events = tuple(StoredAguiEvent.model_validate_json(line) for line in lines[1:])
        except (ValidationError, ValueError) as exc:
            raise EventStoreError("An AG-UI event segment has invalid records.", code="event_segment_invalid") from exc
        if len(events) != header.event_count:
            raise EventStoreError("An AG-UI event segment count is invalid.", code="event_segment_invalid")
        expected_digest = _segment_digest(
            session_id=header.session_id,
            first_sequence=header.first_sequence,
            last_sequence=header.last_sequence,
            previous_segment_digest=header.previous_segment_digest,
            created_at=header.created_at,
            events=events,
        )
        if expected_digest != header.logical_digest:
            raise EventStoreError("An AG-UI event segment digest is invalid.", code="event_segment_digest_mismatch")
        for index, item in enumerate(events):
            if item.session_id != header.session_id or item.presentation_sequence != header.first_sequence + index:
                raise EventStoreError("An AG-UI event segment correlation is invalid.", code="event_segment_invalid")
        return header, events

    async def _broadcast(self, session_id: str, events: tuple[StoredAguiEvent, ...]) -> None:
        subscribers = self._subscribers.get(session_id)
        if not subscribers:
            return
        stale: set[MemoryObjectSendStream[PresentationDelivery]] = set()
        for send in tuple(subscribers):
            try:
                for item in events:
                    send.send_nowait(PresentationDelivery(origin="live", stored=item))
            except (WouldBlock, BrokenResourceError, ClosedResourceError):
                stale.add(send)
        for send in stale:
            subscribers.discard(send)
            await send.aclose()
        if not subscribers:
            self._subscribers.pop(session_id, None)

    def _lock_for(self, session_id: str) -> Lock:
        return self._locks.setdefault(session_id, Lock())


def _segment_digest(
    *,
    session_id: str,
    first_sequence: int,
    last_sequence: int,
    previous_segment_digest: str | None,
    created_at: datetime,
    events: tuple[StoredAguiEvent, ...],
) -> str:
    value = {
        "header": {
            "record_type": "segment_header",
            "schema_version": _SEGMENT_SCHEMA_VERSION,
            "session_id": session_id,
            "first_sequence": first_sequence,
            "last_sequence": last_sequence,
            "event_count": len(events),
            "previous_segment_digest": previous_segment_digest,
            "created_at": created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        },
        "events": [item.model_dump(mode="json", by_alias=True) for item in events],
    }
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _encode_segment(header: AguiSegmentHeader, events: tuple[StoredAguiEvent, ...]) -> bytes:
    lines = [
        header.model_dump(mode="json", by_alias=True),
        *(item.model_dump(mode="json", by_alias=True) for item in events),
    ]
    return b"".join(_canonical_json(line) + b"\n" for line in lines)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _relative_segment_path(header: AguiSegmentHeader) -> str:
    created = header.created_at.astimezone(UTC)
    name = f"{header.first_sequence:020d}-{header.last_sequence:020d}-{header.logical_digest}.jsonl.zst"
    return PurePosixPath(
        f"{created:%Y}",
        f"{created:%m}",
        f"{created:%d}",
        header.session_id,
        "events",
        name,
    ).as_posix()


def _safe_segment_path(root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise StoreIntegrityError("An event segment path is invalid.", code="event_segment_path_invalid")
    candidate = (root / relative).resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if candidate == resolved_root or resolved_root not in candidate.parents:
        raise StoreIntegrityError("An event segment path escapes Session storage.", code="event_segment_path_invalid")
    return candidate


def _verify_index(row: EventSegmentRecord, header: AguiSegmentHeader) -> None:
    if (
        row.session_id != header.session_id
        or row.first_sequence != header.first_sequence
        or row.last_sequence != header.last_sequence
        or row.event_count != header.event_count
        or row.previous_segment_digest != header.previous_segment_digest
        or row.logical_digest != header.logical_digest
    ):
        raise EventStoreError("An AG-UI segment index does not match its file.", code="event_segment_index_mismatch")


__all__ = [
    "AguiSegmentHeader",
    "EventSubscription",
    "PresentationDelivery",
    "PresentationStreamRef",
    "SessionEventStore",
    "StoredAguiEvent",
]
