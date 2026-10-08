"""Run objects, the fenced checkpoint commit and run-prefix cleanup.

A run writes its objects under `orgs/{org}/runs/{run}/{kind}/{attempt}/`, each to a new key and compressed with
zstd, so a key never recurs:

- `state` and `tail`: a checkpoint's state and the display items not yet in a page, replaced by each checkpoint;
- `pages`: display history pages, kept with the run once its page catalog records them;
- `contents` and `subagents`: large binary content and ended inline subagent states the Harness saves through
  the run's state store, kept while the run's checkpoint references them.
- `display-contents`: complete display fields and private parser state, kept by tail and page dependencies.

Only the typed pointers on the run row, its page catalog and their recorded dependencies make objects reachable,
and only a transaction proving the worker lease moves them, so a stale attempt's late bytes are garbage, never state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Literal

import anyio
import zstandard
from a13n_harness import HarnessState, StateStore, StoredRef
from a13n_harness.state import StoredKind
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import ColumnElement, and_, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from a13n_service.infra.db import transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.objects.interface import ObjectRef, ObjectStore, new_key, read
from a13n_service.infra.outbox import Claim, OutboxKind, OutboxRow, enqueue, prepare, settle
from a13n_service.infra.telemetry import meter
from a13n_service.runs import inbox
from a13n_service.runs.attempts import AttemptControl, Lease, LeaseLost
from a13n_service.runs.contents import ContentObject, Contents, decode, live_continuation
from a13n_service.runs.display import Page, Snapshot, StreamPosition, Tail
from a13n_service.runs.tables import AttemptRow, RunItemPageRow, RunRow
from a13n_service.runs.usage import UsageReport, ingest

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

# Bumped only with an explicit migration or rejection plan for outstanding checkpoints.
FORMAT = 1
DISPLAY_FORMAT = 2

type ObjectKind = Literal["state", "tail", "pages", "contents", "subagents", "display-contents"]

_STORED_KINDS: dict[StoredKind, ObjectKind] = {"content": "contents", "subagent_state": "subagents"}

OBJECT_BYTES = meter.create_histogram(
    "a13n.run_object.bytes",
    unit="By",
    description="Compressed size of each run object a worker writes, by kind",
    explicit_bucket_boundaries_advisory=(1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216),
)
CHECKPOINT_DURATION = meter.create_histogram(
    "a13n.checkpoint.duration",
    unit="s",
    description="Time from a checkpoint's first object write to its commit",
    explicit_bucket_boundaries_advisory=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pointer(_Frozen):
    """What the run row holds for one object: its key, digest and size, and the format that wrote it."""

    key: str
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    format: int


class StatePointer(Pointer):
    """`runs.checkpoint`: also the checkpoint's sequence, the attempt that committed it, and every saved value the
    state needs at any depth, which cleanup keeps."""

    seq: int = Field(ge=1)
    attempt: int = Field(ge=1)
    refs: tuple[StoredRef, ...] = ()


class TailPointer(Pointer):
    """`runs.tail`: also the stream position the display covers and the ordinals the tail holds, `first` through
    `count`; every earlier item is in a page."""

    position: StreamPosition
    first: int = Field(ge=1)
    count: int = Field(ge=0)
    refs: tuple[ContentObject, ...] = ()


class PageRef(_Frozen):
    """A written page and the ordinals of its first and last items."""

    key: str
    digest: str
    size: int
    first: int
    last: int
    refs: tuple[ContentObject, ...] = ()


class RunState(_Frozen):
    format: Literal[1] = FORMAT
    harness: HarnessState
    seq: int = Field(ge=1)
    attempt: int = Field(ge=1)
    # The Harness deferred requests of a waiting outcome, which the successor's resume answers.
    deferred: JsonValue = None
    # The resume's optional ordinary input has reached a model request in this checkpoint.
    resume_input_consumed: bool = False


def run_prefix(organization_id: str, run_id: str) -> str:
    return f"orgs/{organization_id}/runs/{run_id}"


def prefix(organization_id: str, run_id: str, kind: ObjectKind) -> str:
    return f"{run_prefix(organization_id, run_id)}/{kind}"


def _attempt(key: str) -> int:
    """The attempt that wrote a run object, named by the segment before the key's final one."""
    return int(key.rsplit("/", 2)[1])


