"""Run state and display objects, the fenced checkpoint commit and run-prefix cleanup.

Objects are immutable and digest-keyed under the run's prefix. Only the typed pointers on the run row make
them reachable, and only a transaction proving the worker lease moves those pointers, so a stale attempt's
late bytes are garbage, never state.
"""

import asyncio
import hashlib
from collections.abc import Sequence
from typing import Literal

from a13n_harness import HarnessState
from a13n_logging import get_logger
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import ColumnElement, and_, exists, or_
from sqlalchemy.orm import aliased

from a13n_service.infra.db import transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.objects.interface import ObjectRef, ObjectStore, read
from a13n_service.runs import inbox
from a13n_service.runs.attempts import Lease, LeaseLost, lock_thread_lease
from a13n_service.runs.display import Display, StreamPosition
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Outcome
from a13n_service.runs.tables import RunRow
from a13n_service.runs.usage import UsageReport, ingest

logger = get_logger(__name__)

# Bumped only with an explicit migration or rejection plan for outstanding checkpoints.
FORMAT = 1

type ObjectKind = Literal["state", "display"]


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
    # A completed or waiting outcome, committed with the state that produced it so the seal writes nothing.
    outcome: Outcome | None = None
    # The Harness deferred requests of a waiting outcome, which the successor's resume answers.
    deferred: JsonValue = None


def prefix(organization_id: str, run_id: str, kind: ObjectKind) -> str:
    return f"orgs/{organization_id}/runs/{run_id}/{kind}"


async def publish(objects: ObjectStore, organization_id: str, run_id: str, kind: ObjectKind, data: bytes) -> ObjectRef:
    """Write outside any session; the caller commits the reference only after the store acknowledged it."""
    digest = hashlib.sha256(data).hexdigest()
    return await objects.put(f"{prefix(organization_id, run_id, kind)}/{digest}", data, content_type="application/json")


def _ref(run: RunRow, kind: ObjectKind, pointer: Pointer) -> ObjectRef:
    return ObjectRef(
        key=f"{prefix(run.organization_id, run.id, kind)}/{pointer.digest}",
        digest=pointer.digest,
        size=pointer.size,
        content_type="application/json",
    )


def compatible(run: RunRow) -> bool:
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


def require_compatible(run: RunRow) -> None:
    if run.checkpoint is None or not compatible(run):
        raise conflict("run", run.id, "checkpoint_incompatible")


async def load_state(objects: ObjectStore, run: RunRow) -> RunState | None:
    if run.checkpoint is None:
        return None
    require_compatible(run)
    pointer = StatePointer.model_validate(run.checkpoint)
    return RunState.model_validate_json(await read(objects, _ref(run, "state", pointer)))


async def load_display(objects: ObjectStore, run: RunRow) -> Display | None:
    if run.display is None:
        return None
    pointer = DisplayPointer.model_validate(run.display)
    return Display.model_validate_json(await read(objects, _ref(run, "display", pointer)))


class Committed(_Frozen):
    """The pointers of the checkpoint a run holds, as its current attempt last committed or restored them."""

    state: StatePointer
    display: DisplayPointer

    @classmethod
    def of(cls, run: RunRow) -> "Committed | None":
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


async def save(
    runtime: Runtime,
    lease: Lease,
    *,
    previous: Committed | None,
    state: RunState,
    display: Display,
    memory_cursors: dict[str, str | None],
    consumed: Sequence[str],
    usage: Sequence[UsageReport],
) -> Committed:
    """The checkpoint commit: publish both objects, then move their pointers in one fenced transaction.

    The transaction also stores the memory cursors the state's history was delivered, consumes the entries the
    state incorporated and ingests pending usage, so pointers, cursors, consumption and usage move together or
    not at all; it is the checkpoint's durability point. `previous`
    must still be the run's pointers: any other value means another writer moved them, which the lease
    predicate already rules out, but consuming input against the wrong state would break at-most-once
    incorporation. The replaced objects are deleted after commit, best effort: takeover and seal cleanup
    reclaim what a failed deletion leaves.
    """
    org, run_id = lease.organization_id, lease.run_id
    state_ref, display_pointer = await asyncio.gather(
        publish(runtime.objects, org, run_id, "state", state.model_dump_json().encode()),
        publish_display(runtime, lease, display),
    )
    committed = Committed(
        state=StatePointer(
            digest=state_ref.digest, size=state_ref.size, format=FORMAT, seq=state.seq, attempt=state.attempt
        ),
        display=display_pointer,
    )
    async with transaction(runtime.storage) as session:
        # Thread first: consuming entries fires the thread-version trigger, which updates the thread row.
        _, run, attempt, current = await lock_thread_lease(session, lease)
        if Committed.of(run) != previous:
            raise LeaseLost()
        run.checkpoint = committed.state.model_dump(mode="json")
        run.display = committed.display.model_dump(mode="json")
        run.memory_cursors = memory_cursors
        await inbox.consume(session, run.id, consumed, checkpoint_seq=state.seq, at=current)
        await ingest(session, run, attempt, usage)
    if previous is not None:
        replaced: tuple[tuple[ObjectKind, Pointer, Pointer], ...] = (
            ("state", previous.state, committed.state),
            ("display", previous.display, committed.display),
        )
        for kind, old, new in replaced:
            if old.digest == new.digest:
                continue
            try:
                await runtime.objects.delete(f"{prefix(org, run_id, kind)}/{old.digest}")
            except Exception as error:
                logger.warning(
                    "Replaced checkpoint object was not deleted",
                    extra={"run_id": run_id, "kind": kind, "error_type": type(error).__name__},
                )
    return committed


async def clean(objects: ObjectStore, run: RunRow, *, limit: int = 1000) -> None:
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
