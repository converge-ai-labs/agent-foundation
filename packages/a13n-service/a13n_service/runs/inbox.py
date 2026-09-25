"""A thread's queued input: append under capacity, pending-only edits, steer assignment and disposition.

Every function here runs inside the caller's transaction, which already holds the thread lock. Input usage
is the count and bytes of pending plus assigned entries; `_overflow` is the only place that compares it with
the thread's limits.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, JsonValue
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import IDEMPOTENCY_KEY_REUSED, ServiceError, conflict, invalid, not_found, rate_limited
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.schemas import EntryUpdate, Failure, Message, RunOptions, canonical_json
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.settings import Control
from a13n_service.tenancy.authorize import ExecutionAuthority

type RequestKind = Literal["thread", "message", "fork"]

OUTSTANDING = ("pending", "assigned")


@dataclass(frozen=True, slots=True)
class Request:
    """Replay evidence for an idempotent message command, stored on the entry it creates."""

    key: str
    kind: RequestKind
    target: str
    digest: str

    @classmethod
    def of(cls, key: str, kind: RequestKind, target: str, body: BaseModel) -> "Request":
        digest = hashlib.sha256(canonical_json([kind, target, body.model_dump(mode="json")])).hexdigest()
        return cls(key=key, kind=kind, target=target, digest=digest)


def payload_size(payload: dict[str, JsonValue]) -> int:
    return len(canonical_json(payload))


async def _overflow(
    session: AsyncSession, thread_id: str, control: Control, *, adding: int, growth: int
) -> dict[str, int] | None:
    """The thread's input usage when `adding` entries and `growth` bytes would exceed its limits, else None."""
    count, size = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(InboxEntryRow.size), 0)).where(
                InboxEntryRow.thread_id == thread_id, InboxEntryRow.status.in_(OUTSTANDING)
            )
        )
    ).one()
    if count + adding <= control.inbox_count and size + growth <= control.inbox_bytes:
        return None
    return {"count": int(count), "bytes": int(size)}


async def has_room(session: AsyncSession, thread_id: str, control: Control, *, adding: int, growth: int) -> bool:
    return await _overflow(session, thread_id, control, adding=adding, growth=growth) is None


async def require_capacity(
    session: AsyncSession, thread_id: str, control: Control, *, adding: int, growth: int
) -> None:
    if (usage := await _overflow(session, thread_id, control, adding=adding, growth=growth)) is not None:
        limits = {"limit_count": control.inbox_count, "limit_bytes": control.inbox_bytes}
        raise rate_limited("Thread inbox is full", 1, {**usage, **limits})


async def find_request(session: AsyncSession, workspace_id: str, principal_id: str, key: str) -> InboxEntryRow | None:
    return await session.scalar(
        select(InboxEntryRow).where(
            InboxEntryRow.workspace_id == workspace_id,
            InboxEntryRow.principal_id == principal_id,
            InboxEntryRow.request_key == key,
        )
    )


def check_replay(entry: InboxEntryRow, request: Request) -> None:
    """A reused key must name the same operation, target and canonical body; pending edits never change it."""
    if (entry.request_kind, entry.request_target, entry.request_digest) != (
        request.kind,
        request.target,
        request.digest,
    ):
        raise conflict("request", request.key, IDEMPOTENCY_KEY_REUSED)


async def _next_position(session: AsyncSession, thread_id: str) -> int:
    highest = await session.scalar(select(func.max(InboxEntryRow.position)).where(InboxEntryRow.thread_id == thread_id))
    return (highest or 0) + 1


async def append_message(
    session: AsyncSession,
    thread: ThreadRow,
    message: Message,
    *,
    principal_id: str,
    authority: ExecutionAuthority,
    request: Request | None,
    control: Control,
) -> InboxEntryRow:
    payload = message.payload.model_dump(mode="json")
    size = payload_size(payload)
    await require_capacity(session, thread.id, control, adding=1, growth=size)
    entry = InboxEntryRow(
        id=new_object_id("inb"),
        organization_id=thread.organization_id,
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        kind="message",
        delivery=message.delivery,
        position=await _next_position(session, thread.id),
        principal_id=principal_id,
        authority=authority.model_dump(mode="json"),
        payload=payload,
        size=size,
        agent_id=message.agent_id,
        agent_revision_id=message.agent_revision_id,
        options=message.options.model_dump(mode="json"),
        request_key=request.key if request else None,
        request_digest=request.digest if request else None,
        request_kind=request.kind if request else None,
        request_target=request.target if request else None,
        status="pending",
    )
    session.add(entry)
    await session.flush()
    return entry


def child_result(child_thread: ThreadRow, child_run: RunRow) -> dict[str, JsonValue]:
    """What the spawning thread reads about one of its sealed child runs, and the edge that delegated it."""
    return {
        "child_run_id": child_run.id,
        "subagent": child_thread.subagent,
        "status": child_run.status,
        "output": child_run.output,
        "failure": child_run.failure,
    }


