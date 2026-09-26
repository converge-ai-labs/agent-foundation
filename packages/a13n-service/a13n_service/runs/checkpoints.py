"""Run state and display objects, the fenced checkpoint commit and run-prefix cleanup.

Objects are immutable and digest-keyed under the run's prefix. Only the typed pointers on the run row make
them reachable, and only a transaction proving the worker lease moves those pointers, so a stale attempt's
late bytes are garbage, never state.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Literal

import anyio
from a13n_harness import HarnessState
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import ColumnElement, and_, exists, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from a13n_service.infra.db import transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.objects.interface import ObjectRef, ObjectStore, read
from a13n_service.infra.outbox import Claim, OutboxKind, OutboxRow, enqueue, settle
from a13n_service.runs import inbox
from a13n_service.runs.attempts import Lease, LeaseLost
from a13n_service.runs.display import Display, StreamPosition
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.runs.usage import UsageReport, ingest

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

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
    # The Harness deferred requests of a waiting outcome, which the successor's resume answers.
    deferred: JsonValue = None


def prefix(organization_id: str, run_id: str, kind: ObjectKind) -> str:
    return f"orgs/{organization_id}/runs/{run_id}/{kind}"


async def publish(objects: ObjectStore, organization_id: str, run_id: str, kind: ObjectKind, data: bytes) -> ObjectRef:
    """Write outside any session; the caller commits the reference only after the store acknowledged it."""
    digest = hashlib.sha256(data).hexdigest()
    return await objects.put(f"{prefix(organization_id, run_id, kind)}/{digest}", data, content_type="application/json")


def _ref(organization_id: str, run_id: str, kind: ObjectKind, pointer: Pointer) -> ObjectRef:
    return ObjectRef(
        key=f"{prefix(organization_id, run_id, kind)}/{pointer.digest}",
        digest=pointer.digest,
        size=pointer.size,
        content_type="application/json",
    )


def claimable() -> ColumnElement[bool]:
    """Runs this build claims: those whose checkpoint has a format no newer than its own. A run without one yet
    starts from its parent's, so that one decides, and a run without either always qualifies.

    A newer format waits for a newer worker during a rolling deploy. An older one no build reads again, since
    `FORMAT` only moves on with a plan for outstanding checkpoints, so this build claims it to fail it.
    """
    parent = aliased(RunRow)
    newer_parent = exists().where(parent.id == RunRow.parent_run_id, parent.checkpoint["format"].as_integer() > FORMAT)
    return or_(and_(RunRow.checkpoint.is_(None), ~newer_parent), RunRow.checkpoint["format"].as_integer() <= FORMAT)


def require_compatible(run_id: str, checkpoint: dict | None) -> StatePointer:
    if checkpoint is None or (pointer := StatePointer.model_validate(checkpoint)).format != FORMAT:
        raise conflict("run", run_id, "checkpoint_incompatible")
    return pointer


async def load_state(
    objects: ObjectStore, organization_id: str, run_id: str, pointer: StatePointer | None
) -> RunState | None:
    if pointer is None:
        return None
    if pointer.format != FORMAT:
        raise conflict("run", run_id, "checkpoint_incompatible")
    return RunState.model_validate_json(await read(objects, _ref(organization_id, run_id, "state", pointer)))


async def load_display(
    objects: ObjectStore, organization_id: str, run_id: str, pointer: DisplayPointer | None
) -> Display | None:
    if pointer is None:
        return None
    return Display.model_validate_json(await read(objects, _ref(organization_id, run_id, "display", pointer)))


class Committed(_Frozen):
    """The pointers of the checkpoint a run holds, as its current attempt last committed or restored them."""

    state: StatePointer
    display: DisplayPointer

    @classmethod
    def of(cls, run: RunRow) -> Committed | None:
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
        return_exceptions=True,
    )
    # Finish both writes before a failure can seal and reclaim the prefix. Ordinary gather would let the
    # other write publish an orphan after that final scan had already completed.
    if isinstance(state_ref, BaseException):
        raise state_ref
    if isinstance(display_pointer, BaseException):
        raise display_pointer
    return Committed(
        state=StatePointer(
            digest=state_ref.digest, size=state_ref.size, format=FORMAT, seq=state.seq, attempt=state.attempt
        ),
        display=display_pointer,
    )


async def commit(
    session: AsyncSession,
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
    against the wrong state would break at-most-once incorporation. The caller stages reclamation in the same transaction; no object I/O runs here.
    """
    if Committed.of(run) != previous:
        raise LeaseLost()
    run.checkpoint = committed.state.model_dump(mode="json")
    run.display = committed.display.model_dump(mode="json")
    run.memory_cursors = memory_cursors
    await inbox.consume(session, run.id, consumed, checkpoint_seq=committed.state.seq, at=at)
    await ingest(session, run, attempt, usage)


