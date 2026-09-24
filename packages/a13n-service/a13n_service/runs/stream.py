"""The thread stream: provisional live output of a thread's runs, carried by Redis and served as SSE.

Workers append output deltas and a boundary marker after each checkpoint, and trim what the boundary's display
covers once a short retention window passes: the stream carries the in-flight tail. Nothing here is durable or
authoritative: display objects hold the durable view, and PostgreSQL decides who may read and which attempt is
current. The stream route turns what the process's `ThreadHub` reads from both into data frames and three control
frames, so a client can always fall back to the durable view:

- `changed {version}`: the thread snapshot is stale; re-read the thread.
- `reset {run_id}`: the run changed attempt; discard its provisional output and re-read its items.
- `gap {run_id}`: deltas were skipped; re-read its items. The next boundary's durable view covers the gap.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from a13n_logging import get_logger
from pydantic import BaseModel
from pydantic.json_schema import JsonSchemaMode, models_json_schema
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.redis import StreamEntry, append, last_id, read, read_entry, read_range, trim
from a13n_service.runs.display import ItemRef, Observed
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import AttemptRow, ThreadRow
from a13n_service.runs.threads import get_thread
from a13n_service.settings import Settings
from a13n_service.tenancy.access import Access, Authenticated, reauthenticate, workspace_scope

logger = get_logger(__name__)

# Deltas a worker buffers while Redis is slow; beyond it output is dropped and readers see a gap.
WRITE_BUFFER = 1024
# A larger delta (a big tool result) is dropped the same way: readers see a gap and read the committed items.
MAX_DELTA_BYTES = 262144
# Entries one connection may fall behind before it skips ahead and reports a gap.
READ_BUFFER = 1024
READ_BATCH = 256
BLOCK_MS = 1000
KEEPALIVE_SECONDS = 15


STREAM_PREFIX = "a13n:thread:"
# A stream entry ID as Redis assigns it: the only cursor `Last-Event-ID` may name.
EVENT_ID = r"^\d{1,20}-\d{1,20}$"


def stream_key(thread_id: str) -> str:
    return STREAM_PREFIX + thread_id


class Delta(BaseModel):
    """A data frame: one AG-UI event of a run's attempt at its per-attempt sequence, and the item it changed."""

    run_id: str
    attempt: int
    sequence: int
    event: dict[str, Any]
    item: ItemRef | None


class Boundary(BaseModel):
    """The run committed a checkpoint whose display covers the attempt up to `sequence`."""

    run_id: str
    attempt: int
    sequence: int


class Changed(BaseModel):
    version: int


class RunSignal(BaseModel):
    """The payload of `reset` and `gap`."""

    run_id: str


type FrameName = Literal["delta", "boundary", "changed", "reset", "gap"]
# The SSE `event` name of each frame and the model of its `data`.
FRAMES: dict[FrameName, type[BaseModel]] = {
    "delta": Delta,
    "boundary": Boundary,
    "changed": Changed,
    "reset": RunSignal,
    "gap": RunSignal,
}


def frames_schema() -> dict[str, Any]:
    """The exported wire contract: each frame's `data` JSON schema by its SSE `event` name."""
    mode: JsonSchemaMode = "serialization"
    references, definitions = models_json_schema(
        [(model, mode) for model in dict.fromkeys(FRAMES.values())], ref_template="#/$defs/{model}"
    )
    return {
        "title": "Thread stream frames",
        "frames": {name: references[(model, mode)] for name, model in FRAMES.items()},
        **definitions,
    }


# Writing


