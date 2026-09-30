"""Reading runs, editing their labels and interrupting them."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.infra.http import require_match
from a13n_service.runs import checkpoints
from a13n_service.runs.display import Display
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Attempts, AttemptView, RunItems, RunLabels, RunPage, RunView
from a13n_service.runs.seal import stop
from a13n_service.runs.tables import AttemptRow, InboxEntryRow, RunRow
from a13n_service.runs.threads import get_run, get_thread
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal

# Ancestors per lineage page.
MAX_LINEAGE = 50


async def run_views(session: AsyncSession, runs: Sequence[RunRow]) -> list[RunView]:
    """Views with each run's source input, which the Console shows as the request of that run."""
    sources = [run.source_entry_id for run in runs if run.source_entry_id is not None]
    payloads = (
        dict(
            (
                await session.execute(
                    select(InboxEntryRow.id, InboxEntryRow.payload).where(InboxEntryRow.id.in_(sources))
                )
            )
            .tuples()
            .all()
        )
        if sources
        else {}
    )
    return [
        RunView.model_validate(run).model_copy(update={"input": payloads.get(run.source_entry_id or "")})
        for run in runs
    ]


async def run_view(session: AsyncSession, run: RunRow) -> RunView:
    return (await run_views(session, [run]))[0]


async def get(storage: Storage, actor: Principal, workspace_id: str, run_id: str) -> RunView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return await run_view(session, await get_run(session, scope.workspace_id, run_id))


async def list_thread_runs(
    storage: Storage, actor: Principal, workspace_id: str, thread_id: str, *, limit: int, cursor: str | None
) -> RunPage:
    """Newest first, so the Console can open a thread at its latest runs."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        thread = await get_thread(session, scope.workspace_id, thread_id)
        rows, next_cursor = await cursors.keyset_page(
            session,
            select(RunRow).where(RunRow.thread_id == thread.id),
            (RunRow.created_at, RunRow.id),
            kind="thread_runs",
            owner=thread.id,
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        return RunPage(items=await run_views(session, rows), next_cursor=next_cursor)


async def lineage(storage: Storage, actor: Principal, workspace_id: str, run_id: str, *, cursor: str | None) -> RunPage:
    """The run and its ancestors through `parent_run_id`, across fork origins, nearest first."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        start = cursors.id_position(cursor, "lineage", run_id) or run_id
        chain = [await get_run(session, scope.workspace_id, start)]
        while chain[-1].parent_run_id is not None and len(chain) < MAX_LINEAGE:
            parent = await session.get(RunRow, chain[-1].parent_run_id)
            assert parent is not None
            chain.append(parent)
        older = chain[-1].parent_run_id
        return RunPage(
            items=await run_views(session, chain),
            next_cursor=cursors.encode("lineage", run_id, older) if older is not None else None,
        )


async def items(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str) -> RunItems:
    """The committed display. A sealed run's unfinished items can no longer finish, so they read interrupted."""
    pointer, view = await _read(runtime.storage, actor, workspace_id, run_id)
    try:
        display = await checkpoints.load_display(runtime.objects, pointer)
    except ServiceError as error:
        if error.code != "unavailable":
            raise
        # A checkpoint committed after the pointer was read deletes the object it replaced; the pointer read
        # again names the display that replaced it.
        pointer, view = await _read(runtime.storage, actor, workspace_id, run_id)
        display = await checkpoints.load_display(runtime.objects, pointer)
    display = display or Display.empty(run_id)
    sealed = view.sealed_at is not None
    return RunItems(
        run=view,
        snapshot=display.snapshot.model_copy(update={"continuity": {}}),
        display_revision=pointer.digest if pointer is not None else None,
        position=str(display.position) if pointer is not None else None,
        resume_after=display.resume_after,
        complete=sealed,
    )


async def _read(
    storage: Storage, actor: Principal, workspace_id: str, run_id: str
) -> tuple[checkpoints.DisplayPointer | None, RunView]:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        run = await get_run(session, scope.workspace_id, run_id)
        pointer = checkpoints.DisplayPointer.model_validate(run.display) if run.display is not None else None
        return pointer, await run_view(session, run)


async def update_labels(
    storage: Storage, actor: Principal, workspace_id: str, run_id: str, body: RunLabels, *, if_match: str | None
) -> RunView:
    """Labels are the one thing a sealed run still lets callers change."""
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        run = await get_run(session, scope.workspace_id, run_id, lock=True)
        require_match(if_match, run.id, run.version)
        run.labels = dict(body.labels)
        await session.flush()
        return await run_view(session, run)


async def interrupt(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str) -> RunView:
    """Accepted runs seal cancelled now; running ones are asked to stop at a safe boundary.

    Repeating is harmless: a cancelled run returns itself; other sealed outcomes conflict.
    """
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        run = await get_run(session, scope.workspace_id, run_id)
        thread = await get_thread(session, scope.workspace_id, run.thread_id, lock=True)
        run = await get_run(session, scope.workspace_id, run_id, lock=True)
        if run.status in {"completed", "waiting", "failed"}:
            raise conflict("run", run.id, f"run_{run.status}")
        await stop(session, runtime, thread, run)
        return await run_view(session, run)


async def list_attempts(storage: Storage, actor: Principal, workspace_id: str, run_id: str) -> Attempts:
    """One unpaged list: at most `max_attempts` charged attempts, plus one per worker drain that handed the run off."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        run = await get_run(session, scope.workspace_id, run_id)
        rows = await session.scalars(select(AttemptRow).where(AttemptRow.run_id == run.id).order_by(AttemptRow.number))
        return Attempts(items=[AttemptView.model_validate(row) for row in rows])
