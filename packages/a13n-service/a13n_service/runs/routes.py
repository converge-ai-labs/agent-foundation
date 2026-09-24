"""The runs HTTP surface: sessions, threads, their inbox and stream, and runs."""

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from a13n_service.infra.http import IdempotencyKey, IfMatch, PageLimit, tagged
from a13n_service.runs import archive, entries, resume, runs, sessions, stream, submit, threads, usage
from a13n_service.runs.runtime import CurrentRuntime
from a13n_service.runs.schemas import (
    Attempts,
    EntryPage,
    EntryStatus,
    EntryUpdate,
    EntryView,
    Fork,
    InboxOrder,
    Message,
    NewThread,
    ResumeRequest,
    RunItems,
    RunLabels,
    RunPage,
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
    UsageFilter,
    UsageSummary,
)
from a13n_service.tenancy.requests import Actor, Credential

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["runs"])


def _created(response: Response, result: tuple[Submitted, bool]) -> Submitted:
    """201 when this call created the entry, 200 when it replayed an earlier call with the same key."""
    receipt, created = result
    response.status_code = 201 if created else 200
    tagged(response, receipt.thread)
    return receipt


# Sessions


@router.get("/sessions", response_model=SessionPage)
async def list_sessions(
    runtime: CurrentRuntime,
    workspace_id: str,
    actor: Actor,
    query: Annotated[SessionQuery, Query()],
) -> SessionPage:
    return await sessions.list_sessions(runtime.storage, actor, workspace_id, query)


@router.post("/sessions", response_model=SessionView, status_code=201)
async def create_session(
    runtime: CurrentRuntime, response: Response, workspace_id: str, body: SessionCreate, actor: Actor
) -> SessionView:
    return tagged(response, await sessions.create_session(runtime.storage, actor, workspace_id, body))


@router.get("/sessions/{session_id}", response_model=SessionView)
async def get_session(
    runtime: CurrentRuntime, response: Response, workspace_id: str, session_id: str, actor: Actor
) -> SessionView:
    return tagged(response, await sessions.get_session(runtime.storage, actor, workspace_id, session_id))


@router.patch("/sessions/{session_id}", response_model=SessionView)
async def update_session(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: str,
    session_id: str,
    body: SessionUpdate,
    actor: Actor,
    if_match: IfMatch = None,
) -> SessionView:
    result = await sessions.update_session(runtime.storage, actor, workspace_id, session_id, body, if_match=if_match)
    return tagged(response, result)


# Threads