class ThreadStream:
    """One attempt's appends and the trims its boundaries allow. A bounded buffer and a background writer keep Redis
    latency off execution."""

    def __init__(self, redis: Redis, settings: Settings, *, thread_id: str, run_id: str, attempt: int):
        self.redis, self.settings = redis, settings
        self.key, self.run_id, self.attempt = stream_key(thread_id), run_id, attempt
        self.buffer: asyncio.Queue[dict[str, str]] = asyncio.Queue(WRITE_BUFFER)
        self.writer: asyncio.Task[None] | None = None

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
        self.writer.cancel()

    def delta(self, observed: Observed) -> None:
        event = json.dumps(observed.event, separators=(",", ":"))
        if len(event) > MAX_DELTA_BYTES:
            return
        fields = {"sequence": str(observed.sequence), "event": event}
        if observed.item is not None:
            fields["item"] = observed.item.model_dump_json()
        self._put(fields)

    def boundary(self, sequence: int) -> None:
        self._put({"sequence": str(sequence), "boundary": "1"})

    def _put(self, fields: dict[str, str]) -> None:
        try:
            self.buffer.put_nowait({"run_id": self.run_id, "attempt": str(self.attempt), **fields})
        except asyncio.QueueFull:
            pass  # Readers detect the missing sequence and fall back to committed items.

    async def _write(self) -> None:
        worker, timeout = self.settings.worker, self.settings.redis.timeout
        while True:
            entries = [await self.buffer.get()]
            while not self.buffer.empty() and len(entries) < READ_BATCH:
                entries.append(self.buffer.get_nowait())
            ids = await append(
                self.redis, self.key, entries, max_length=worker.stream_length, ttl=worker.stream_ttl, timeout=timeout
            )
            # Redis returns no IDs for entries it dropped; their boundary trims nothing.
            boundaries = [entry_id for entry_id, fields in zip(ids, entries, strict=bool(ids)) if "boundary" in fields]
            if boundaries:
                retained = _retained_from(boundaries[-1], worker.stream_trim_seconds)
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
class Snapshot:
    """What a stream needs from PostgreSQL: the thread version and its active run's latest attempt."""

    version: int
    run_id: str | None
    attempt: int


class Revoked(Exception):
    """The reader lost access; its stream ends."""


type Signal = StreamEntry | Snapshot | Revoked | None  # None: Redis failed, deltas may be missing


@dataclass(eq=False)
class Reader:
    credential: Authenticated
    workspace_id: str
    thread_id: str
    signals: asyncio.Queue[Signal] = field(default_factory=lambda: asyncio.Queue(READ_BUFFER))
    overflowed: bool = False

    def send(self, signal: Signal) -> None:
        try:
            self.signals.put_nowait(signal)
        except asyncio.QueueFull:
            self.overflowed = True


async def _snapshots(session: AsyncSession, thread_ids: list[str]) -> dict[str, Snapshot]:
    latest = (
        select(func.max(AttemptRow.number))
        .where(AttemptRow.run_id == ThreadRow.current_run_id)
        .correlate(ThreadRow)
        .scalar_subquery()
    )
    rows = await session.execute(
        select(ThreadRow.id, ThreadRow.version, ThreadRow.current_run_id, latest).where(ThreadRow.id.in_(thread_ids))
    )
    return {thread_id: Snapshot(version, run_id, attempt or 0) for thread_id, version, run_id, attempt in rows}


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


