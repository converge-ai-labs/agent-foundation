"""The session stream: every thread of a session, with its current state and the live output of its active runs.

Workers append each attempt's output deltas to the thread's Redis stream, `a13n:thread:{thread}`, and a boundary
marker after each checkpoint commit, and trim what a boundary's display covers (`ThreadStream`). Nothing there is
durable or authoritative: display objects hold the committed view, and PostgreSQL decides who may read and which
run and attempt are current.

Each Control process has one `SessionHub`. For the sessions that have readers it shares one blocking XREAD over
their threads and, every `control.stream_refresh_seconds` or sooner when a reader meets a run it does not follow,
reads their state and each reader's authority once. A connection sends four frames:

- `thread`: a thread's current state, whenever it changes;
- `items`: a run's committed items from an ordinal on, whenever the connection follows the run from a fresh base:
  on connect, for a new run or attempt, once a checkpoint covers lost output, and at seal;
- `delta`: one live event of a followed run;
- `gap`: output of a followed run was lost; its deltas are held until a checkpoint covers the loss.

A client never sends a position: every connection starts from a fresh snapshot.
"""

import asyncio
import json
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Literal

from a13n_logging import exception_details, get_logger
from a13n_stream_protocol.display import MAX_FIELD_CHARS, DisplayContinuation, Item, ItemRef
from pydantic import BaseModel
from pydantic.json_schema import JsonSchemaMode, models_json_schema
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, defer

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.redis import StreamEntry, append, last_id, read, read_range, trim
from a13n_service.runs import checkpoints
from a13n_service.runs.checkpoints import PageRef, Pointer, TailPointer
from a13n_service.runs.contents import ContentObject, decode
from a13n_service.runs.display import Observed, Page, StreamPosition, Tail
from a13n_service.runs.runs import NEWEST_ITEMS, newest, run_views
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EntryView, RunView, ThreadView
from a13n_service.runs.sessions import find_session
from a13n_service.runs.tables import AttemptRow, InboxEntryRow, RunItemPageRow, RunRow, ThreadRow
from a13n_service.settings import Settings
from a13n_service.tenancy.access import Access, Authenticated, reauthenticate, workspace_scope

logger = get_logger(__name__)

# Deltas a worker buffers while Redis is slow; beyond it output is dropped and readers see a gap.
WRITE_BUFFER = 1024
# A larger delta (a big tool result) is dropped the same way: readers see a gap and read the committed items.
MAX_DELTA_BYTES = 262144
# What one connection may queue before the Service closes it as too far behind; its reconnect starts afresh.
READ_BUFFER = 1024
READ_BYTES = 4 * 1024 * 1024
READ_BATCH = 256
BLOCK_MS = 1000
# A short pause after a read that returned entries, so one XREAD collects the entries of many threads.
READ_PAUSE = 0.05
KEEPALIVE_SECONDS = 15
# Bases one process builds at once: a reconnecting crowd waits here instead of exhausting the database pool.
BUILDS = 4
# Display objects kept for the followers of the same runs; a key never names other bytes.
CACHED_OBJECTS = 64

STREAM_PREFIX = "a13n:thread:"


def stream_key(thread_id: str) -> str:
    return STREAM_PREFIX + thread_id


class ThreadState(BaseModel):
    """A thread's current state: the thread, the runs its pointers name and its open inbox entries."""

    thread: ThreadView
    current_run: RunView | None
    last_run: RunView | None
    # Pending and assigned entries, in inbox order.
    entries: list[EntryView]


class RunSnapshot(BaseModel):
    """A run's committed items from ordinal `first` on. The receiver deletes the items it holds from `first` on and
    inserts these; earlier items are final. `complete` marks the run's final display."""

    thread_id: str
    run_id: str
    first: int
    items: list[Item]
    continuation: DisplayContinuation | None
    complete: bool


class Delta(BaseModel):
    """One raw AG-UI event, sequenced by the producer and correlated to its display item."""

    thread_id: str
    run_id: str
    attempt: int
    sequence: int
    event: dict[str, Any]
    item: ItemRef | None


class Gap(BaseModel):
    """Output of a followed run was lost: its deltas are held until an `items` frame repairs it."""

    thread_id: str
    run_id: str