async def append_child_result(
    session: AsyncSession, thread: ThreadRow, child_run: RunRow, origin_run: RunRow, payload: dict[str, JsonValue]
) -> InboxEntryRow:
    """The one result entry of a sealed child run; the caller found room for `payload` under the thread lock."""
    entry = InboxEntryRow(
        id=new_object_id("inb"),
        organization_id=thread.organization_id,
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        kind="child_result",
        delivery="steer",
        position=await _next_position(session, thread.id),
        principal_id=origin_run.principal_id,
        authority=origin_run.authority,
        payload=payload,
        size=payload_size(payload),
        options={},
        child_run_id=child_run.id,
        origin_run_id=origin_run.id,
        status="pending",
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_entry(session: AsyncSession, thread: ThreadRow, entry_id: str, *, lock: bool = False) -> InboxEntryRow:
    query = select(InboxEntryRow).where(InboxEntryRow.thread_id == thread.id, InboxEntryRow.id == entry_id)
    entry = await session.scalar(query.with_for_update() if lock else query)
    if entry is None:
        raise not_found("inbox_entry", entry_id)
    return entry


def _require_pending(entry: InboxEntryRow) -> None:
    if entry.status != "pending":
        raise conflict("inbox_entry", entry.id, f"entry_{entry.status}")


async def editable_entry(session: AsyncSession, thread: ThreadRow, entry_id: str, *, editor_id: str) -> InboxEntryRow:
    """The locked pending message, when `editor_id` submitted it.

    An entry runs under its submitter's frozen authority, so only that principal may change what it asks for.
    """
    entry = await get_entry(session, thread, entry_id, lock=True)
    _require_pending(entry)
    if entry.kind != "message":
        raise conflict("inbox_entry", entry.id, "not_a_message")
    if entry.principal_id != editor_id:
        raise ServiceError("forbidden", "Only the principal that submitted an entry can edit it", {"id": entry.id})
    return entry


def edited(entry: InboxEntryRow, change: EntryUpdate) -> Message:
    """The message a pending entry becomes with `change` applied; an explicit null revision unpins it."""
    set_revision = "agent_revision_id" in change.model_fields_set
    return Message.model_validate(
        {
            "delivery": change.delivery or entry.delivery,
            "payload": change.payload or entry.payload,
            "agent_id": entry.agent_id,
            "agent_revision_id": change.agent_revision_id if set_revision else entry.agent_revision_id,
            "options": change.options or entry.options,
        }
    )


async def edit_entry(
    session: AsyncSession, thread: ThreadRow, entry: InboxEntryRow, message: Message, *, control: Control
) -> None:
    """Replace an editable entry's content with `message`; its payload bytes are rechecked against capacity."""
    payload = message.payload.model_dump(mode="json")
    size = payload_size(payload)
    await require_capacity(session, thread.id, control, adding=0, growth=size - entry.size)
    entry.payload, entry.size, entry.delivery = payload, size, message.delivery
    entry.agent_revision_id, entry.options = message.agent_revision_id, message.options.model_dump(mode="json")
    await session.flush()


async def withdraw_entry(session: AsyncSession, thread: ThreadRow, entry_id: str, *, at: datetime) -> InboxEntryRow:
    """Withdrawal keeps a tombstone, so the request key can never be reused for different input."""
    entry = await get_entry(session, thread, entry_id, lock=True)
    _require_pending(entry)
    entry.status, entry.finished_at = "withdrawn", at
    await session.flush()
    return entry


async def withdraw_pending(session: AsyncSession, thread: ThreadRow, *, at: datetime) -> None:
    await session.execute(
        update(InboxEntryRow)
        .where(InboxEntryRow.thread_id == thread.id, InboxEntryRow.status == "pending")
        .values(status="withdrawn", finished_at=at)
    )


async def reorder(session: AsyncSession, thread: ThreadRow, entry_ids: Sequence[str]) -> None:
    """The order names exactly the pending entries; they keep their set of positions in the new order."""
    pending = (
        await session.scalars(
            select(InboxEntryRow)
            .where(InboxEntryRow.thread_id == thread.id, InboxEntryRow.status == "pending")
            .order_by(InboxEntryRow.position)
            .with_for_update()
        )
    ).all()
    if {entry.id for entry in pending} != set(entry_ids) or len(pending) != len(entry_ids):
        raise invalid("entry_ids", "must list exactly the pending entries")
    positions = [entry.position for entry in pending]
    by_id = {entry.id: entry for entry in pending}
    await session.execute(text("SET CONSTRAINTS uq_inbox_entries_thread_id_position DEFERRED"))
    for position, entry_id in zip(positions, entry_ids, strict=True):
        by_id[entry_id].position = position
    await session.flush()


async def pending_entries(
    session: AsyncSession, thread_id: str, *, limit: int, messages_only: bool = False
) -> Sequence[InboxEntryRow]:
    """The first `limit` pending entries in position order; `messages_only` skips child results in the query."""
    query = select(InboxEntryRow).where(InboxEntryRow.thread_id == thread_id, InboxEntryRow.status == "pending")
    if messages_only:
        query = query.where(InboxEntryRow.kind == "message")
    return (await session.scalars(query.order_by(InboxEntryRow.position).limit(limit).with_for_update())).all()


def fail(entry: InboxEntryRow, failure: Failure, *, at: datetime) -> None:
    entry.status, entry.failure, entry.finished_at = "failed", failure.model_dump(), at


async def fail_assigned(session: AsyncSession, run_id: str, entry_id: str, failure: Failure, *, at: datetime) -> None:
    """Fail one entry assigned to the run; entries its checkpoints consumed are unaffected."""
    await session.execute(
        update(InboxEntryRow)
        .where(
            InboxEntryRow.id == entry_id,
            InboxEntryRow.assigned_run_id == run_id,
            InboxEntryRow.status == "assigned",
        )
        .values(status="failed", failure=failure.model_dump(), finished_at=at)
    )


def assign(entry: InboxEntryRow, run: RunRow) -> None:
    entry.status, entry.assigned_run_id = "assigned", run.id


def steers_into(entry: InboxEntryRow, run: RunRow, origin: RunRow | None) -> bool:
    """Compatibility is configuration, not identity: headers and principals take no part.

    A steer that gives no options (the defaults) joins whatever options the run started with. Options it gives
    compare as submitted: the run froze its own at acceptance, so it keeps the digest of what its source submitted.
    """
    if entry.kind == "child_result":
        # The origin must be this run or already in its history; a later failure never revives it.
        return origin is not None and (origin.id == run.id or origin.status in {"completed", "waiting"})
    options = RunOptions.model_validate(entry.options)
    return (
        entry.delivery == "steer"
        and entry.agent_id == run.agent_id
        and entry.agent_revision_id in {None, run.agent_revision_id}
        and (options == RunOptions() or options.digest() == run.options_digest)
    )


async def assign_steers(
    session: AsyncSession, thread: ThreadRow, run: RunRow, *, max_count: int, max_bytes: int, scan: int
) -> list[InboxEntryRow]:
    """A bounded FIFO batch of compatible pending entries for the next model request of `run`.

    Incompatible entries stay pending for a later run and take no part in the batch's budget.
    """
    candidates = await pending_entries(session, thread.id, limit=scan)
    origins = await _origins(session, candidates)
    chosen: list[InboxEntryRow] = []
    size = 0
    for entry in candidates:
        if not steers_into(entry, run, origins.get(entry.origin_run_id or "")):
            continue
        if len(chosen) == max_count or size + entry.size > max_bytes:
            break
        assign(entry, run)
        chosen.append(entry)
        size += entry.size
    await session.flush()
    return chosen


async def _origins(session: AsyncSession, entries: Sequence[InboxEntryRow]) -> dict[str, RunRow]:
    ids = {entry.origin_run_id for entry in entries if entry.origin_run_id is not None}
    if not ids:
        return {}
    return {run.id: run for run in await session.scalars(select(RunRow).where(RunRow.id.in_(ids)))}


async def assigned_entries(session: AsyncSession, run_id: str) -> Sequence[InboxEntryRow]:
    return (
        await session.scalars(
            select(InboxEntryRow)
            .where(InboxEntryRow.assigned_run_id == run_id, InboxEntryRow.status == "assigned")
            .order_by(InboxEntryRow.position)
        )
    ).all()


async def consume(
    session: AsyncSession, run_id: str, entry_ids: Sequence[str], *, checkpoint_seq: int, at: datetime
) -> None:
    if entry_ids:
        await session.execute(
            update(InboxEntryRow)
            .where(
                InboxEntryRow.assigned_run_id == run_id,
                InboxEntryRow.status == "assigned",
                InboxEntryRow.id.in_(entry_ids),
            )
            .values(status="consumed", incorporated_checkpoint_seq=checkpoint_seq, finished_at=at)
        )


async def release_assigned(session: AsyncSession, thread: ThreadRow, run: RunRow, *, at: datetime) -> None:
    """Seal disposition of the entries assigned to `run` and not incorporated: completed/waiting return them to
    pending, or withdraw them like the rest of an archived thread's pending input; failure fails them.

    Consumed entries stay consumed either way: they record committed incorporation, not successful work.
    """
    values: dict[str, object]
    if run.status in {"completed", "waiting"}:
        values = (
            {"status": "pending", "assigned_run_id": None}
            if thread.archived_at is None
            else {"status": "withdrawn", "finished_at": at}
        )
    else:
        values = {
            "status": "failed",
            "finished_at": at,
            "failure": Failure(code="run_ended", message=f"The assigned run was {run.status}").model_dump(),
        }
    await session.execute(
        update(InboxEntryRow)
        .where(InboxEntryRow.assigned_run_id == run.id, InboxEntryRow.status == "assigned")
        .values(**values)
    )
