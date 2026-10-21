"""The runs HTTP surface: sessions and their stream, threads, their inbox and stream, and runs."""

from typing import Annotated, Literal

from a13n_stream_protocol.display import ItemKind, ItemState
from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from a13n_service.infra import reads
from a13n_service.infra.errors import invalid
from a13n_service.infra.http import IdempotencyKey, IfMatch, PageLimit, tagged
from a13n_service.infra.platform import platform_operation
from a13n_service.resources.agents import read_views as agent_reads
from a13n_service.runs import (
    archive,
    authors,
    entries,
    item_reads,
    read_views,
    resume,
    runs,
    sessions,
    stream,
    submit,
    threads,
)
from a13n_service.runs import thread_stream as thread_streams
from a13n_service.runs.display import StreamPosition
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.runs.schemas import (
    Attempts,
    AttemptView,
    EntryPage,
    EntryStatus,
    EntryUpdate,
    EntryView,
    Fork,
    InboxOrder,
    Message,
    MessageAuthorQuery,
    MessageAuthors,
    NewThread,
    Resume,
    RunAgent,
    RunContent,
    RunItems,
    RunLabels,
    RunPage,
    RunQuery,
    RunView,
    SessionCreate,
    SessionPage,
    SessionQuery,
    SessionUpdate,
    SessionView,
    Submitted,
    ThreadPage,
    ThreadUpdate,
    ThreadView,
)
from a13n_service.tenancy.requests import Actor, Credential, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["runs"])


def _created(response: Response, result: tuple[Submitted, bool]) -> Submitted:
    """201 when this call created the entry, 200 when it replayed an earlier call with the same key."""
    receipt, created = result
    response.status_code = 201 if created else 200
    tagged(response, receipt.thread)
    return receipt


# Sessions


@router.get("/sessions", response_model=SessionPage, openapi_extra=platform_operation("execution", "read"))
async def list_sessions(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    actor: Actor,
    query: Annotated[SessionQuery, Query()],
) -> SessionPage:
    return await sessions.list_sessions(runtime.storage, actor, workspace_id, query)


@router.post("/sessions", response_model=SessionView, status_code=201)
async def create_session(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, body: SessionCreate, actor: Actor
) -> SessionView:
    return tagged(response, await sessions.create_session(runtime.storage, actor, workspace_id, body))


@router.get("/sessions/{session_id}", response_model=SessionView, openapi_extra=platform_operation("execution", "read"))
async def get_session(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, session_id: str, actor: Actor
) -> SessionView:
    return tagged(response, await sessions.get_session(runtime.storage, actor, workspace_id, session_id))


@router.patch("/sessions/{session_id}", response_model=SessionView)
async def update_session(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    session_id: str,
    body: SessionUpdate,
    actor: Actor,
    if_match: IfMatch = None,
) -> SessionView:
    result = await sessions.update_session(runtime.storage, actor, workspace_id, session_id, body, if_match=if_match)
    return tagged(response, result)


@router.get(
    "/sessions/{session_id}/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def session_stream(
    request: Request,
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    session_id: str,
    credential: Credential,
) -> StreamingResponse:
    """The session's threads and the live output of their runs over SSE: `thread`, `items`, `delta` and `gap`
    frames, plus `schedule` and `occurrence` state for Schedule-owned sessions. Every connection starts from a snapshot."""
    reader = await stream.open_stream(runtime, credential, workspace_id, session_id)
    return StreamingResponse(
        stream.frames(request.app.state.session_hub, reader),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/sessions/{session_id}/message-authors",
    response_model=MessageAuthors,
    openapi_extra=platform_operation("execution", "read"),
)
async def get_message_authors(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    session_id: str,
    actor: Actor,
    query: Annotated[MessageAuthorQuery, Query()],
) -> MessageAuthors:
    """Get message senders' current display names, emails, account kinds, status and avatar URLs.

    Supply the session ID and 1 to 100 entry IDs from the thread inbox. Requires workspace read permission;
    only message entries in that session are returned. This is not an arbitrary user or member-directory lookup.
    Use returned names to identify senders in answers; retain IDs when needed for precise references.
    """
    return await authors.message_authors(runtime.storage, actor, workspace_id, session_id, query)