type FrameName = Literal["thread", "items", "delta", "gap"]
# The SSE `event` name of each frame and the model of its `data`.
FRAMES: dict[FrameName, type[BaseModel]] = {"thread": ThreadState, "items": RunSnapshot, "delta": Delta, "gap": Gap}


def frames_schema() -> dict[str, Any]:
    """The exported wire contract: each frame's `data` JSON schema by its SSE `event` name."""
    mode: JsonSchemaMode = "serialization"
    references, definitions = models_json_schema(
        [(model, mode) for model in FRAMES.values()], ref_template="#/$defs/{model}"
    )
    return {
        "title": "Session stream frames",
        "frames": {name: references[(model, mode)] for name, model in FRAMES.items()},
        **definitions,
    }


# Writing


@dataclass(frozen=True)
class WrittenPosition:
    """The last delta this attempt confirmed in Redis, replaced as one event-loop-local value."""

    attempt: int
    sequence: int
    redis_id: str


class ThreadStream:
    """One attempt's buffered appends, confirmed position, and boundary-driven trims."""

    def __init__(self, redis: Redis, settings: Settings, *, thread_id: str, run_id: str, attempt: int):
        self.redis, self.settings = redis, settings
        self.key, self.run_id, self.attempt = stream_key(thread_id), run_id, attempt
        self.buffer: asyncio.Queue[dict[str, str]] = asyncio.Queue(WRITE_BUFFER)
        self.writer: asyncio.Task[None] | None = None
        self.last_written: WrittenPosition | None = None

    async def __aenter__(self) -> "ThreadStream":
        self.writer = asyncio.create_task(self._write(), name=f"stream-{self.run_id}")
        return self

    async def __aexit__(self, *_: object) -> None:
        assert self.writer is not None
        try:
            async with asyncio.timeout(self.settings.redis.timeout):
                await self.buffer.join()
        except TimeoutError:
            pass
        finally:
            self.writer.cancel()

    def delta(self, observed: Observed) -> None:
        event = json.dumps(observed.event, separators=(",", ":"))
        if len(event.encode()) > MAX_DELTA_BYTES:
            return
        self._put(
            {
                "sequence": str(observed.sequence),
                "event": event,
                "item": observed.item.model_dump_json() if observed.item is not None else "null",
            }
        )

    def boundary(self, sequence: int, *, resume_after: str | None = None) -> None:
        # Commit may finish after newer deltas were appended. Only the frozen
        # checkpoint's confirmed lower bound is safe for checkpoint-driven trim.
        fields = {"sequence": str(sequence), "boundary": "1"}
        if resume_after is not None:
            fields["resume_after"] = resume_after
        self._put(fields)

    def _put(self, fields: dict[str, str]) -> None:
        try:
            self.buffer.put_nowait({"run_id": self.run_id, "attempt": str(self.attempt), **fields})
        except asyncio.QueueFull:
            pass  # Readers detect the missing sequence and wait for a checkpoint that covers it.

    async def _write(self) -> None:
        worker, timeout = self.settings.worker, self.settings.redis.timeout
        while True:
            entries = [await self.buffer.get()]
            while not self.buffer.empty() and len(entries) < READ_BATCH:
                entries.append(self.buffer.get_nowait())
            ids = await append(
                self.redis, self.key, entries, max_length=worker.stream_length, ttl=worker.stream_ttl, timeout=timeout
            )
            for entry_id, fields in zip(ids, entries, strict=bool(ids)):
                if "boundary" not in fields:
                    self.last_written = WrittenPosition(self.attempt, int(fields["sequence"]), entry_id)
            # A late marker's physical ID can follow uncovered deltas. Never
            # trim from it, even when appending the marker succeeded.
            anchors = [
                fields["resume_after"]
                for _, fields in zip(ids, entries, strict=bool(ids))
                if "boundary" in fields and "resume_after" in fields
            ]
            if anchors:
                retained = _retained_from(anchors[-1], worker.stream_trim_seconds)
                await trim(self.redis, self.key, min_id=retained, timeout=timeout)
            for _ in entries:
                self.buffer.task_done()