async def publish(objects: ObjectStore, lease: Lease, kind: ObjectKind, data: bytes, *, level: int) -> ObjectRef:
    """Compress and write outside any session to a new key of the lease's attempt; the caller commits the reference
    only after the store acknowledged it."""
    key = new_key(f"{prefix(lease.organization_id, lease.run_id, kind)}/{lease.number}")
    ref = await objects.put(key, zstandard.ZstdCompressor(level=level).compress(data), content_type="application/zstd")
    OBJECT_BYTES.record(ref.size, {"kind": kind})
    return ref


def _publish(runtime: Runtime, lease: Lease, kind: ObjectKind, data: bytes) -> Awaitable[ObjectRef]:
    return publish(runtime.objects, lease, kind, data, level=runtime.settings.worker.compression_level)


async def load(objects: ObjectStore, pointer: Pointer | PageRef | StoredRef) -> bytes:
    """The bytes a committed reference names, verified and decompressed."""
    ref = ObjectRef(key=pointer.key, digest=pointer.digest, size=pointer.size, content_type="application/zstd")
    return zstandard.ZstdDecompressor().decompress(await read(objects, ref))


def claimable() -> ColumnElement[bool]:
    """Runs this build claims: those whose checkpoint has a format no newer than its own. A run without one yet
    starts from its parent's, so that one decides, and a run without either always qualifies.

    A newer format waits for a newer worker during a rolling deploy. An older one no build reads again, since
    `FORMAT` only moves on with a plan for outstanding checkpoints, so this build claims it to fail it.
    """
    parent = aliased(RunRow)
    newer_parent = exists().where(parent.id == RunRow.parent_run_id, parent.checkpoint["format"].as_integer() > FORMAT)
    return or_(and_(RunRow.checkpoint.is_(None), ~newer_parent), RunRow.checkpoint["format"].as_integer() <= FORMAT)


def require_compatible(run: RunRow) -> StatePointer | None:
    """The run's checkpoint pointer, if it has one, in a format this build reads."""
    if run.checkpoint is None:
        return None
    if (pointer := StatePointer.model_validate(run.checkpoint)).format != FORMAT:
        raise conflict("run", run.id, "checkpoint_incompatible")
    return pointer


async def load_state(objects: ObjectStore, pointer: StatePointer | None) -> RunState | None:
    if pointer is None:
        return None
    return RunState.model_validate_json(await load(objects, pointer))


async def load_tail(objects: ObjectStore, pointer: TailPointer | None, *, hydrate: bool = False) -> Tail:
    if pointer is None:
        return Tail()
    if pointer.format > DISPLAY_FORMAT:
        raise conflict("display", pointer.key, "display_incompatible")
    tail = Tail.model_validate_json(await load(objects, pointer))
    if hydrate:
        refs = {ref.id: ref for ref in tail.refs}
        values = {identifier: decode(await load(objects, ref), ref) for identifier, ref in refs.items()}
        for item in tail.items:
            for field, ref in item.content_refs.items():
                item.content[field] = values[ref.id]
        if tail.continuation_ref is not None:
            from a13n_stream_protocol.display import DisplayContinuation

            tail.continuation = DisplayContinuation.model_validate(values[tail.continuation_ref.id])
    return tail


async def load_page(objects: ObjectStore, page: PageRef) -> Page:
    return Page.model_validate_json(await load(objects, page))


def near_deadline(runtime: Runtime, control: AttemptControl) -> bool:
    """An object write must land before a takeover could clean the run's prefix, so none starts near it."""
    return control.expiring(runtime.settings.objects.timeout)