# Threads


@router.get("/threads", response_model=ThreadPage, openapi_extra=platform_operation("execution", "read"))
async def list_threads(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    actor: Actor,
    session_id: str | None = None,
    occurrence_id: str | None = None,
    text_query: Annotated[str | None, Query(max_length=1024)] = None,
    label: Annotated[list[str] | None, Query()] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> ThreadPage:
    return await threads.list_threads(
        runtime.storage,
        actor,
        workspace_id,
        session_id=session_id,
        occurrence_id=occurrence_id,
        text_query=text_query,
        label=label or [],
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/threads",
    response_model=Submitted,
    status_code=201,
    responses={200: {"model": Submitted, "description": "The replayed submission in its current state"}},
)
async def create_thread(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    body: NewThread,
    actor: Actor,
    key: IdempotencyKey,
) -> Submitted:
    """Create a thread (and its session unless one is named) with its first message."""
    return _created(response, await submit.create_thread(runtime, actor, workspace_id, body, request_key=key))


@router.get("/threads/{thread_id}", response_model=ThreadView, openapi_extra=platform_operation("execution", "read"))
async def get_thread(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, thread_id: str, actor: Actor
) -> ThreadView:
    return tagged(response, await threads.get(runtime.storage, actor, workspace_id, thread_id))


@router.patch("/threads/{thread_id}", response_model=ThreadView)
async def update_thread(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    thread_id: str,
    body: ThreadUpdate,
    actor: Actor,
    if_match: IfMatch = None,
) -> ThreadView:
    result = await threads.update(runtime.storage, actor, workspace_id, thread_id, body, if_match=if_match)
    return tagged(response, result)


@router.post("/threads/{thread_id}/archive", response_model=ThreadView)
async def archive_thread(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    thread_id: str,
    actor: Actor,
    if_match: IfMatch = None,
) -> ThreadView:
    result = await archive.archive(runtime, actor, workspace_id, thread_id, if_match=if_match)
    return tagged(response, result)


@router.get(
    "/threads/{thread_id}/runs",
    response_model=RunPage | read_views.RunSummaryPage,
    openapi_extra=platform_operation("execution", "read"),
)
async def list_thread_runs(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    thread_id: str,
    actor: Actor,
    view: reads.View = "detailed",
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: str | None = None,
) -> RunPage | read_views.RunSummaryPage:
    """Newest first."""
    return await runs.list_thread_runs(
        runtime.storage, actor, workspace_id, thread_id, limit=reads.page_limit(view, limit), cursor=cursor, view=view
    )


@router.get(
    "/threads/{thread_id}/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def thread_stream(
    request: Request,
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    thread_id: str,
    credential: Credential,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", pattern=thread_streams.EVENT_ID)] = None,
    run: str | None = None,
    position: Annotated[str | None, Query(pattern=thread_streams.POSITION)] = None,
) -> StreamingResponse:
    """Live output of one thread's runs over SSE: `delta` and `boundary` frames with `changed`, `reset`, `gap`.
    Resumes from `Last-Event-ID` or a run's `position`; the session stream suits clients that do not track them."""
    if (run is None) != (position is None):
        raise invalid("position", "run and position must be provided together")
    resume = None
    if run is not None and position is not None:
        attempt, sequence = map(int, position.split("-"))
        resume = thread_streams.Resume(run, StreamPosition(attempt=attempt, sequence=sequence))
    reader = await thread_streams.open_stream(runtime, credential, workspace_id, thread_id, resume)
    return StreamingResponse(
        thread_streams.frames(request.app.state.thread_hub, reader, last_event_id, resume),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# Inbox


@router.get(
    "/threads/{thread_id}/inbox",
    response_model=EntryPage | read_views.EntrySummaryPage,
    openapi_extra=platform_operation("execution", "read"),
)
async def list_inbox(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    thread_id: str,
    actor: Actor,
    status: Annotated[list[EntryStatus] | None, Query()] = None,
    view: reads.View = "detailed",
    kind: Literal["message", "child_result"] | None = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: str | None = None,
) -> EntryPage | read_views.EntrySummaryPage:
    """In inbox order."""
    return await entries.list_entries(
        runtime.storage,
        actor,
        workspace_id,
        thread_id,
        status=status or [],
        limit=reads.page_limit(view, limit),
        cursor=cursor,
        view=view,
        kind=kind,
    )


@router.post(
    "/threads/{thread_id}/inbox",
    response_model=Submitted,
    status_code=201,
    responses={200: {"model": Submitted, "description": "The replayed submission in its current state"}},
)
async def submit_message(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    thread_id: str,
    body: Message,
    actor: Actor,
    key: IdempotencyKey,
) -> Submitted:
    """Append a message; it starts a run at once when the thread can accept it, or steers the active run."""
    return _created(
        response,
        await submit.submit_message(runtime, actor, workspace_id, thread_id, body, request_key=key),
    )


@router.get(
    "/threads/{thread_id}/inbox/{entry_id}",
    response_model=EntryView | read_views.EntrySummary | reads.TextRead,
    openapi_extra=platform_operation("execution", "read"),
)
async def get_inbox_entry(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    thread_id: str,
    entry_id: str,
    actor: Actor,
    view: reads.View = "detailed",
    section: Literal["payload", "options", "failure"] | None = None,
    offset: reads.Offset = 0,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> EntryView | read_views.EntrySummary | reads.TextRead:
    """One entry and its disposition; edits name the thread's ETag."""
    value = await entries.get_entry(runtime.storage, actor, workspace_id, thread_id, entry_id)
    if section is not None:
        return reads.read_value(
            getattr(value, section),
            source=f"inbox/{entry_id}/{section}",
            offset=offset,
            limit=limit,
            version=version,
            query=query,
        )
    if offset or version or query:
        raise invalid("section", "Select a section for fragment or search parameters")
    return read_views.entry_summary(value) if view == "compact" else value


@router.patch("/threads/{thread_id}/inbox/{entry_id}", response_model=Submitted)
async def edit_entry(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    thread_id: str,
    entry_id: str,
    body: EntryUpdate,
    actor: Actor,
    if_match: IfMatch = None,
) -> Submitted:
    result = await entries.edit(runtime, actor, workspace_id, thread_id, entry_id, body, if_match=if_match)
    tagged(response, result.thread)
    return result


@router.delete("/threads/{thread_id}/inbox/{entry_id}", response_model=Submitted)
async def withdraw_entry(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    thread_id: str,
    entry_id: str,
    actor: Actor,
    if_match: IfMatch = None,
) -> Submitted:
    """Withdraw a pending entry; its tombstone keeps the request key."""
    result = await entries.withdraw(runtime.storage, actor, workspace_id, thread_id, entry_id, if_match=if_match)
    tagged(response, result.thread)
    return result


@router.put("/threads/{thread_id}/inbox/order", response_model=ThreadView)
async def reorder_inbox(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    thread_id: str,
    body: InboxOrder,
    actor: Actor,
    if_match: IfMatch = None,
) -> ThreadView:
    result = await entries.reorder(runtime.storage, actor, workspace_id, thread_id, body, if_match=if_match)
    return tagged(response, result)


# Runs


@router.get(
    "/runs", response_model=RunPage | read_views.RunSummaryPage, openapi_extra=platform_operation("execution", "read")
)
async def list_runs(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor, query: Annotated[RunQuery, Query()]
) -> RunPage | read_views.RunSummaryPage:
    return await runs.list_runs(runtime.storage, actor, workspace_id, query)


@router.get(
    "/runs/{run_id}",
    response_model=RunView | read_views.RunSummary | reads.TextRead,
    openapi_extra=platform_operation("execution", "read"),
)
async def get_run(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    run_id: str,
    actor: Actor,
    view: reads.View = "detailed",
    section: Literal[
        "input",
        "output",
        "failure",
        "resume",
        "pending",
        "options",
        "environment_mounts",
        "memory_mounts",
        "usage_at_seal",
        "labels",
    ]
    | None = None,
    offset: reads.Offset = 0,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> RunView | read_views.RunSummary | reads.TextRead:
    value = tagged(response, await runs.get(runtime.storage, actor, workspace_id, run_id))
    if section is not None:
        return reads.read_value(
            getattr(value, section),
            source=f"runs/{run_id}/{section}",
            offset=offset,
            limit=limit,
            version=version,
            query=query,
        )
    if offset or version or query:
        raise invalid("section", "Select a section for fragment or search parameters")
    return await runs.compact_view(runtime.storage, value) if view == "compact" else value


@router.patch("/runs/{run_id}", response_model=RunView)
async def update_run(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    run_id: str,
    body: RunLabels,
    actor: Actor,
    if_match: IfMatch = None,
) -> RunView:
    """Labels only."""
    result = await runs.update_labels(runtime.storage, actor, workspace_id, run_id, body, if_match=if_match)
    return tagged(response, result)


@router.post("/runs/{run_id}/interrupt", response_model=RunView)
async def interrupt_run(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, run_id: str, actor: Actor
) -> RunView:
    return tagged(response, await runs.interrupt(runtime, actor, workspace_id, run_id))


@router.post(
    "/runs/{run_id}/fork",
    response_model=Submitted,
    status_code=201,
    responses={200: {"model": Submitted, "description": "The replayed submission in its current state"}},
)
async def fork_run(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    run_id: str,
    body: Fork,
    actor: Actor,
    key: IdempotencyKey,
) -> Submitted:
    """A new thread in the run's session that continues from this run's committed history."""
    return _created(response, await submit.fork(runtime, actor, workspace_id, run_id, body, request_key=key))


@router.post(
    "/runs/{run_id}/resume",
    response_model=RunView,
    status_code=201,
    responses={200: {"model": RunView, "description": "The existing successor run in its current state"}},
)
async def resume_run(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    run_id: str,
    body: Resume,
    actor: Actor,
    key: IdempotencyKey,
) -> RunView:
    """Answer the exact waiting run's approvals, client tools and user questions; the successor continues from them."""
    successor, created = await resume.resume(runtime, actor, workspace_id, run_id, body, request_key=key)
    response.status_code = 201 if created else 200
    return tagged(response, successor)


@router.get(
    "/runs/{run_id}/items",
    response_model=RunItems | item_reads.ItemPage,
    openapi_extra=platform_operation("execution", "read"),
)
async def list_run_items(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    run_id: str,
    actor: Actor,
    before: Annotated[int | None, Query(ge=1, description="Return the items just before this ordinal")] = None,
    after: Annotated[int | None, Query(ge=0, description="Return the items just after this ordinal")] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    view: reads.View = "detailed",
    kind: ItemKind | None = None,
    state: ItemState | None = None,
    tool_name: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
) -> RunItems | item_reads.ItemPage:
    """A run's committed display items by ordinal. Detailed unfiltered reads default to the newest items and full tail.
    Compact or filtered reads start at the oldest ordinal and strictly honor limit. Use exclusive before/after
    positions, then follow next_before/next_after even when a filtered page is empty."""
    if view == "compact" or any((kind, state, tool_name)):
        return await item_reads.list_items(
            runtime,
            actor,
            workspace_id,
            run_id,
            view=view,
            before=before,
            after=after,
            limit=reads.page_limit(view, limit, runs.NEWEST_ITEMS),
            kind=kind,
            state=state,
            tool_name=tool_name,
        )
    return await runs.items(
        runtime,
        actor,
        workspace_id,
        run_id,
        before=before,
        after=after,
        limit=reads.page_limit(view, limit, runs.NEWEST_ITEMS),
    )


@router.get(
    "/runs/{run_id}/agent",
    response_model=RunAgent | agent_reads.RunAgentSummary | reads.TextRead,
    openapi_extra=platform_operation("execution", "read"),
)
async def get_run_agent(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    run_id: str,
    actor: Actor,
    view: reads.View = "detailed",
    section: Literal["original", "effective", "overrides"] | None = None,
    part: agent_reads.ConfigSection = "all",
    offset: reads.Offset = 0,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> RunAgent | agent_reads.RunAgentSummary | reads.TextRead:
    """The frozen revision and effective configuration. Section reads are versioned fragments, never writable configs."""
    value = await runs.agent(runtime.storage, actor, workspace_id, run_id)
    if section is not None:
        if section == "overrides":
            if part != "all":
                raise invalid("part", "overrides reads use part=all")
            run = await runs.get(runtime.storage, actor, workspace_id, run_id)
            selected = run.options.overrides
        else:
            selected = agent_reads.config_section(
                value.revision.config if section == "original" else value.config, part
            )
        return reads.read_value(
            selected,
            source=f"runs/{run_id}/agent/{section}/{part}",
            offset=offset,
            limit=limit,
            version=version,
            query=query,
        )
    if part != "all" or offset or version or query:
        raise invalid("section", "Select original, effective or overrides for a partial read")
    return agent_reads.run_agent_summary(value.revision, value.config) if view == "compact" else value


@router.get(
    "/runs/{run_id}/lineage",
    response_model=RunPage | read_views.RunSummaryPage,
    openapi_extra=platform_operation("execution", "read"),
)
async def get_run_lineage(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    run_id: str,
    actor: Actor,
    cursor: str | None = None,
    view: reads.View = "detailed",
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
) -> RunPage | read_views.RunSummaryPage:
    """The run and its ancestors, nearest first, across fork origins."""
    return await runs.lineage(
        runtime.storage, actor, workspace_id, run_id, cursor=cursor, view=view, limit=reads.page_limit(view, limit)
    )


@router.get(
    "/runs/{run_id}/contents/{content_id}",
    response_model=RunContent | read_views.ContentSummary | reads.TextRead,
    openapi_extra=platform_operation("execution", "read"),
)
async def get_run_content(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    run_id: str,
    content_id: str,
    actor: Actor,
    response: Response,
    view: reads.View = "detailed",
    offset: Annotated[int | None, Query(ge=0)] = None,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> RunContent | read_views.ContentSummary | reads.TextRead:
    """The complete value behind a committed display reference."""
    response.headers["Cache-Control"] = "no-store"
    value = await runs.content(runtime, actor, workspace_id, run_id, content_id)
    if offset is not None or query is not None:
        return reads.read_value(
            value.value,
            source=f"runs/{run_id}/contents/{content_id}",
            offset=offset or 0,
            limit=limit,
            version=version,
            query=query,
            source_truncated=value.truncated,
        )
    if version is not None:
        raise invalid("offset", "Provide offset=0 for a fragment")
    return read_views.content_summary(value) if view == "compact" else value


@router.get("/runs/{run_id}/attempts", response_model=Attempts, openapi_extra=platform_operation("execution", "read"))
async def list_run_attempts(runtime: CurrentRuntime, workspace_id: WorkspaceId, run_id: str, actor: Actor) -> Attempts:
    return await runs.list_attempts(runtime.storage, actor, workspace_id, run_id)


@router.get(
    "/runs/{run_id}/images/{image_id}",
    response_class=Response,
    responses={
        200: {
            "content": {
                "image/png": {"schema": {"type": "string", "format": "binary"}},
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
            }
        }
    },
)
async def run_image(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, run_id: str, image_id: str, actor: Actor
) -> Response:
    """Read a retained screenshot from committed Run history. Binary media is not an MCP query endpoint."""
    data, media_type = await runs.image(runtime, actor, workspace_id, run_id, image_id)
    return Response(
        data, media_type=media_type, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    )


@router.get(
    "/runs/{run_id}/attempts/{attempt_id}",
    response_model=AttemptView,
    openapi_extra=platform_operation("execution", "read"),
)
async def get_run_attempt(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor, run_id: str, attempt_id: str
) -> AttemptView:
    return await runs.get_attempt(runtime.storage, actor, workspace_id, run_id, attempt_id)


@router.get(
    "/runs/{run_id}/items/{item_id}/content",
    response_model=reads.TextRead,
    openapi_extra=platform_operation("execution", "read"),
)
async def read_run_item_content(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    run_id: str,
    item_id: str,
    actor: Actor,
    ordinal: Annotated[int, Query(ge=1, description="Ordinal returned with this item ID; both must match")],
    field: Literal["all", "text", "arguments", "result", "value", "result_parts", "metadata", "failure"] = "all",
    offset: reads.Offset = 0,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> reads.TextRead:
    """Read one display field or follow its retained content reference, under the run's existing authorization."""
    return await item_reads.content(
        runtime,
        actor,
        workspace_id,
        run_id,
        item_id,
        ordinal=ordinal,
        field=field,
        offset=offset,
        limit=limit,
        version=version,
        query=query,
    )