def _retained_from(boundary: str, window: float) -> str:
    """The oldest entry a boundary keeps: itself, or the first one appended `window` seconds before it.

    Redis assigns entry IDs from its own clock, so the boundary's ID dates the window without comparing clocks.
    """
    if window == 0:
        return boundary
    milliseconds, _, _ = boundary.partition("-")
    return f"{int(milliseconds) - round(window * 1000)}-0"


# Reading


@dataclass(frozen=True, slots=True)
class Progress:
    """A current run as a refresh found it: its latest attempt and the position and first ordinal of its committed
    display."""

    run_id: str
    attempt: int
    position: StreamPosition
    first: int


@dataclass(frozen=True, slots=True)
class Update:
    """What one refresh found for one thread: its state frame when the state changed, its current run's progress,
    and for a thread new to the hub the Redis position after which its live entries arrive."""

    thread_id: str
    frame: str | None
    progress: Progress | None
    cursor: str | None


class Close:
    """The connection ends: its reader lost access or fell too far behind."""


type Signal = StreamEntry | Update | Close


@dataclass(eq=False)
class Reader:
    credential: Authenticated
    workspace_id: str
    session_id: str
    signals: asyncio.Queue[Signal] = field(default_factory=asyncio.Queue)
    size: int = 0
    closed: bool = False

    def send(self, signal: Signal) -> None:
        if self.closed:
            return
        size = _size(signal)
        if isinstance(signal, Close) or self.signals.qsize() >= READ_BUFFER or self.size + size > READ_BYTES:
            self.closed = True
            self.signals.put_nowait(Close())
            return
        self.size += size
        self.signals.put_nowait(signal)

    async def receive(self) -> Signal:
        signal = await self.signals.get()
        self.size -= _size(signal)
        return signal


def _size(signal: Signal) -> int:
    """Queued bytes a signal holds for this reader alone; state frames are shared by every reader."""
    return sum(len(value) for value in signal.fields.values()) if isinstance(signal, StreamEntry) else 0


@dataclass(frozen=True, slots=True)
class _Version:
    thread_id: str
    session_id: str
    # The thread's version and those of the runs its pointers name: a `thread` frame follows any change.
    key: tuple[int, int | None, int | None]
    # Its current run's, None without one.
    progress: Progress | None

    def watch(self, frame: str) -> "_Watch":
        return _Watch(self.key, self.progress.run_id if self.progress is not None else None, frame)


@dataclass(frozen=True, slots=True)
class _Watch:
    """A watched thread: its version key, current run and latest `thread` frame."""

    key: tuple[int, int | None, int | None]
    current_run_id: str | None
    frame: str


@dataclass(eq=False)
class _Watched:
    workspace_id: str
    readers: set[Reader] = field(default_factory=set)
    threads: dict[str, _Watch] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Base:
    """A fresh base for following a run: its committed items from `snapshot.first` on, and where live output
    resumes."""

    snapshot: RunSnapshot
    # The run's latest attempt and the last sequence of it the items cover.
    attempt: int
    sequence: int
    # The tail's first ordinal: every earlier item is final.
    first: int
    resume_after: str | None
    # The first in-progress item shown as a preview of a large field, which live output does not extend.
    preview: int | None


async def _repeatable(session: AsyncSession) -> None:
    """Read the rest of this session from one snapshot, so a frame's thread, runs and entries agree."""
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))


