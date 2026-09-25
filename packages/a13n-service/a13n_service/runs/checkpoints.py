"""Run state and display objects, the fenced checkpoint commit and run-prefix cleanup.

Objects are immutable and digest-keyed under the run's prefix. Only the typed pointers on the run row make
them reachable, and only a transaction proving the worker lease moves those pointers, so a stale attempt's
late bytes are garbage, never state.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Literal

from a13n_harness import HarnessState
from a13n_logging import exception_details, get_logger
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import ColumnElement, and_, exists, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from a13n_service.infra.db import after_commit
from a13n_service.infra.errors import conflict
from a13n_service.infra.objects.interface import ObjectRef, ObjectStore, read
from a13n_service.runs import inbox
from a13n_service.runs.attempts import Lease, LeaseLost
from a13n_service.runs.display import Display, StreamPosition
from a13n_service.runs.schemas import RunInput
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.runs.usage import UsageReport, ingest

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)

# Bumped only with an explicit migration or rejection plan for outstanding checkpoints.
FORMAT = 1

type ObjectKind = Literal["state", "display"]


# Readers accept the row while in persistence, or detached values after the session closes.
type RunObjects = RunRow | RunInput | _Objects


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pointer(_Frozen):
    """What the run row holds for one object: its digest and size, and the format that wrote it."""

    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    format: int


class StatePointer(Pointer):
    """`runs.checkpoint`: also the checkpoint's sequence and the attempt that committed it."""

    seq: int = Field(ge=1)
    attempt: int = Field(ge=1)


class DisplayPointer(Pointer):
    """`runs.display`: also the stream position the display covers."""

    position: StreamPosition


class RunState(_Frozen):
    format: Literal[1] = FORMAT
    harness: HarnessState
    seq: int = Field(ge=1)
    attempt: int = Field(ge=1)
    # The Harness deferred requests of a waiting outcome, which the successor's resume answers.
    deferred: JsonValue = None


def prefix(organization_id: str, run_id: str, kind: ObjectKind) -> str:
    return f"orgs/{organization_id}/runs/{run_id}/{kind}"


async def publish(objects: ObjectStore, organization_id: str, run_id: str, kind: ObjectKind, data: bytes) -> ObjectRef:
    """Write outside any session; the caller commits the reference only after the store acknowledged it."""
    digest = hashlib.sha256(data).hexdigest()
    return await objects.put(f"{prefix(organization_id, run_id, kind)}/{digest}", data, content_type="application/json")


def _ref(run: RunObjects, kind: ObjectKind, pointer: Pointer) -> ObjectRef:
    return ObjectRef(
        key=f"{prefix(run.organization_id, run.id, kind)}/{pointer.digest}",
        digest=pointer.digest,
        size=pointer.size,
        content_type="application/json",
    )


def compatible(run: RunObjects) -> bool:
    """Whether this build can continue from the run's checkpoint; a run without one always can."""
    return run.checkpoint is None or StatePointer.model_validate(run.checkpoint).format == FORMAT


def claimable() -> ColumnElement[bool]:
    """Runs this build claims: those whose checkpoint has a format no newer than its own. A run without one yet
    starts from its parent's, so that one decides, and a run without either always qualifies.

    A newer format waits for a newer worker during a rolling deploy. An older one no build reads again, since
    `FORMAT` only moves on with a plan for outstanding checkpoints, so this build claims it to fail it.
    """
    parent = aliased(RunRow)
    newer_parent = exists().where(parent.id == RunRow.parent_run_id, parent.checkpoint["format"].as_integer() > FORMAT)
    return or_(and_(RunRow.checkpoint.is_(None), ~newer_parent), RunRow.checkpoint["format"].as_integer() <= FORMAT)


def require_compatible(run: RunObjects) -> None:
    if run.checkpoint is None or not compatible(run):
        raise conflict("run", run.id, "checkpoint_incompatible")


async def load_state(objects: ObjectStore, run: RunObjects) -> RunState | None:
    if run.checkpoint is None:
        return None
    require_compatible(run)
    pointer = StatePointer.model_validate(run.checkpoint)
    return RunState.model_validate_json(await read(objects, _ref(run, "state", pointer)))


async def load_display(objects: ObjectStore, run: RunObjects) -> Display | None:
    if run.display is None:
        return None
    pointer = DisplayPointer.model_validate(run.display)
    return Display.model_validate_json(await read(objects, _ref(run, "display", pointer)))


class Committed(_Frozen):
    """The pointers of the checkpoint a run holds, as its current attempt last committed or restored them."""

    state: StatePointer
    display: DisplayPointer

    @classmethod
    def of(cls, run: RunObjects) -> Committed | None:
        if run.checkpoint is None or run.display is None:
            return None
        return cls(
            state=StatePointer.model_validate(run.checkpoint), display=DisplayPointer.model_validate(run.display)
        )


async def publish_display(runtime: Runtime, lease: Lease, display: Display) -> DisplayPointer:
    ref = await publish(
        runtime.objects, lease.organization_id, lease.run_id, "display", display.model_dump_json().encode()
    )
    return DisplayPointer(digest=ref.digest, size=ref.size, format=FORMAT, position=display.position)