@router.get("/threads", response_model=ThreadPage)
async def list_threads(
    runtime: CurrentRuntime,
    workspace_id: str,
    actor: Actor,
    session_id: str | None = None,
    label: Annotated[list[str] | None, Query()] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> ThreadPage:
    return await threads.list_threads(
        runtime.storage,
        actor,
        workspace_id,
        session_id=session_id,
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
    runtime: CurrentRuntime, response: Response, workspace_id: str, body: NewThread, actor: Actor, key: IdempotencyKey
) -> Submitted:
    """Create a thread (and its session unless one is named) with its first message."""
    return _created(response, await submit.create_thread(runtime, actor, workspace_id, body, request_key=key))


@router.get("/threads/{thread_id}", response_model=ThreadView)
async def get_thread(
    runtime: CurrentRuntime, response: Response, workspace_id: str, thread_id: str, actor: Actor
) -> ThreadView:
    return tagged(response, await threads.get(runtime.storage, actor, workspace_id, thread_id))


@router.patch("/threads/{thread_id}", response_model=ThreadView)
async def update_thread(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: str,
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
    workspace_id: str,
    thread_id: str,
    actor: Actor,
    if_match: IfMatch = None,
) -> ThreadView:
    result = await archive.archive(runtime, actor, workspace_id, thread_id, if_match=if_match)
    return tagged(response, result)


@router.get("/threads/{thread_id}/runs", response_model=RunPage)
async def list_thread_runs(
    runtime: CurrentRuntime,
    workspace_id: str,
    thread_id: str,
    actor: Actor,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> RunPage:
    """Newest first."""
    return await runs.list_thread_runs(runtime.storage, actor, workspace_id, thread_id, limit=limit, cursor=cursor)


@router.get(
    "/threads/{thread_id}/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def thread_stream(
    request: Request,
    runtime: CurrentRuntime,
    workspace_id: str,
    thread_id: str,
    credential: Credential,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", pattern=stream.EVENT_ID)] = None,
) -> StreamingResponse:
    """Live output of the thread's runs over SSE: `delta` and `boundary` frames with `changed`, `reset`, `gap`."""
    reader = await stream.open_stream(runtime, credential, workspace_id, thread_id)
    return StreamingResponse(
        stream.frames(request.app.state.thread_hub, reader, last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# Inbox


@router.get("/threads/{thread_id}/inbox", response_model=EntryPage)
async def list_inbox(
    runtime: CurrentRuntime,
    workspace_id: str,
    thread_id: str,
    actor: Actor,
    status: Annotated[list[EntryStatus] | None, Query()] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> EntryPage:
    """In inbox order."""
    return await entries.list_entries(
        runtime.storage, actor, workspace_id, thread_id, status=status or [], limit=limit, cursor=cursor
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
    workspace_id: str,
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


@router.get("/threads/{thread_id}/inbox/{entry_id}", response_model=EntryView)
async def get_entry(
    runtime: CurrentRuntime, workspace_id: str, thread_id: str, entry_id: str, actor: Actor
) -> EntryView:
    """One entry and its disposition; edits name the thread's ETag."""
    return await entries.get_entry(runtime.storage, actor, workspace_id, thread_id, entry_id)


@router.patch("/threads/{thread_id}/inbox/{entry_id}", response_model=Submitted)
async def edit_entry(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: str,
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
    workspace_id: str,
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
    workspace_id: str,
    thread_id: str,
    body: InboxOrder,
    actor: Actor,
    if_match: IfMatch = None,
) -> ThreadView:
    result = await entries.reorder(runtime.storage, actor, workspace_id, thread_id, body, if_match=if_match)
    return tagged(response, result)


# Runs


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(runtime: CurrentRuntime, response: Response, workspace_id: str, run_id: str, actor: Actor) -> RunView:
    return tagged(response, await runs.get(runtime.storage, actor, workspace_id, run_id))


@router.patch("/runs/{run_id}", response_model=RunView)
async def update_run(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: str,
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
    runtime: CurrentRuntime, response: Response, workspace_id: str, run_id: str, actor: Actor
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
    workspace_id: str,
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
    workspace_id: str,
    run_id: str,
    body: ResumeRequest,
    actor: Actor,
    key: IdempotencyKey,
) -> RunView:
    """Answer the waiting run's approvals and client tools; the successor run continues from them."""
    successor, created = await resume.resume(runtime, actor, workspace_id, run_id, body, request_key=key)
    response.status_code = 201 if created else 200
    return tagged(response, successor)


@router.get("/runs/{run_id}/items", response_model=RunItems)
async def run_items(runtime: CurrentRuntime, workspace_id: str, run_id: str, actor: Actor) -> RunItems:
    return await runs.items(runtime, actor, workspace_id, run_id)


@router.get("/runs/{run_id}/lineage", response_model=RunPage)
async def run_lineage(
    runtime: CurrentRuntime, workspace_id: str, run_id: str, actor: Actor, cursor: str | None = None
) -> RunPage:
    """The run and its ancestors, nearest first, across fork origins."""
    return await runs.lineage(runtime.storage, actor, workspace_id, run_id, cursor=cursor)


@router.get("/runs/{run_id}/attempts", response_model=Attempts)
async def run_attempts(runtime: CurrentRuntime, workspace_id: str, run_id: str, actor: Actor) -> Attempts:
    return await runs.list_attempts(runtime.storage, actor, workspace_id, run_id)


# Usage


@router.get("/usage", response_model=UsageSummary)
async def summarize_usage(
    runtime: CurrentRuntime, workspace_id: str, actor: Actor, where: Annotated[UsageFilter, Query()]
) -> UsageSummary:
    return await usage.summarize(runtime.storage, actor, workspace_id, where)