class RunObjects(StateStore):
    """The Harness state store of one attempt: each value it saves becomes an object of the attempt."""

    def __init__(self, runtime: Runtime, lease: Lease, control: AttemptControl) -> None:
        super().__init__(content_threshold=runtime.settings.worker.content_bytes)
        self.runtime, self.lease, self.control = runtime, lease, control

    async def save(self, data: bytes, kind: StoredKind) -> StoredRef:
        if near_deadline(self.runtime, self.control):
            raise LeaseLost()
        ref = await _publish(self.runtime, self.lease, _STORED_KINDS[kind], data)
        return StoredRef(key=ref.key, digest=ref.digest, size=ref.size)

    async def load(self, ref: StoredRef) -> bytes:
        return await load(self.runtime.objects, ref)


class Committed(_Frozen):
    """The pointers of the checkpoint a run holds, as its current attempt last committed or restored them."""

    state: StatePointer
    tail: TailPointer

    @classmethod
    def of(cls, run: RunRow) -> Committed | None:
        if run.checkpoint is None or run.tail is None:
            return None
        return cls(state=StatePointer.model_validate(run.checkpoint), tail=TailPointer.model_validate(run.tail))


class DisplayWrite(_Frozen):
    """The display objects one commit makes the run's: its tail, and the pages its catalog gains."""

    tail: TailPointer
    pages: tuple[PageRef, ...]


async def _all(writes: Sequence[Awaitable[ObjectRef]]) -> list[ObjectRef]:
    """Finish every write before a failure can seal and reclaim the prefix: ordinary gather would let another write
    publish an orphan after that final scan had already completed."""
    results = await asyncio.gather(*writes, return_exceptions=True)
    refs: list[ObjectRef] = []
    for result in results:
        if isinstance(result, BaseException):
            raise result
        refs.append(result)
    return refs


def _display_writes(runtime: Runtime, lease: Lease, snapshot: Snapshot) -> list[Awaitable[ObjectRef]]:
    """The writes of a display snapshot: its tail, then its pages."""
    return [
        _publish(runtime, lease, "tail", snapshot.tail.model_dump_json().encode()),
        *(_publish(runtime, lease, "pages", page.model_dump_json().encode()) for page in snapshot.pages),
    ]


def _display_write(snapshot: Snapshot, refs: list[ObjectRef]) -> DisplayWrite:
    tail, *pages = refs
    return DisplayWrite(
        tail=TailPointer(
            key=tail.key,
            digest=tail.digest,
            size=tail.size,
            format=DISPLAY_FORMAT,
            position=snapshot.tail.position,
            first=snapshot.tail.first,
            count=snapshot.tail.first + len(snapshot.tail.items) - 1,
            refs=snapshot.tail.refs,
        ),
        pages=tuple(
            PageRef(
                key=ref.key,
                digest=ref.digest,
                size=ref.size,
                first=page.items[0].ordinal,
                last=page.items[-1].ordinal,
                refs=page.refs,
            )
            for ref, page in zip(pages, snapshot.pages, strict=True)
        ),
    )


async def _contents(
    runtime: Runtime, lease: Lease, snapshot: Snapshot, contents: Contents | None, control: AttemptControl | None
) -> Snapshot:
    contents = contents or Contents()

    async def write(data: bytes) -> ObjectRef:
        if control is not None and near_deadline(runtime, control):
            raise LeaseLost()
        return await _publish(runtime, lease, "display-contents", data)

    pages = []
    for page in snapshot.pages:
        items = [await contents.item(item, write) for item in page.items]
        pages.append(page.model_copy(update={"items": items, "refs": contents.dependencies(items)}))
    items = [await contents.item(item, write) for item in snapshot.tail.items]
    refs = contents.dependencies(items)
    continuation = snapshot.tail.continuation
    continuation_ref = None
    if continuation is not None:
        continuation_ref = await contents.value("continuation", "state", continuation.model_dump(mode="json"), write)
        if continuation_ref is not None:
            refs = (*refs, continuation_ref)
        continuation = live_continuation(continuation)
    tail = snapshot.tail.model_copy(
        update={
            "items": items,
            "refs": refs,
            "continuation": continuation,
            "continuation_ref": continuation_ref,
        }
    )
    return Snapshot(pages, tail)