class ThreadHub:
    """All thread streams of this process share one blocking XREAD and one periodic authority pass."""

    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.readers: dict[str, set[Reader]] = {}
        self.cursors: dict[str, str] = {}
        self.joined = asyncio.Event()

    async def run(self) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(self._read())
            group.create_task(self._refresh())

    async def join(self, reader: Reader) -> str:
        """Register the reader and return the position after which it receives live entries."""
        key = stream_key(reader.thread_id)
        if key not in self.cursors:
            position = await last_id(self.runtime.redis, key)
            self.cursors.setdefault(key, position)
        # No await between reading the cursor and registering: live entries start exactly after it.
        self.readers.setdefault(reader.thread_id, set()).add(reader)
        self.joined.set()
        return self.cursors[key]

    def leave(self, reader: Reader) -> None:
        readers = self.readers.get(reader.thread_id, set())
        readers.discard(reader)
        if not readers:
            self.readers.pop(reader.thread_id, None)
            self.cursors.pop(stream_key(reader.thread_id), None)

    async def snapshot(self, thread_id: str) -> Snapshot | None:
        async with short_session(self.runtime.storage) as session:
            return (await _snapshots(session, [thread_id])).get(thread_id)

    async def _read(self) -> None:
        while True:
            if not self.cursors:
                self.joined.clear()
                await self.joined.wait()
                continue
            try:
                entries = await read(self.runtime.redis, dict(self.cursors), count=READ_BATCH, block_ms=BLOCK_MS)
            except ServiceError:
                for readers in self.readers.values():
                    for reader in readers:
                        reader.send(None)
                await asyncio.sleep(BLOCK_MS / 1000)
                continue
            for entry in entries:
                if entry.key not in self.cursors:
                    continue
                self.cursors[entry.key] = entry.id
                for reader in self.readers.get(entry.key.removeprefix(STREAM_PREFIX), ()):
                    reader.send(entry)

    async def _refresh(self) -> None:
        while True:
            await asyncio.sleep(self.runtime.settings.control.stream_refresh_seconds)
            if not self.readers:
                continue
            try:
                await self._refresh_once()
            except Exception as error:
                logger.warning("Thread stream refresh failed", extra={"error_type": type(error).__name__})

    async def _refresh_once(self) -> None:
        """One snapshot query for every watched thread, and one authority check per credential and workspace."""
        readers = [reader for group in self.readers.values() for reader in group]
        async with short_session(self.runtime.storage) as session:
            snapshots = await _snapshots(session, list(self.readers))
            readable: dict[tuple[str, str], bool] = {}
            for reader in readers:
                key = (reader.credential.credential_id, reader.workspace_id)
                if key not in readable:
                    readable[key] = await _may_read(
                        session, self.runtime.access, reader.credential, reader.workspace_id
                    )
                snapshot = snapshots.get(reader.thread_id)
                reader.send(snapshot if readable[key] and snapshot is not None else Revoked())


async def open_stream(runtime: Runtime, credential: Authenticated, workspace_id: str, thread_id: str) -> Reader:
    """Authorize before the response starts, so refusals are ordinary HTTP errors."""
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, credential.principal, workspace_id, "read")
        thread = await get_thread(session, scope.workspace_id, thread_id)
    return Reader(credential, scope.workspace_id, thread.id)


def _frame(event: FrameName, data: BaseModel, entry_id: str | None = None) -> str:
    head = f"id: {entry_id}\n" if entry_id is not None else ""
    return f"{head}event: {event}\ndata: {data.model_dump_json()}\n\n"


def _order(entry_id: str) -> tuple[int, int]:
    milliseconds, _, sequence = entry_id.partition("-")
    return int(milliseconds), int(sequence or 0)


