"""Shared automatic organization for Spaces and standalone file Memories."""

from datetime import timedelta
from functools import partial

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.infra.sweeps import Sweep
from a13n_service.resources.memories.schemas import ChangeCountTrigger, OrganizationPolicy
from a13n_service.resources.memories.tables import MemoryFileRevisionRow, MemoryFileStoreRow, MemoryRow, MemorySpaceRow
from a13n_service.runs.memories import organization_history, organizations
from a13n_service.runs.memories.organization_schemas import FileMemoryTarget, OrganizationRequest, SpaceTarget
from a13n_service.runs.memories.organization_tables import (
    FileOrganizationRow,
    OrganizationProgressRow,
    OrganizationRow,
    SpaceOrganizationRow,
)
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Failure
from a13n_service.runs.tables import RunRow, SessionRow
from a13n_service.tenancy.access import principal_for, require_active_workspace
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope, authorize

type State = SpaceOrganizationRow | FileOrganizationRow


async def _settle(session: AsyncSession, memory_id: str, policy: OrganizationPolicy, state: State) -> bool:
    if state.active_run_id is None:
        return True
    run = await session.get_one(RunRow, state.active_run_id)
    if run.sealed_at is None:
        state.due_at = await now(session) + timedelta(seconds=30)
        return False
    store = await session.scalar(
        select(MemoryFileStoreRow)
        .where(
            MemoryFileStoreRow.memory_id == memory_id,
        )
        .with_for_update()
    )
    assert store is not None
    external = await session.scalar(
        select(MemoryFileRevisionRow.seq)
        .where(
            MemoryFileRevisionRow.memory_id == memory_id,
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
    has_external = external is not None or store.change_count > state.source_change_count
    state.observed_seq = state.source_seq if has_external else store.seq
    state.observed_change_count = state.source_change_count
    state.last_run_id, state.active_run_id = run.id, None
    state.failure = run.failure
    if run.status == "completed":
        organization = await session.scalar(select(OrganizationRow).where(OrganizationRow.run_id == run.id))
        if organization is not None:
            for source in organization.sources:
                session.add(
                    OrganizationProgressRow(
                        memory_id=memory_id, source_run_id=source["run_id"], organization_id=organization.id
                    )
                )
    state.due_at = await now(session) + timedelta(seconds=policy.cooldown_seconds)
    return False


async def _admit(
    session: AsyncSession,
    runtime: Runtime,
    resource: MemorySpaceRow | MemoryRow,
    memory_id: str,
    policy: OrganizationPolicy,
    state: State,
    authority_data: dict | None,
    *,
    force: bool,
) -> bool:
    if policy.model is None or authority_data is None:
        raise conflict(resource.KIND, resource.id, "organization_not_configured")
    scope = WorkspaceScope(resource.organization_id, resource.workspace_id)
    authority = ExecutionAuthority.model_validate(authority_data)
    actor = await principal_for(session, runtime.access, authority.principal_id, confinement=scope)
    await require_active_workspace(session, scope.workspace_id)
    for verb in ("run", "read", "write"):
        authorize(actor, scope, verb, authority=authority)
    sources = await organization_history.select_sources(session, actor, scope, policy.history, memory_id=memory_id)
    if not force and not sources:
        return False
    owner = await session.get_one(SessionRow, state.session_id) if state.session_id is not None else None
    accepted = await organizations.admit(
        session,
        runtime,
        actor,
        scope,
        OrganizationRequest(
            target=SpaceTarget(id=resource.id)
            if isinstance(resource, MemorySpaceRow)
            else FileMemoryTarget(id=resource.id),
            model=policy.model,
            instructions=policy.instructions or "Organize the saved memory files.",
            history=policy.history,
        ),
        authority=authority,
        owner=owner,
        history_sources=sources,
    )
    state.session_id = accepted.session_id
    state.active_run_id = accepted.run_id
    state.pending = False
    state.failure = None
    state.history_gap = False
    state.due_at = await now(session) + timedelta(seconds=30)
    return True


def _candidates():
    state = SpaceOrganizationRow
    automatic = and_(MemorySpaceRow.enabled, MemorySpaceRow.organization["enabled"].as_boolean())
    changed = or_(
        MemoryFileStoreRow.change_count > func.coalesce(state.observed_change_count, 0),
        and_(
            MemorySpaceRow.version > func.coalesce(state.configuration_version, 0),
            MemoryFileStoreRow.seq > func.coalesce(state.processed_seq, 0),
            MemoryFileStoreRow.change_count > 0,
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
                and_(
                    automatic,
                    or_(
                        and_(changed, MemoryFileStoreRow.file_count > 0),
                        MemorySpaceRow.organization["history"].as_string().is_not(None),
                    ),
                    or_(state.next_check_at.is_(None), state.next_check_at <= func.clock_timestamp()),
                ),
            ),
        )
        .order_by(
            case(
                (or_(state.active_run_id.is_not(None), state.pending), state.due_at),
                else_=func.greatest(state.due_at, state.next_check_at),
            )
            .asc()
            .nullsfirst(),
            MemorySpaceRow.id,
        )
    )


async def _round(
    session: AsyncSession,
    runtime: Runtime,
    resource: MemorySpaceRow | MemoryRow,
    memory_id: str,
    policy: OrganizationPolicy,
    state: State,
    authority_data: dict | None,
    version: int,
    *,
    enabled: bool = True,
) -> None:
    at = await now(session)
    if state.due_at is not None and state.due_at > at:
        return
    if not await _settle(session, memory_id, policy, state) or not enabled:
        return
    if not state.pending:
        if not policy.enabled or (state.next_check_at is not None and state.next_check_at > at):
            return
        state.next_check_at = at + timedelta(seconds=policy.check_interval_seconds)
    store = await session.scalar(
        select(MemoryFileStoreRow).where(MemoryFileStoreRow.memory_id == memory_id).with_for_update()
    )
    assert store is not None
    changed = store.change_count > state.observed_change_count or (
        version > state.configuration_version and store.seq > state.processed_seq and store.change_count > 0
    )
    files_ready = changed and store.file_count > 0
    if isinstance(policy.trigger, ChangeCountTrigger):
        files_ready = files_ready and store.change_count - state.observed_change_count >= policy.trigger.count
    if store.file_count == 0 and policy.history is None:
        state.pending = False
        state.observed_seq = store.seq
        state.configuration_version = version
        return
    if not state.pending and not files_ready and policy.history is None:
        return
    try:
        async with session.begin_nested():
            state.source_seq, state.source_change_count = store.seq, store.change_count
            admitted = await _admit(
                session,
                runtime,
                resource,
                memory_id,
                policy,
                state,
                authority_data,
                force=state.pending or files_ready,
            )
            if admitted:
                state.configuration_version = version
    except ServiceError as error:
        state.failure = Failure.of(error).model_dump()
        state.due_at = at + timedelta(seconds=policy.cooldown_seconds)


async def organize(runtime: Runtime) -> None:
    async with short_session(runtime.storage) as session:
        candidates = list(await session.scalars(_candidates().limit(runtime.settings.control.sweep_batch)))
        files = list(
            await session.scalars(
                select(FileOrganizationRow.memory_id)
                .where(
                    or_(FileOrganizationRow.due_at.is_(None), FileOrganizationRow.due_at <= func.clock_timestamp()),
                    or_(
                        FileOrganizationRow.active_run_id.is_not(None),
                        and_(
                            FileOrganizationRow.policy["enabled"].as_boolean(),
                            or_(
                                FileOrganizationRow.next_check_at.is_(None),
                                FileOrganizationRow.next_check_at <= func.clock_timestamp(),
                            ),
                        ),
                    ),
                )
                .order_by(FileOrganizationRow.next_check_at.asc().nullsfirst(), FileOrganizationRow.memory_id)
                .limit(runtime.settings.control.sweep_batch)
            )
        )
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
            await _round(
                session,
                runtime,
                space,
                space.memory_id,
                OrganizationPolicy.model_validate(space.organization),
                state,
                space.authority,
                space.version,
                enabled=space.enabled,
            )
    for memory_id in files:
        async with transaction(runtime.storage) as session:
            memory = await session.scalar(
                select(MemoryRow).where(MemoryRow.id == memory_id).with_for_update(skip_locked=True)
            )
            if memory is None:
                continue
            state = await session.get(FileOrganizationRow, memory_id)
            if state is not None:
                await _round(
                    session,
                    runtime,
                    memory,
                    memory_id,
                    OrganizationPolicy.model_validate(state.policy),
                    state,
                    state.authority,
                    state.version,
                )


def organization_sweep(runtime: Runtime) -> Sweep:
    return Sweep(name="organize_memory_spaces", every=10, run=partial(organize, runtime), timeout=30)