async def publish_display(
    runtime: Runtime,
    lease: Lease,
    snapshot: Snapshot,
    *,
    contents: Contents | None = None,
    control: AttemptControl | None = None,
) -> DisplayWrite:
    """Write a display snapshot outside any session."""
    snapshot = await _contents(runtime, lease, snapshot, contents, control)
    return _display_write(snapshot, await _all(_display_writes(runtime, lease, snapshot)))


async def publish_checkpoint(
    runtime: Runtime,
    lease: Lease,
    state: RunState,
    snapshot: Snapshot,
    *,
    contents: Contents | None = None,
    control: AttemptControl | None = None,
) -> tuple[StatePointer, DisplayWrite]:
    """Write the checkpoint's objects outside any session; `commit` makes them the run's checkpoint."""
    snapshot = await _contents(runtime, lease, snapshot, contents, control)
    written, *display = await _all(
        [
            _publish(runtime, lease, "state", state.model_dump_json().encode()),
            *_display_writes(runtime, lease, snapshot),
        ]
    )
    pointer = StatePointer(
        key=written.key,
        digest=written.digest,
        size=written.size,
        format=FORMAT,
        seq=state.seq,
        attempt=state.attempt,
        refs=state.harness.refs,
    )
    return pointer, _display_write(snapshot, display)


def record_display(session: AsyncSession, run: RunRow, display: DisplayWrite) -> None:
    """Make a written tail the run's and add its pages to the catalog, in the caller's fenced transaction."""
    run.tail = display.tail.model_dump(mode="json")
    session.add_all(
        RunItemPageRow(
            run_id=run.id,
            first_ordinal=page.first,
            organization_id=run.organization_id,
            workspace_id=run.workspace_id,
            last_ordinal=page.last,
            key=page.key,
            digest=page.digest,
            size=page.size,
            refs=[ref.model_dump(mode="json") for ref in page.refs],
        )
        for page in display.pages
    )


async def commit(
    session: AsyncSession,
    run: RunRow,
    attempt: AttemptRow,
    *,
    previous: Committed | None,
    state: StatePointer,
    display: DisplayWrite,
    memory_cursors: dict[str, str | None],
    consumed: Sequence[str],
    usage: Sequence[UsageReport],
    at: datetime,
) -> Committed:
    """The checkpoint commit, in the caller's transaction under the thread → run → attempt lease locks: move the
    pointers to the published objects and add the published pages to the catalog.

    The transaction also stores the memory cursors the state's history was delivered, consumes the entries the
    state incorporated and ingests pending usage, so pointers, cursors, consumption and usage move together or
    not at all; it is the checkpoint's durability point. `previous` must still be the run's pointers: any other
    value means another writer moved them, which the lease predicate already rules out, but consuming input
    against the wrong state would break at-most-once incorporation. The caller stages reclamation in the same
    transaction; no object I/O runs here.
    """
    if Committed.of(run) != previous:
        raise LeaseLost()
    run.checkpoint = state.model_dump(mode="json")
    record_display(session, run, display)
    run.memory_cursors = memory_cursors
    await inbox.consume(session, run.id, consumed, checkpoint_seq=state.seq, at=at)
    await ingest(session, run, attempt, usage)
    return Committed(state=state, tail=display.tail)


CLEANUP: OutboxKind = "checkpoint_cleanup"