async def _versions(session: AsyncSession, sessions: Sequence[tuple[str, str]]) -> list[_Version]:
    """Every thread of the watched sessions, oldest first, with its version key and its current run's progress."""
    current, last = aliased(RunRow), aliased(RunRow)
    latest = (
        select(func.max(AttemptRow.number))
        .where(AttemptRow.run_id == ThreadRow.current_run_id)
        .correlate(ThreadRow)
        .scalar_subquery()
    )
    position = current.tail["position"]
    rows = await session.execute(
        select(
            ThreadRow.id,
            ThreadRow.session_id,
            ThreadRow.version,
            ThreadRow.current_run_id,
            current.version,
            last.version,
            latest,
            position["attempt"].as_integer(),
            position["sequence"].as_integer(),
            current.tail["first"].as_integer(),
        )
        .outerjoin(current, current.id == ThreadRow.current_run_id)
        .outerjoin(last, last.id == ThreadRow.last_run_id)
        .where(tuple_(ThreadRow.workspace_id, ThreadRow.session_id).in_(sessions))
        .order_by(ThreadRow.created_at, ThreadRow.id)
    )
    return [
        _Version(
            thread_id,
            session_id,
            (version, current_version, last_version),
            Progress(run_id, attempt or 0, StreamPosition(attempt=committed or 0, sequence=sequence or 0), first or 1)
            if run_id is not None
            else None,
        )
        for (
            thread_id,
            session_id,
            version,
            run_id,
            current_version,
            last_version,
            attempt,
            committed,
            sequence,
            first,
        ) in rows
    ]


async def _states(session: AsyncSession, thread_ids: Sequence[str]) -> dict[str, ThreadState]:
    threads = (
        await session.scalars(
            select(ThreadRow).options(defer(ThreadRow.message_history)).where(ThreadRow.id.in_(thread_ids))
        )
    ).all()
    current = [thread.current_run_id for thread in threads if thread.current_run_id is not None]
    named = {*current, *(thread.last_run_id for thread in threads if thread.last_run_id is not None)}
    rows = (await session.scalars(select(RunRow).where(RunRow.id.in_(named)))).all()
    runs = {view.id: view for view in await run_views(session, rows)}
    # Assigned entries belong to the thread's current run, pending ones to the thread: each finds its partial index.
    entries = await session.scalars(
        select(InboxEntryRow)
        .where(
            or_(
                and_(InboxEntryRow.status == "pending", InboxEntryRow.thread_id.in_(thread_ids)),
                and_(InboxEntryRow.status == "assigned", InboxEntryRow.assigned_run_id.in_(current)),
            )
        )
        .order_by(InboxEntryRow.position)
    )
    open_entries: dict[str, list[EntryView]] = {}
    for entry in entries:
        open_entries.setdefault(entry.thread_id, []).append(EntryView.model_validate(entry))
    return {
        thread.id: ThreadState(
            thread=ThreadView.model_validate(thread),
            current_run=runs.get(thread.current_run_id or ""),
            last_run=runs.get(thread.last_run_id or ""),
            entries=open_entries.get(thread.id, []),
        )
        for thread in threads
    }


async def _may_read(session: AsyncSession, access: Access, credential: Authenticated, workspace_id: str) -> bool:
    """Whether the credential still reads the workspace; an unavailable database decides nothing."""
    try:
        principal = await reauthenticate(session, access, credential)
        await workspace_scope(session, principal, workspace_id, "read")
    except ServiceError as error:
        if error.code == "unavailable":
            raise
        return False
    return True


