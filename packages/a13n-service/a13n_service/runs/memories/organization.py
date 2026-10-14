"""Space-wide organization: discover saved changes and admit ordinary memory-only Runs."""

from datetime import timedelta
from functools import partial

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.infra.sweeps import Sweep
from a13n_service.resources.memories.schemas import OrganizationPolicy
from a13n_service.resources.memories.tables import MemoryFileRevisionRow, MemoryFileStoreRow, MemorySpaceRow
from a13n_service.runs.accept import Source, start_run
from a13n_service.runs.inbox import append_message
from a13n_service.runs.memories import organizer
from a13n_service.runs.memories.organization_tables import SpaceOrganizationRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Failure, Message, MessagePayload, RunOptions, TextPart, UsageLimit
from a13n_service.runs.sessions import new_session
from a13n_service.runs.submit import validate_message
from a13n_service.runs.tables import RunRow, SessionRow
from a13n_service.runs.threads import new_thread
from a13n_service.tenancy.access import principal_for, require_active_workspace
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope, authorize

COOLDOWN = timedelta(minutes=5)


async def _settle(session: AsyncSession, space: MemorySpaceRow, state: SpaceOrganizationRow) -> bool:
    if state.active_run_id is None:
        return True
    run = await session.get_one(RunRow, state.active_run_id)
    if run.sealed_at is None:
        state.due_at = await now(session) + timedelta(seconds=30)
        return False
    store = await session.scalar(
        select(MemoryFileStoreRow)
        .where(
            MemoryFileStoreRow.memory_id == space.memory_id,
        )
        .with_for_update()
    )
    assert store is not None
    external = await session.scalar(
        select(MemoryFileRevisionRow.seq)
        .where(
            MemoryFileRevisionRow.memory_id == space.memory_id,
            MemoryFileRevisionRow.seq > state.source_seq,
            MemoryFileRevisionRow.run_id.is_distinct_from(run.id),
        )
        .limit(1)
    )
    state.history_gap = store.pruned_through_seq > state.source_seq
    if run.status == "completed" and external is None and not state.history_gap:
        state.processed_seq = store.seq
    # Keep external changes eligible under the current automatic policy. Consume our own writes,
    # including a history gap caused by them, so they do not schedule another round.
    state.observed_seq = state.source_seq if external is not None else store.seq
    state.last_run_id, state.active_run_id = run.id, None
    state.failure = run.failure
    state.due_at = await now(session) + COOLDOWN
    return False


async def _admit(session: AsyncSession, runtime: Runtime, space: MemorySpaceRow, state: SpaceOrganizationRow) -> None:
    policy = OrganizationPolicy.model_validate(space.organization)
    if policy.model is None or space.authority is None:
        raise conflict(MemorySpaceRow.KIND, space.id, "organization_not_configured")
    scope = WorkspaceScope(space.organization_id, space.workspace_id)
    authority = ExecutionAuthority.model_validate(space.authority)
    actor = await principal_for(session, runtime.access, authority.principal_id, confinement=scope)
    await require_active_workspace(session, scope.workspace_id)
    authorize(actor, scope, "run", authority=authority)
    authorize(actor, scope, "read", authority=authority)
    agent_id, revision_id = await organizer.revision(session, runtime, actor, scope, policy.model)
    if state.session_id is None:
        owner = new_session(space.organization_id, space.workspace_id, actor.id)
        owner.purpose = "memory_organization"
        session.add(owner)
        await session.flush()
        state.session_id = owner.id
        await session.flush()
    else:
        owner = await session.get_one(SessionRow, state.session_id)
    thread = new_thread(owner, mcp_headers={})
    thread.purpose = "memory_organization"
    message = Message(
        agent_id=agent_id,
        agent_revision_id=revision_id,
        payload=MessagePayload(content=(TextPart(type="text", text="Organize the saved files in this Memory Space."),)),
        options=RunOptions(max_usage=UsageLimit(requests=200)),
    )
    revision, overrides = await validate_message(session, runtime, actor, scope, thread, message, authority=authority)
    session.add(thread)
    await session.flush()
    entry = await append_message(
        session,
        thread,
        message,
        principal_id=actor.id,
        authority=authority,
        request=None,
        control=runtime.settings.control,
    )
    run = await start_run(session, runtime, thread, Source.message(entry, "input", actor, revision, overrides))
    state.active_run_id = run.id
    state.pending = False
    state.failure = None
    state.history_gap = False
    state.due_at = await now(session) + timedelta(seconds=30)


def _candidates():
    state = SpaceOrganizationRow
    automatic = and_(MemorySpaceRow.enabled, MemorySpaceRow.organization["enabled"].as_boolean())
    changed = or_(
        MemoryFileStoreRow.seq > func.coalesce(state.observed_seq, 0),
        and_(
            MemorySpaceRow.version > func.coalesce(state.configuration_version, 0),
            MemoryFileStoreRow.seq > func.coalesce(state.processed_seq, 0),
        ),
    )
    return (
        select(MemorySpaceRow.id)
        .join(MemoryFileStoreRow, MemoryFileStoreRow.memory_id == MemorySpaceRow.memory_id)
        .outerjoin(state, state.space_id == MemorySpaceRow.id)
        .where(
            or_(state.due_at.is_(None), state.due_at <= func.clock_timestamp()),
            or_(
                state.active_run_id.is_not(None),
                and_(MemorySpaceRow.enabled, state.pending),
                and_(automatic, changed, MemoryFileStoreRow.file_count > 0),
            ),
        )
        .order_by(state.due_at.asc().nullsfirst(), MemorySpaceRow.id)
    )


async def organize(runtime: Runtime) -> None:
    async with short_session(runtime.storage) as session:
        candidates = list(await session.scalars(_candidates().limit(runtime.settings.control.sweep_batch)))
    for space_id in candidates:
        async with transaction(runtime.storage) as session:
            space = await session.scalar(
                select(MemorySpaceRow).where(MemorySpaceRow.id == space_id).with_for_update(skip_locked=True)
            )
            if space is None:
                continue
            state = await session.get(SpaceOrganizationRow, space_id)
            if state is None:
                state = SpaceOrganizationRow(space_id=space_id)
                session.add(state)
                await session.flush()
            at = await now(session)
            if state.due_at is not None and state.due_at > at:
                continue
            if not await _settle(session, space, state):
                continue
            if not space.enabled:
                continue
            store = await session.get_one(MemoryFileStoreRow, space.memory_id)
            automatic = OrganizationPolicy.model_validate(space.organization).enabled
            changed = store.seq > state.observed_seq or (
                space.version > state.configuration_version and store.seq > state.processed_seq
            )
            if not state.pending and not (automatic and changed):
                continue
            if store.file_count == 0:
                state.pending = False
                state.observed_seq = store.seq
                state.configuration_version = space.version
                continue
            state.source_seq = store.seq
            try:
                async with session.begin_nested():
                    await _admit(session, runtime, space, state)
                    state.configuration_version = space.version
            except ServiceError as error:
                state.failure = Failure.of(error).model_dump()
                state.due_at = at + COOLDOWN


def organization_sweep(runtime: Runtime) -> Sweep:
    return Sweep(name="organize_memory_spaces", every=10, run=partial(organize, runtime), timeout=30)