@dataclass
class _View:
    """The connection's position: the active run, its current attempt and the last sequence per attempt."""

    hub: ThreadHub
    thread_id: str
    snapshot: Snapshot
    sequences: dict[tuple[str, int], int] = field(default_factory=dict)
    inactive: set[str] = field(default_factory=set)

    def seed(self, entry: StreamEntry) -> None:
        fields = entry.fields
        self.sequences[(fields["run_id"], int(fields["attempt"]))] = int(fields["sequence"])

    def gap(self) -> list[str]:
        run_id = self.snapshot.run_id
        return [_frame("gap", RunSignal(run_id=run_id))] if run_id is not None else []

    def _replaced(self, run_id: str, attempt: int) -> bool:
        """A later attempt of the active run supersedes output of an earlier one; the first attempt replaces none."""
        return run_id == self.snapshot.run_id and attempt > self.snapshot.attempt > 0

    def update(self, snapshot: Snapshot) -> list[str]:
        frames: list[str] = []
        if snapshot.version != self.snapshot.version:
            frames.append(_frame("changed", Changed(version=snapshot.version)))
        if snapshot.run_id is not None and self._replaced(snapshot.run_id, snapshot.attempt):
            frames.append(_frame("reset", RunSignal(run_id=snapshot.run_id)))
        self.snapshot = snapshot
        return frames

    async def entry(self, entry: StreamEntry) -> list[str]:
        fields = entry.fields
        run_id, attempt, sequence = fields["run_id"], int(fields["attempt"]), int(fields["sequence"])
        frames: list[str] = []
        if run_id != self.snapshot.run_id and run_id not in self.inactive:
            # A run this connection has not seen: the snapshot may be older than the stream.
            snapshot = await self.hub.snapshot(self.thread_id)
            if snapshot is not None:
                frames += self.update(snapshot)
            if run_id != self.snapshot.run_id:
                self.inactive.add(run_id)
        if run_id != self.snapshot.run_id or attempt < self.snapshot.attempt:
            return frames
        if attempt > self.snapshot.attempt:
            if self._replaced(run_id, attempt):
                frames.append(_frame("reset", RunSignal(run_id=run_id)))
            self.snapshot = Snapshot(self.snapshot.version, run_id, attempt)
        if "boundary" in fields:
            # A boundary past this connection's last sequence of the attempt covers deltas it never received: dropped
            # by the writer or removed before it read them. After the gap's re-read, deltas continue from the boundary.
            if self.sequences.get((run_id, attempt), 0) < sequence:
                frames.append(_frame("gap", RunSignal(run_id=run_id)))
            self.sequences[(run_id, attempt)] = sequence
            return [*frames, _frame("boundary", Boundary(run_id=run_id, attempt=attempt, sequence=sequence), entry.id)]
        expected = self.sequences.get((run_id, attempt), 0) + 1
        if sequence != expected:
            frames.append(_frame("gap", RunSignal(run_id=run_id)))
        self.sequences[(run_id, attempt)] = sequence
        delta = Delta(
            run_id=run_id,
            attempt=attempt,
            sequence=sequence,
            event=json.loads(fields["event"]),
            item=ItemRef.model_validate_json(fields["item"]) if "item" in fields else None,
        )
        return [*frames, _frame("delta", delta, entry.id)]


async def frames(hub: ThreadHub, reader: Reader, last_event_id: str | None) -> AsyncIterator[str]:
    """SSE for one connection: retained entries after its cursor, then live entries and control frames."""
    redis, key = hub.runtime.redis, stream_key(reader.thread_id)
    snapshot = await hub.snapshot(reader.thread_id)
    if snapshot is None:
        return
    view = _View(hub, reader.thread_id, snapshot)
    try:
        live_after = await hub.join(reader)
        after = last_event_id or "0-0"
        if last_event_id is not None:
            # Resuming continues the sequence of the client's last entry; a trimmed cursor lost what followed it.
            if (seen := await read_entry(redis, key, last_event_id)) is not None:
                view.seed(seen)
            else:
                for frame in view.gap():
                    yield frame
        while _order(after) < _order(live_after):
            batch = await read_range(redis, key, after=after, until=live_after, count=READ_BATCH)
            if not batch:
                break
            for entry in batch:
                for frame in await view.entry(entry):
                    yield frame
            after = batch[-1].id
        while True:
            try:
                signal = await asyncio.wait_for(reader.signals.get(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if reader.overflowed:
                reader.overflowed = False
                for frame in view.gap():
                    yield frame
            match signal:
                case StreamEntry():
                    for frame in await view.entry(signal):
                        yield frame
                case Snapshot():
                    for frame in view.update(signal):
                        yield frame
                case Revoked():
                    return
                case None:
                    for frame in view.gap():
                        yield frame
    except ServiceError:
        for frame in view.gap():
            yield frame
    finally:
        hub.leave(reader)