class SessionHub:
    """The session streams of this process: one shared blocking XREAD and one periodic refresh."""

    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.sessions: dict[str, _Watched] = {}
        # Redis key → its thread's session, and the last entry the shared read returned.
        self.owners: dict[str, str] = {}
        self.cursors: dict[str, str] = {}
        # Serializes refreshes and the first reader of a session, so frames reach each reader in commit order.
        self.lock = asyncio.Lock()
        self.joined = asyncio.Event()
        self.nudged = asyncio.Event()
        self.builds = asyncio.Semaphore(BUILDS)
        self.objects: OrderedDict[str, asyncio.Task[bytes]] = OrderedDict()

    async def run(self) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(self._read())
            group.create_task(self._refresh())

    async def join(self, reader: Reader) -> tuple[dict[str, _Watch], dict[str, str]]:
        """Register the reader: its session's threads and the Redis position after which each thread's live entries
        reach it."""
        async with self.lock:
            watched = self.sessions.get(reader.session_id)
            if watched is None:
                watched = await self._watch(reader.session_id, reader.workspace_id)
            # No await from here on: live entries start exactly after these cursors.
            watched.readers.add(reader)
            return dict(watched.threads), {
                thread_id: self.cursors[stream_key(thread_id)] for thread_id in watched.threads
            }

    def leave(self, reader: Reader) -> None:
        watched = self.sessions.get(reader.session_id)
        if watched is None:
            return
        watched.readers.discard(reader)
        if not watched.readers:
            del self.sessions[reader.session_id]
            for thread_id in watched.threads:
                self.owners.pop(stream_key(thread_id), None)
                self.cursors.pop(stream_key(thread_id), None)

    def nudge(self) -> None:
        """Refresh now: a reader met a run or attempt it does not follow yet."""
        self.nudged.set()

    async def _watch(self, session_id: str, workspace_id: str) -> _Watched:
        async with short_session(self.runtime.storage) as session:
            await _repeatable(session)
            versions = await _versions(session, [(workspace_id, session_id)])
            states = await _states(session, [version.thread_id for version in versions])
        cursors = await asyncio.gather(*(self._cursor(version.thread_id) for version in versions))
        # No await from here on: a connection that closed during these reads leaves nothing watched.
        watched = self.sessions[session_id] = _Watched(workspace_id)
        for version, cursor in zip(versions, cursors, strict=True):
            watched.threads[version.thread_id] = version.watch(_frame("thread", states[version.thread_id]))
            self._add(version.thread_id, session_id, cursor)
        return watched

    async def _cursor(self, thread_id: str) -> str:
        """The thread's newest Redis entry: its live entries start after it."""
        try:
            return await last_id(self.runtime.redis, stream_key(thread_id))
        except ServiceError:
            # Redis is down: read what it stores from now on. Earlier output reaches readers through their bases.
            return f"{time.time_ns() // 1_000_000}-0"

    def _add(self, thread_id: str, session_id: str, cursor: str) -> None:
        """Add a thread's Redis stream to the shared read, after `cursor`."""
        key = stream_key(thread_id)
        self.cursors[key], self.owners[key] = cursor, session_id
        self.joined.set()

    async def _read(self) -> None:
        while True:
            if not self.cursors:
                self.joined.clear()
                await self.joined.wait()
                continue
            try:
                entries = await read(self.runtime.redis, dict(self.cursors), count=READ_BATCH, block_ms=BLOCK_MS)
            except ServiceError:
                # The cursors stay put, so the next read loses nothing.
                await asyncio.sleep(BLOCK_MS / 1000)
                continue
            for entry in entries:
                if entry.key not in self.cursors:
                    continue
                self.cursors[entry.key] = entry.id
                if (watched := self.sessions.get(self.owners[entry.key])) is not None:
                    for reader in watched.readers:
                        reader.send(entry)
            if entries:
                await asyncio.sleep(READ_PAUSE)

    async def _refresh(self) -> None:
        while True:
            with suppress(TimeoutError):
                await asyncio.wait_for(self.nudged.wait(), timeout=self.runtime.settings.control.stream_refresh_seconds)
            self.nudged.clear()
            if not self.sessions:
                continue
            try:
                await self._refresh_once()
            except Exception as error:
                logger.warning(
                    "Session stream refresh failed",
                    extra={"error_type": type(error).__name__, "exception_details": exception_details(error)},
                )

    async def _refresh_once(self) -> None:
        """One snapshot of every watched thread's state, and one authority check per credential and workspace."""
        async with self.lock:
            sessions = dict(self.sessions)
            readers = [reader for watched in sessions.values() for reader in watched.readers]
            async with short_session(self.runtime.storage) as session:
                await _repeatable(session)
                versions = await _versions(session, [(watched.workspace_id, key) for key, watched in sessions.items()])
                changed = [
                    version.thread_id
                    for version in versions
                    if (watch := sessions[version.session_id].threads.get(version.thread_id)) is None
                    or watch.key != version.key
                ]
                states = await _states(session, changed) if changed else {}
                readable: dict[tuple[str, str], bool] = {}
                for reader in readers:
                    key = (reader.credential.credential_id, reader.workspace_id)
                    if key not in readable:
                        readable[key] = await _may_read(
                            session, self.runtime.access, reader.credential, reader.workspace_id
                        )
            new = {
                version.thread_id: await self._cursor(version.thread_id)
                for version in versions
                if version.thread_id not in sessions[version.session_id].threads
            }
            # No await from here on: every reader receives these updates before any later entry of a new thread.
            updates: dict[str, list[Update]] = {}
            for version in versions:
                watched = sessions[version.session_id]
                if self.sessions.get(version.session_id) is not watched:
                    continue  # Its last reader left while the refresh read.
                frame = None
                if version.thread_id in states:
                    frame = _frame("thread", states[version.thread_id])
                    watched.threads[version.thread_id] = version.watch(frame)
                if (cursor := new.get(version.thread_id)) is not None:
                    self._add(version.thread_id, version.session_id, cursor)
                if frame is not None or version.progress is not None:
                    updates.setdefault(version.session_id, []).append(
                        Update(version.thread_id, frame, version.progress, cursor)
                    )
            for reader in readers:
                if not readable[(reader.credential.credential_id, reader.workspace_id)]:
                    reader.send(Close())
                    continue
                for update in updates.get(reader.session_id, ()):
                    reader.send(update)

    async def base(self, run_id: str, first: int | None) -> _Base:
        """The run's committed items from ordinal `first` on, or its newest ones as a default items read returns them
        when None, with in-progress items complete enough for live output to extend. A run sealed or on a later attempt shows unfinished items
        interrupted, as the worker of its next attempt does."""
        async with self.builds:
            try:
                return await self._base(run_id, first)
            except ServiceError as error:
                if error.code != "unavailable":
                    raise
                # A checkpoint committed after the pointer was read deletes the tail it replaced; the pointer read
                # again names the tail that replaced it, and pages are never deleted.
                return await self._base(run_id, first)

    async def _base(self, run_id: str, first: int | None) -> _Base:
        async with short_session(self.runtime.storage) as session:
            run = await session.get_one(RunRow, run_id)
            attempt = await session.scalar(select(func.max(AttemptRow.number)).where(AttemptRow.run_id == run.id)) or 0
            pointer = TailPointer.model_validate(run.tail) if run.tail is not None else None
            tail_first = pointer.first if pointer is not None else 1
            start = newest(pointer, NEWEST_ITEMS) if first is None else min(first, tail_first)
            rows = await session.scalars(
                select(RunItemPageRow)
                .where(
                    RunItemPageRow.run_id == run.id,
                    RunItemPageRow.first_ordinal < tail_first,
                    RunItemPageRow.last_ordinal >= start,
                )
                .order_by(RunItemPageRow.first_ordinal)
            )
            pages = [
                PageRef(key=row.key, digest=row.digest, size=row.size, first=row.first_ordinal, last=row.last_ordinal)
                for row in rows
            ]
            thread_id, sealed = run.thread_id, run.sealed_at is not None
        tail, loaded = await asyncio.gather(self._tail(pointer), asyncio.gather(*map(self._page, pages)))
        current = tail.position.attempt == attempt
        items = [
            item.model_copy(update={"state": "interrupted"})
            if item.state == "in_progress" and (sealed or not current)
            else item
            for item in (*(item for page in loaded for item in page.items), *tail.items)
            if item.ordinal >= start
        ]
        snapshot = RunSnapshot(
            thread_id=thread_id,
            run_id=run_id,
            first=start,
            items=items,
            continuation=tail.continuation if current and not sealed else None,
            complete=sealed,
        )
        return _Base(
            snapshot,
            attempt,
            tail.position.sequence if current else 0,
            tail_first,
            tail.resume_after if current else None,
            min((item.ordinal for item in items if item.state == "in_progress" and item.content_refs), default=None),
        )

    async def _object(self, ref: Pointer | PageRef | ContentObject) -> bytes:
        """The verified bytes an object reference names, read once for every reader that needs them now."""
        task = self.objects.get(ref.key)
        if task is None or (task.done() and task.exception() is not None):
            task = asyncio.ensure_future(checkpoints.load(self.runtime.objects, ref))
            self.objects[ref.key] = task
            while len(self.objects) > CACHED_OBJECTS:
                self.objects.popitem(last=False)
        self.objects.move_to_end(ref.key)
        return await asyncio.shield(task)

    async def _page(self, ref: PageRef) -> Page:
        return Page.model_validate_json(await self._object(ref))

    async def _tail(self, pointer: TailPointer | None) -> Tail:
        """The tail, with each in-progress field within the live field limit in full rather than as a preview, so
        live output extends it. A larger field keeps its preview and reference until the item completes."""
        if pointer is None:
            return Tail()
        tail = Tail.model_validate_json(await self._object(pointer))
        refs = {ref.id: ref for ref in tail.refs}
        for item in tail.items:
            if item.state != "in_progress":
                continue
            for name, public in list(item.content_refs.items()):
                if (saved := refs[public.id]).size_bytes <= MAX_FIELD_CHARS:
                    item.content[name] = decode(await self._object(saved), saved)
                    del item.content_refs[name]
        return tail