def retire(session: AsyncSession, run: RunRow, previous: Committed | None, committed: Committed) -> None:
    """Persist deletion of the objects the pointer change retired; keys never recur, so none is needed again."""
    if previous is None:
        return
    pairs: tuple[tuple[Pointer, Pointer], ...] = ((previous.state, committed.state), (previous.tail, committed.tail))
    keys: list[JsonValue] = [old.key for old, new in pairs if old.key != new.key]
    if keys:
        enqueue(
            session,
            organization_id=run.organization_id,
            workspace_id=run.workspace_id,
            kind=CLEANUP,
            target={"run_id": run.id},
            payload={"keys": keys},
        )


async def reclaim(session: AsyncSession, run: RunRow, *, before_attempt: int | None = None) -> None:
    """Stage an orphan scan of the run prefix at takeover or seal. It keeps what this transaction captures as the
    run's: its pointers, every value its checkpoint references and every page in its catalog.

    A takeover passes the new attempt's number and deletes only earlier attempts' objects: the new attempt may
    already have published objects that nothing names yet.
    """
    session.add_all(await prepare_reclaims(session, [(run, before_attempt)]))


async def prepare_reclaims(session: AsyncSession, takeovers: Sequence[tuple[RunRow, int | None]]) -> list[OutboxRow]:
    """Capture all takeover keep sets with one page-catalog read."""
    if not takeovers:
        return []
    pages: dict[str, list[str]] = {run.id: [] for run, _ in takeovers}
    for run_id, key, refs in await session.execute(
        select(RunItemPageRow.run_id, RunItemPageRow.key, RunItemPageRow.refs).where(RunItemPageRow.run_id.in_(pages))
    ):
        pages[run_id].append(key)
        pages[run_id].extend(ref["key"] for ref in refs)
    rows: list[OutboxRow] = []
    for run, before_attempt in takeovers:
        keep: list[JsonValue] = [pointer["key"] for pointer in (run.checkpoint, run.tail) if pointer]
        if run.checkpoint is not None:
            keep += [ref.key for ref in StatePointer.model_validate(run.checkpoint).refs]
        if run.tail is not None:
            keep += [ref.key for ref in TailPointer.model_validate(run.tail).refs]
        keep += pages[run.id]
        keep = list(dict.fromkeys(keep))
        rows.append(
            prepare(
                organization_id=run.organization_id,
                workspace_id=run.workspace_id,
                kind=CLEANUP,
                target={"run_id": run.id},
                payload={"keep": keep, "before_attempt": before_attempt, "after": None},
            )
        )
    return rows


async def clean(runtime: Runtime, claimed: Claim) -> None:
    """Deliver a durable reclamation intent outside sessions, with one total I/O budget.

    A scan checkpoints its last completed key and defers remaining pages without consuming an attempt.
    A failure retries the same page; deletes are idempotent. Zero progress at the deadline is a failed attempt.
    """
    assert claimed.organization_id is not None
    organization_id, run_id = claimed.organization_id, claimed.target["run_id"]
    payload = dict(claimed.payload)
    if "keys" in payload:
        with anyio.fail_after(runtime.settings.objects.timeout):
            for key in payload["keys"]:
                await runtime.objects.delete(key)
        async with transaction(runtime.storage) as session:
            await settle(session, claimed, "delivered")
        return

    before, keep = payload["before_attempt"], set(payload["keep"])
    progressed, complete = False, False
    with anyio.move_on_after(runtime.settings.objects.timeout):
        keys = await runtime.objects.keys(run_prefix(organization_id, run_id), limit=1000, after=payload["after"])
        for key in keys:
            if key not in keep and (before is None or _attempt(key) < before):
                await runtime.objects.delete(key)
            payload["after"] = key
            progressed = True
        complete = len(keys) < 1000
    if not complete and not progressed:
        raise TimeoutError("Checkpoint reclamation made no progress")
    async with transaction(runtime.storage) as session:
        if await settle(session, claimed, "delivered" if complete else "deferred") and not complete:
            await session.execute(update(OutboxRow).where(OutboxRow.id == claimed.id).values(payload=payload))
