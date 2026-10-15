"""Reading runs, editing their labels and interrupting them."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.infra.http import require_match
from a13n_service.resources.agents.schemas import AgentRevision, apply_override
from a13n_service.resources.agents.tables import AgentRevisionRow
from a13n_service.runs import checkpoints
from a13n_service.runs.checkpoints import PageRef, TailPointer
from a13n_service.runs.contents import MAX_IMAGE_BYTES, ContentObject, decode
from a13n_service.runs.display import Page, Tail
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import (
    Attempts,
    AttemptView,
    RunAgent,
    RunContent,
    RunItems,
    RunLabels,
    RunOptions,
    RunPage,
    RunQuery,
    RunView,
)
from a13n_service.runs.seal import stop
from a13n_service.runs.tables import AttemptRow, InboxEntryRow, RunItemPageRow, RunRow
from a13n_service.runs.threads import get_run, get_thread
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal

# Ancestors per lineage page.
MAX_LINEAGE = 50
# The items a default read returns: the newest, and always the whole tail.
NEWEST_ITEMS = 200


def newest(pointer: TailPointer | None, limit: int) -> int:
    """The first ordinal of a run's newest `limit` items, or of its tail when that holds more."""
    tail, count = (pointer.first, pointer.count) if pointer is not None else (1, 0)
    return max(1, min(tail, count + 1 - limit))


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


async def agent(storage: Storage, actor: Principal, workspace_id: str, run_id: str) -> RunAgent:
    """The revision the run froze at acceptance and the configuration it executes with the run's override."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        run = await get_run(session, scope.workspace_id, run_id)
        # The run's foreign key keeps its revision, and revisions are never deleted.
        revision = AgentRevision.model_validate(await session.get(AgentRevisionRow, run.agent_revision_id))
    override = RunOptions.model_validate(run.options).overrides
    config = revision.config if override is None else apply_override(revision.config, override)
    return RunAgent(revision=revision, config=config)


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


async def list_runs(storage: Storage, actor: Principal, workspace_id: str, filters: RunQuery) -> RunPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = select(RunRow).where(RunRow.workspace_id == scope.workspace_id)
        for name in ("agent_id", "session_id", "thread_id", "principal_id", "status"):
            if (value := getattr(filters, name)) is not None:
                query = query.where(getattr(RunRow, name) == value)
        if filters.created_after is not None:
            query = query.where(RunRow.created_at >= filters.created_after)
        if filters.created_before is not None:
            query = query.where(RunRow.created_at < filters.created_before)
        rows, cursor = await cursors.keyset_page(
            session,
            query,
            (RunRow.created_at, RunRow.id),
            kind="runs",
            owner=cursors.query_owner(scope.workspace_id, filters.model_dump(mode="json", exclude={"limit", "cursor"})),
            cursor=filters.cursor,
            limit=filters.limit,
            newest_first=True,
        )
        return RunPage(items=await run_views(session, rows), next_cursor=cursor)


async def get_attempt(
    storage: Storage, actor: Principal, workspace_id: str, run_id: str, attempt_id: str
) -> AttemptView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        run = await get_run(session, scope.workspace_id, run_id)
        attempt = await session.get(AttemptRow, attempt_id)
        if attempt is None or attempt.run_id != run.id:
            raise not_found("attempt", attempt_id)
        return AttemptView.model_validate(attempt)


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


async def items(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    run_id: str,
    *,
    before: int | None = None,
    after: int | None = None,
    limit: int,
) -> RunItems:
    """Committed display items in ordinal order: at most `limit` just before ordinal `before` or just after ordinal
    `after`, or by default the newest `limit` and always the whole tail, since only its items can still change. A
    sealed run's unfinished items can no longer finish, so they read interrupted."""
    if before is not None and after is not None:
        raise invalid("before", "before and after exclude each other")
    window = await _read(runtime.storage, actor, workspace_id, run_id, before=before, after=after, limit=limit)
    try:
        loaded = await _load(runtime, window)
    except ServiceError as error:
        if error.code != "unavailable":
            raise
        # A checkpoint committed after the pointer was read deletes the tail it replaced; the pointer read again
        # names the tail that replaced it, and pages are never deleted.
        window = await _read(runtime.storage, actor, workspace_id, run_id, before=before, after=after, limit=limit)
        loaded = await _load(runtime, window)
    tail, pages = loaded
    sealed = window.run.sealed_at is not None
    return RunItems(
        run=window.run,
        items=[
            item.model_copy(update={"state": "interrupted"}) if sealed and item.state == "in_progress" else item
            for item in (*(item for page in pages for item in page.items), *tail.items)
            if window.first <= item.ordinal <= window.last
        ],
        complete=sealed,
    )


