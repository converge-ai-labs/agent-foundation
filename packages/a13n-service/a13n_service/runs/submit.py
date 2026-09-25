"""Idempotent message commands: create a thread, submit to a thread, fork a run.

Each is one short transaction that resolves the request key, appends the entry and tries to accept it from the
source its validation loaded. The unique request index arbitrates concurrent duplicates: the loser rolls back
everything it created, including a tentative thread or session, then replays the winner.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import transaction, violated_constraint
from a13n_service.infra.errors import conflict
from a13n_service.resources.agents.schemas import AgentOverride
from a13n_service.resources.agents.service import SelectedRevision
from a13n_service.resources.assets.service import require_usable
from a13n_service.resources.connections.service import validate_caller_headers
from a13n_service.runs import checkpoints
from a13n_service.runs.accept import Source, accept, primary_template, run_revision
from a13n_service.runs.attachments import asset_fields, require_readable
from a13n_service.runs.environments.mounts import mount_environments, shared_mounts, thread_has_primary
from a13n_service.runs.inbox import Request, append_message, check_replay, find_request
from a13n_service.runs.memories.mounts import mount_memories, shared_memory_mounts
from a13n_service.runs.runs import run_view
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EntryView, Fork, Message, NewThread, Submitted, ThreadView
from a13n_service.runs.sessions import find_session, new_session
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow
from a13n_service.runs.threads import get_run, get_thread, new_thread, refresh_version, require_open
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope, execution_authority

# The request as the resolved workspace names it, so a replay matches however the path spelled the workspace.
type Requested = Callable[[WorkspaceScope], Request]
type Create = Callable[[AsyncSession, WorkspaceScope, Request], Awaitable[tuple[ThreadRow, Source]]]


async def validate_message(
    session: AsyncSession,
    runtime: Runtime,
    principal: Principal,
    scope: WorkspaceScope,
    thread: ThreadRow,
    message: Message,
    *,
    authority: ExecutionAuthority,
) -> tuple[SelectedRevision, AgentOverride | None]:
    """Malformed or unauthorized input fails before anything is appended, and a pending edit before it applies.
    Returns the revision the message selects and its overrides as a run accepted now freezes them.

    Each asset must reach the model of the run the message would start on `thread`, with the primary environment
    the thread mounts or acceptance would reserve. Acceptance checks the assets again against the mounts it
    freezes; an acceptance in a later transaction validates the overrides again too, under the authority current
    then.
    """
    revision, overrides = await run_revision(
        session,
        runtime,
        principal,
        scope,
        message.agent_id,
        message.agent_revision_id,
        message.options.overrides,
        authority=authority,
    )
    await require_usable(session, scope.workspace_id, asset_fields(message.payload))
    primary = await thread_has_primary(session, thread) or primary_template(thread, revision.config) is not None
    await require_readable(
        session, principal, scope, revision, overrides, message.payload, authority=authority, primary=primary
    )
    return revision, overrides


async def _append(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    scope: WorkspaceScope,
    thread: ThreadRow,
    message: Message,
    request: Request,
) -> Source:
    """Validate and append the command's message; the source it returns starts a run if the thread accepts now."""
    authority = execution_authority(actor, scope)
    revision, overrides = await validate_message(session, runtime, actor, scope, thread, message, authority=authority)
    entry = await append_message(
        session,
        thread,
        message,
        principal_id=actor.id,
        authority=authority,
        request=request,
        control=runtime.settings.control,
    )
    return Source.message(entry, "input", actor, revision, overrides)


async def receipt(session: AsyncSession, thread: ThreadRow, entry: InboxEntryRow) -> Submitted:
    """The entry's current disposition and the run that owns it, if any."""
    await refresh_version(session, thread)
    run = await session.get(RunRow, entry.assigned_run_id) if entry.assigned_run_id is not None else None
    return Submitted(
        thread=ThreadView.model_validate(thread),
        entry=EntryView.model_validate(entry),
        run=await run_view(session, run) if run is not None else None,
    )


async def _replay(session: AsyncSession, entry: InboxEntryRow, request: Request) -> Submitted:
    check_replay(entry, request)
    thread = await session.get(ThreadRow, entry.thread_id)
    assert thread is not None
    return await receipt(session, thread, entry)