async def publish_checkpoint(runtime: Runtime, lease: Lease, state: RunState, display: Display) -> Committed:
    """Write the checkpoint's objects outside any session; `commit` makes them the run's checkpoint."""
    state_ref, display_pointer = await asyncio.gather(
        publish(runtime.objects, lease.organization_id, lease.run_id, "state", state.model_dump_json().encode()),
        publish_display(runtime, lease, display),
    )
    return Committed(
        state=StatePointer(
            digest=state_ref.digest, size=state_ref.size, format=FORMAT, seq=state.seq, attempt=state.attempt
        ),
        display=display_pointer,
    )


async def commit(
    session: AsyncSession,
    runtime: Runtime,
    run: RunRow,
    attempt: AttemptRow,
    *,
    previous: Committed | None,
    committed: Committed,
    memory_cursors: dict[str, str | None],
    consumed: Sequence[str],
    usage: Sequence[UsageReport],
    at: datetime,
) -> None:
    """The checkpoint commit, in the caller's transaction under the thread → run → attempt lease locks: move the
    pointers to the published objects.

    The transaction also stores the memory cursors the state's history was delivered, consumes the entries the
    state incorporated and ingests pending usage, so pointers, cursors, consumption and usage move together or
    not at all; it is the checkpoint's durability point. `previous` must still be the run's pointers: any other
    value means another writer moved them, which the lease predicate already rules out, but consuming input
    against the wrong state would break at-most-once incorporation. After commit, the process queues replaced
    objects for best-effort deletion; takeover and seal cleanup reclaim what failed or skipped deletion leaves.
    """
    if Committed.of(run) != previous:
        raise LeaseLost()
    run.checkpoint = committed.state.model_dump(mode="json")
    run.display = committed.display.model_dump(mode="json")
    run.memory_cursors = memory_cursors
    await inbox.consume(session, run.id, consumed, checkpoint_seq=committed.state.seq, at=at)
    await ingest(session, run, attempt, usage)
    if previous is not None:
        after_commit(
            session,
            partial(runtime.cleanup.discard, runtime.objects, run.organization_id, run.id, previous, committed),
        )


async def _discard(
    objects: ObjectStore, organization_id: str, run_id: str, previous: Committed, committed: Committed
) -> None:
    replaced: tuple[tuple[ObjectKind, Pointer, Pointer], ...] = (
        ("state", previous.state, committed.state),
        ("display", previous.display, committed.display),
    )
    for kind, old, new in replaced:
        if old.digest == new.digest:
            continue
        # Display bytes can repeat while no event advances the stream. Only retire an older position:
        # later checkpoints never return to it, even if the item content itself repeats.
        if (
            isinstance(old, DisplayPointer)
            and isinstance(new, DisplayPointer)
            and (old.position.attempt, old.position.sequence) >= (new.position.attempt, new.position.sequence)
        ):
            continue
        try:
            await objects.delete(f"{prefix(organization_id, run_id, kind)}/{old.digest}")
        except Exception as error:
            logger.warning(
                "Replaced checkpoint object was not deleted",
                extra={
                    "run_id": run_id,
                    "kind": kind,
                    "error_type": type(error).__name__,
                    "exception_details": exception_details(error),
                },
            )


async def clean(objects: ObjectStore, run: RunObjects, *, limit: int = 1000) -> None:
    """Delete every state/display object of this run that its pointers do not name.

    Callers are the run's owners only: a worker after its own commit, a takeover before entering the
    Harness, and every seal after commit. Objects named by frozen pointers are never deleted.
    """
    pointers: tuple[tuple[ObjectKind, dict | None], ...] = (("state", run.checkpoint), ("display", run.display))
    keep = {f"{prefix(run.organization_id, run.id, kind)}/{value['digest']}" for kind, value in pointers if value}
    for kind, _ in pointers:
        for key in await objects.keys(prefix(run.organization_id, run.id, kind), limit=limit):
            if key not in keep:
                await objects.delete(key)


class _Objects(_Frozen):
    id: str
    organization_id: str
    checkpoint: dict | None
    display: dict | None


class Cleanup:
    """Best-effort reclamation off the commit path, owned by the process lifespan.

    One consumer bounds object-store load. A full buffer drops hints; takeover and seal still reclaim the
    prefix. Each job gets one total object-I/O budget, rather than one budget for every deletion.
    """

    BUFFER = 256

    def __init__(self, *, timeout: float):
        self.timeout = timeout
        self.queue: asyncio.Queue[Callable[[], Awaitable[None]]] = asyncio.Queue(self.BUFFER)

    async def discard(
        self, objects: ObjectStore, organization_id: str, run_id: str, previous: Committed, committed: Committed
    ) -> None:
        self._offer(partial(_discard, objects, organization_id, run_id, previous, committed))

    async def sealed(self, objects: ObjectStore, run: RunObjects) -> None:
        frozen = _Objects(
            id=run.id, organization_id=run.organization_id, checkpoint=run.checkpoint, display=run.display
        )
        self._offer(partial(clean, objects, frozen))

    def _offer(self, work: Callable[[], Awaitable[None]]) -> None:
        try:
            self.queue.put_nowait(work)
        except asyncio.QueueFull:
            logger.warning("Checkpoint cleanup buffer full")

    async def run(self) -> None:
        while True:
            work = await self.queue.get()
            try:
                async with asyncio.timeout(self.timeout):
                    await work()
            except Exception as error:
                logger.warning(
                    "Checkpoint cleanup failed",
                    extra={"error_type": type(error).__name__, "exception_details": exception_details(error)},
                )
            finally:
                self.queue.task_done()