CLEANUP: OutboxKind = "checkpoint_cleanup"


def retire(session: AsyncSession, run: RunRow, previous: Committed | None, committed: Committed) -> None:
    """Persist deletion of provably retired references alongside the pointer change."""
    if previous is None:
        return
    keys = []
    if previous.state.digest != committed.state.digest:
        keys.append(_ref(run.organization_id, run.id, "state", previous.state).key)
    old, new = previous.display, committed.display
    # A different digest at the same stream position may be published again. Seal collects those orphans.
    if (old.position.attempt, old.position.sequence) < (new.position.attempt, new.position.sequence):
        keys.append(_ref(run.organization_id, run.id, "display", old).key)
    if keys:
        enqueue(
            session,
            organization_id=run.organization_id,
            workspace_id=run.workspace_id,
            kind=CLEANUP,
            target={"run_id": run.id},
            payload={"keys": keys},
        )


def reclaim(session: AsyncSession, run: RunRow, *, before_attempt: int | None = None) -> None:
    """Stage an orphan scan at takeover or seal, preserving the pointers captured in this transaction.

    Active runs only retire state from older attempts. A display can repeat at the same position, so only
    a sealed run can scan display orphans. Newly published state carries its attempt number and is skipped.
    """
    pointers: tuple[tuple[ObjectKind, dict | None], ...] = (("state", run.checkpoint), ("display", run.display))
    keep: list[JsonValue] = [
        f"{prefix(run.organization_id, run.id, kind)}/{value['digest']}" for kind, value in pointers if value
    ]
    enqueue(
        session,
        organization_id=run.organization_id,
        workspace_id=run.workspace_id,
        kind=CLEANUP,
        target={"run_id": run.id},
        payload={"keep": keep, "before_attempt": before_attempt, "after": None},
    )


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

    before = payload["before_attempt"]
    # State-only scans at takeover also avoid reading an entire run prefix that is still growing.
    scan_prefix = (
        prefix(organization_id, run_id, "state") if before is not None else f"orgs/{organization_id}/runs/{run_id}"
    )
    after = payload["after"]
    progressed, complete = False, False
    with anyio.move_on_after(runtime.settings.objects.timeout):
        keys = await runtime.objects.keys(scan_prefix, limit=1000, after=after)
        for key in keys:
            if key not in payload["keep"]:
                if before is None:
                    await runtime.objects.delete(key)
                elif (data := await runtime.objects.get(key)) is not None:
                    state = RunState.model_validate_json(data)
                    if state.attempt < before:
                        await runtime.objects.delete(key)
            payload["after"] = key
            progressed = True
        complete = len(keys) < 1000
    if not complete and not progressed:
        raise TimeoutError("Checkpoint reclamation made no progress")
    async with transaction(runtime.storage) as session:
        if await settle(session, claimed, "delivered" if complete else "deferred") and not complete:
            await session.execute(update(OutboxRow).where(OutboxRow.id == claimed.id).values(payload=payload))