async def _submit(
    runtime: Runtime, actor: Principal, workspace_id: str, requested: Requested, create: Create
) -> tuple[Submitted, bool]:
    """Returns the receipt and whether this call created it (201) rather than replayed it (200)."""
    try:
        async with transaction(runtime.storage) as session:
            scope, request, found = await _lookup(session, actor, workspace_id, requested)
            if found is not None:
                return await _replay(session, found, request), False
            thread, source = await create(session, scope, request)
            await accept(session, runtime, thread, explicit=source)
            assert source.entry is not None
            return await receipt(session, thread, source.entry), True
    except IntegrityError as error:
        if violated_constraint(error) != "uq_inbox_entries_request":
            raise
    # A concurrent request with the same key committed first; its entry is the evidence.
    async with transaction(runtime.storage) as session:
        _, request, found = await _lookup(session, actor, workspace_id, requested)
        assert found is not None
        return await _replay(session, found, request), False


async def _lookup(
    session: AsyncSession, actor: Principal, workspace_id: str, requested: Requested
) -> tuple[WorkspaceScope, Request, InboxEntryRow | None]:
    """Authentication and current permission precede replay lookup; lookup precedes state validation."""
    scope = await workspace_scope(session, actor, workspace_id, "run")
    request = requested(scope)
    return scope, request, await find_request(session, scope.workspace_id, actor.id, request.key)


async def submit_message(
    runtime: Runtime, actor: Principal, workspace_id: str, thread_id: str, message: Message, *, request_key: str
) -> tuple[Submitted, bool]:
    async def create(session: AsyncSession, scope: WorkspaceScope, request: Request) -> tuple[ThreadRow, Source]:
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_open(thread)
        return thread, await _append(session, runtime, actor, scope, thread, message, request)

    return await _submit(
        runtime, actor, workspace_id, lambda _: Request.of(request_key, "message", thread_id, message), create
    )


async def create_thread(
    runtime: Runtime, actor: Principal, workspace_id: str, body: NewThread, *, request_key: str
) -> tuple[Submitted, bool]:
    async def create(session: AsyncSession, scope: WorkspaceScope, request: Request) -> tuple[ThreadRow, Source]:
        await validate_caller_headers(session, scope.workspace_id, body.mcp_headers)
        if body.session_id is not None:
            owner = await find_session(session, scope.workspace_id, body.session_id)
        else:
            owner = new_session(scope.organization_id, scope.workspace_id, actor.id)
            session.add(owner)
        thread = new_thread(owner, mcp_headers=body.mcp_headers)
        session.add(thread)
        await session.flush()
        await mount_environments(session, thread, body.environments, principal_id=actor.id)
        await mount_memories(session, thread, body.memories, limit=runtime.settings.memory.mounts_per_thread)
        return thread, await _append(session, runtime, actor, scope, thread, body, request)

    return await _submit(
        runtime, actor, workspace_id, lambda scope: Request.of(request_key, "thread", scope.workspace_id, body), create
    )


async def fork(
    runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, body: Fork, *, request_key: str
) -> tuple[Submitted, bool]:
    """A new thread in the origin's session whose first run continues the origin's committed history."""

    async def create(session: AsyncSession, scope: WorkspaceScope, request: Request) -> tuple[ThreadRow, Source]:
        origin = await get_run(session, scope.workspace_id, run_id)
        origin_thread = await get_thread(session, scope.workspace_id, origin.thread_id, lock=True)
        # Failed and cancelled runs never became history; fork their parent and resubmit instead.
        if origin.status not in {"completed", "waiting"}:
            raise conflict("run", origin.id, f"run_{origin.status}")
        checkpoints.require_compatible(origin)
        owner = await session.get(SessionRow, origin.session_id)
        assert owner is not None
        thread = new_thread(
            owner,
            mcp_headers=origin_thread.mcp_headers,
            origin="fork",
            origin_thread_id=origin_thread.id,
            origin_run_id=origin.id,
        )
        session.add(thread)
        await session.flush()
        shared = [] if body.fresh_environments else await shared_mounts(session, origin_thread)
        await mount_environments(session, thread, [*shared, *body.environments], principal_id=actor.id)
        memories = [*await shared_memory_mounts(session, origin_thread), *body.memories]
        await mount_memories(session, thread, memories, limit=runtime.settings.memory.mounts_per_thread)
        return thread, await _append(session, runtime, actor, scope, thread, body, request)

    return await _submit(runtime, actor, workspace_id, lambda _: Request.of(request_key, "fork", run_id, body), create)