@dataclass(frozen=True, slots=True)
class _Window:
    """What one items read returns as its short session found it: the run, its tail pointer, the first and last
    ordinals it returns and the pages that hold those before the tail."""

    run: RunView
    tail: TailPointer | None
    first: int
    last: int
    pages: list[PageRef]


async def _read(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    run_id: str,
    *,
    before: int | None,
    after: int | None,
    limit: int,
) -> _Window:
    """Read the window in one short session. Pages committed after the pointer start at or after its tail, so none
    is selected."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        run = await get_run(session, scope.workspace_id, run_id)
        pointer = TailPointer.model_validate(run.tail) if run.tail is not None else None
        tail, count = (pointer.first, pointer.count) if pointer is not None else (1, 0)
        if before is not None:
            last = min(before - 1, count)
            first = max(1, last - limit + 1)
        elif after is not None:
            first, last = after + 1, min(count, after + limit)
        else:
            first, last = newest(pointer, limit), count
        rows = await session.scalars(
            select(RunItemPageRow)
            .where(
                RunItemPageRow.run_id == run.id,
                RunItemPageRow.first_ordinal <= min(last, tail - 1),
                RunItemPageRow.last_ordinal >= first,
            )
            .order_by(RunItemPageRow.first_ordinal)
        )
        pages = [
            PageRef(key=row.key, digest=row.digest, size=row.size, first=row.first_ordinal, last=row.last_ordinal)
            for row in rows
        ]
        return _Window(await run_view(session, run), pointer, first, last, pages)


async def _load(runtime: Runtime, window: _Window) -> tuple[Tail, list[Page]]:
    return await asyncio.gather(
        checkpoints.load_tail(runtime.objects, window.tail),
        asyncio.gather(*(checkpoints.load_page(runtime.objects, page) for page in window.pages)),
    )


async def _content_ref(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    run_id: str,
    content_id: str,
    *,
    purpose: Literal["value", "image"],
) -> ContentObject:
    """Read only values reachable from this run's committed tail or immutable pages; no object I/O in the session."""
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        run = await get_run(session, scope.workspace_id, run_id)
        refs = TailPointer.model_validate(run.tail).refs if run.tail is not None else ()
        ref = next((value for value in refs if value.id == content_id and value.purpose == purpose), None)
        if ref is None:
            page_refs = await session.scalar(
                select(RunItemPageRow.refs)
                .where(
                    RunItemPageRow.run_id == run.id,
                    RunItemPageRow.refs.contains([{"id": content_id, "purpose": purpose}]),
                )
                .limit(1)
            )
            ref = next(
                (ContentObject.model_validate(value) for value in page_refs or [] if value["id"] == content_id), None
            )
        if ref is None:
            raise not_found("run_content", content_id)
    return ref


async def image(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, image_id: str) -> tuple[bytes, str]:
    ref = await _content_ref(runtime, actor, workspace_id, run_id, image_id, purpose="image")
    if ref.media_type not in {"image/png", "image/jpeg"} or ref.size_bytes > MAX_IMAGE_BYTES:
        raise not_found("run_image", image_id)
    value = await checkpoints.load(runtime.objects, ref)
    if len(value) != ref.size_bytes:
        raise ServiceError("unavailable", "Image length does not match its reference", {"dependency": "objects"})
    return value, ref.media_type


async def content(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, content_id: str) -> RunContent:
    ref = await _content_ref(runtime, actor, workspace_id, run_id, content_id, purpose="value")
    assert ref.media_type == "text/plain" or ref.media_type == "application/json"
    value = decode(await checkpoints.load(runtime.objects, ref), ref)
    return RunContent(id=ref.id, media_type=ref.media_type, value=value, truncated=ref.truncated)


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