async def open_stream(runtime: Runtime, credential: Authenticated, workspace_id: str, session_id: str) -> Reader:
    """Authorize before the response starts, so refusals are ordinary HTTP errors."""
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, credential.principal, workspace_id, "read")
        found = await find_session(session, scope.workspace_id, session_id)
    return Reader(credential, scope.workspace_id, found.id)


def _frame(event: FrameName, data: BaseModel) -> str:
    return f"event: {event}\ndata: {data.model_dump_json()}\n\n"


def _order(entry_id: str) -> tuple[int, int]:
    milliseconds, _, sequence = entry_id.partition("-")
    return int(milliseconds), int(sequence or 0)


@dataclass
class _Follow:
    """A run this connection streams: its attempt, the last sequence delivered and the first ordinal a new base
    starts at."""

    thread_id: str
    attempt: int
    sequence: int
    first: int
    # The first item the base sent as a preview: it is sent again once final.
    preview: int | None = None
    # While set, the run's deltas are held until a checkpoint covers this sequence.
    held: int | None = None
    # A committed sequence the last refresh found ahead of the output this connection received.
    expected: int | None = None


class _Connection:
    """One session stream: the Redis position it consumed per thread and the runs it follows."""

    def __init__(self, hub: SessionHub, reader: Reader):
        self.hub, self.reader = hub, reader
        self.positions: dict[str, str] = {}
        self.follows: dict[str, _Follow] = {}
        # Runs whose final display this connection sent, and the run attempts it already asked a refresh for.
        self.ended: set[str] = set()
        self.asked: set[tuple[str, int]] = set()

    async def open(self) -> list[str]:
        threads, self.positions = await self.hub.join(self.reader)
        frames: list[str] = []
        for thread_id, watch in threads.items():
            frames.append(watch.frame)
            if watch.current_run_id is not None:
                frames += await self.follow(thread_id, watch.current_run_id, None)
        return frames

    async def handle(self, signal: StreamEntry | Update) -> list[str]:
        if isinstance(signal, StreamEntry):
            return self.entry(signal)
        return await self.update(signal)

    def entry(self, entry: StreamEntry) -> list[str]:
        self.positions[entry.key.removeprefix(STREAM_PREFIX)] = entry.id
        run_id = entry.fields["run_id"]
        if (follow := self.follows.get(run_id)) is None:
            if run_id not in self.ended:
                self.ask(run_id, int(entry.fields["attempt"]))
            return []
        return self.deliver(run_id, follow, entry)

    def deliver(self, run_id: str, follow: _Follow, entry: StreamEntry) -> list[str]:
        fields = entry.fields
        attempt, sequence = int(fields["attempt"]), int(fields["sequence"])
        if follow.held is not None or attempt < follow.attempt:
            return []
        if attempt > follow.attempt:
            # Following the new attempt replays this entry from Redis.
            self.ask(run_id, attempt)
            return []
        if "boundary" in fields:
            # A boundary past the delivered output covers deltas this connection never received.
            return self.hold(run_id, follow, sequence) if sequence > follow.sequence else []
        if sequence <= follow.sequence:
            return []
        if sequence > follow.sequence + 1:
            return self.hold(run_id, follow, sequence - 1)
        if "event" not in fields:
            # An incompatible retained frame cannot be interpreted as a raw event.
            return self.hold(run_id, follow, sequence)
        follow.sequence = sequence
        delta = Delta(
            thread_id=follow.thread_id,
            run_id=run_id,
            attempt=attempt,
            sequence=sequence,
            event=json.loads(fields["event"]),
            item=json.loads(fields.get("item", "null")),
        )
        return [_frame("delta", delta)]

    def hold(self, run_id: str, follow: _Follow, through: int | None) -> list[str]:
        """Hold the run's deltas until a checkpoint covers `through`, or any later one when the extent is unknown."""
        if follow.held is not None:
            return []
        follow.held = through if through is not None else follow.sequence + 1
        return [_frame("gap", Gap(thread_id=follow.thread_id, run_id=run_id))]

    def ask(self, run_id: str, attempt: int) -> None:
        if (run_id, attempt) not in self.asked:
            self.asked.add((run_id, attempt))
            self.hub.nudge()

    async def update(self, update: Update) -> list[str]:
        frames = [update.frame] if update.frame is not None else []
        if update.cursor is not None:
            self.positions[update.thread_id] = update.cursor
        progress = update.progress
        for run_id, follow in list(self.follows.items()):
            if follow.thread_id == update.thread_id and (progress is None or run_id != progress.run_id):
                # Sealed: its final display.
                del self.follows[run_id]
                self.ended.add(run_id)
                frames.append(_frame("items", (await self.hub.base(run_id, follow.first)).snapshot))
        if progress is None or progress.run_id in self.ended:
            return frames
        run_id = progress.run_id
        if (follow := self.follows.get(run_id)) is None:
            return frames + await self.follow(update.thread_id, run_id, None)
        if progress.attempt > follow.attempt:
            return frames + await self.follow(update.thread_id, run_id, follow.first)
        committed = progress.position
        covered = committed.attempt == follow.attempt
        if follow.held is None and covered:
            if follow.expected is not None and follow.sequence < follow.expected:
                # Output committed a whole refresh interval ago never arrived: it was lost on its way here.
                frames += self.hold(run_id, follow, follow.expected)
            elif committed.sequence > follow.sequence:
                follow.expected = committed.sequence
            elif follow.preview is not None and progress.first > follow.preview:
                # The item sent as a preview is final now.
                return frames + await self.follow(update.thread_id, run_id, follow.first)
            else:
                # Every item before the committed tail was final when this connection had received its output.
                follow.expected, follow.first = None, max(follow.first, progress.first)
        if follow.held is not None and covered and committed.sequence >= follow.held:
            return frames + await self.follow(update.thread_id, run_id, follow.first)
        return frames

    async def follow(self, thread_id: str, run_id: str, first: int | None) -> list[str]:
        """Send a fresh base of the run, then the deltas Redis retains after it up to this connection's position."""
        base = await self.hub.base(run_id, first)
        frames = [_frame("items", base.snapshot)]
        if base.snapshot.complete:
            self.follows.pop(run_id, None)
            self.ended.add(run_id)
            return frames
        follow = self.follows[run_id] = _Follow(thread_id, base.attempt, base.sequence, base.first, base.preview)
        after, until = base.resume_after or "0-0", self.positions.get(thread_id, "0-0")
        try:
            while _order(after) < _order(until):
                batch = await read_range(
                    self.hub.runtime.redis, stream_key(thread_id), after=after, until=until, count=READ_BATCH
                )
                if not batch:
                    break
                for entry in batch:
                    if entry.fields.get("run_id") == run_id:
                        frames += self.deliver(run_id, follow, entry)
                after = batch[-1].id
        except ServiceError:
            frames += self.hold(run_id, follow, None)
        return frames


async def frames(hub: SessionHub, reader: Reader) -> AsyncIterator[str]:
    """SSE for one connection: a snapshot of the session, then live frames until it closes."""
    connection = _Connection(hub, reader)
    try:
        for frame in await connection.open():
            yield frame
        while True:
            try:
                signal = await asyncio.wait_for(reader.receive(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if isinstance(signal, Close):
                return
            for frame in await connection.handle(signal):
                yield frame
    except ServiceError as error:
        # The database or object store cannot serve this connection now; the client reconnects with backoff.
        logger.warning("Session stream closed", extra={"error_code": error.code, "session_id": reader.session_id})
    finally:
        hub.leave(reader)
