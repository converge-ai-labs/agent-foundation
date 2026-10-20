"""Detached Session stream state from one short PostgreSQL snapshot."""

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, defer

from a13n_service.infra.errors import ServiceError
from a13n_service.runs.display import StreamPosition
from a13n_service.runs.runs import run_views
from a13n_service.runs.schemas import EntryView, RunView, ThreadView
from a13n_service.runs.tables import AttemptRow, InboxEntryRow, RunRow, ThreadRow
from a13n_service.tenancy.access import Access, Authenticated, reauthenticate, workspace_scope


@dataclass(frozen=True, slots=True)
class Scope:
    workspace_id: str
    session_id: str


class ThreadState(BaseModel):
    """A thread's current state: the thread, the runs its pointers name and its open inbox entries."""

    thread: ThreadView
    current_run: RunView | None
    last_run: RunView | None
    # Pending and assigned entries, in inbox order.
    entries: list[EntryView]


@dataclass(frozen=True, slots=True)
class Progress:
    """A current run as a refresh found it: its latest attempt and the position and first ordinal of its committed
    display."""

    run_id: str
    attempt: int
    position: StreamPosition
    first: int


@dataclass(frozen=True, slots=True)
class Version:
    thread_id: str
    session_id: str
    # The thread's version and those of the runs its pointers name: a `thread` frame follows any change.
    key: tuple[int, int | None, int | None]
    # Its current run's, None without one.
    progress: Progress | None

    def watch(self, frame: str) -> "Watch":
        return Watch(self.key, self.progress.run_id if self.progress is not None else None, frame)


@dataclass(frozen=True, slots=True)
class Watch:
    """A watched thread: its version key, current run and latest `thread` frame."""

    key: tuple[int, int | None, int | None]
    current_run_id: str | None
    frame: str


async def repeatable(session: AsyncSession) -> None:
    """Read the rest of this session from one snapshot, so a frame's thread, runs and entries agree."""
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))


async def versions(session: AsyncSession, sessions: Sequence[tuple[str, str]]) -> list[Version]:
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
        Version(
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


async def states(session: AsyncSession, thread_ids: Sequence[str]) -> dict[str, ThreadState]:
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


async def may_read(session: AsyncSession, access: Access, credential: Authenticated, workspace_id: str) -> bool:
    """Whether the credential still reads the workspace; an unavailable database decides nothing."""
    try:
        principal = await reauthenticate(session, access, credential)
        await workspace_scope(session, principal, workspace_id, "read")
    except ServiceError as error:
        if error.code == "unavailable":
            raise
        return False
    return True
