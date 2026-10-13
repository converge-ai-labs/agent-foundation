"""A bounded control sweep admits ordinary, memory-only runs from durable seal opportunities."""

from datetime import timedelta
from functools import partial

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.sweeps import Sweep
from a13n_service.resources.memories.schemas import MemoryMount
from a13n_service.resources.memories.tables import MemoryFileRevisionRow, MemoryFileStoreRow
from a13n_service.runs.accept import Source, start_run
from a13n_service.runs.inbox import append_message
from a13n_service.runs.memories.mounts import mount_memories
from a13n_service.runs.memories.organization_tables import SessionMemoryRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import (
    Failure,
    Message,
    MessagePayload,
    RunOptions,
    SessionMemorySettings,
    TextPart,
    UsageLimit,
)
from a13n_service.runs.submit import validate_message
from a13n_service.runs.tables import RunRow, SessionRow
from a13n_service.runs.threads import new_thread
from a13n_service.tenancy.access import principal_for, require_active_workspace
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope, authorize

COOLDOWN = timedelta(minutes=5)


async def _settle(session: AsyncSession, owner: SessionRow, policy: SessionMemoryRow) -> bool:
    """Only confirmed completion advances progress. Revisions protect changes made during organization."""
    if policy.active_run_id is None:
        return True
    run = await session.get(RunRow, policy.active_run_id)
    assert run is not None
    if run.sealed_at is None:
        policy.due_at = await now(session) + timedelta(seconds=30)
        return False
    policy.last_run_id, policy.active_run_id = run.id, None
    policy.failure = run.failure
    policy.due_at = await now(session) + COOLDOWN
    if run.status == "completed" and owner.memory_id is not None:
        store = await session.scalar(
            select(MemoryFileStoreRow).where(MemoryFileStoreRow.memory_id == owner.memory_id).with_for_update()
        )
        if store is not None and store.pruned_through_seq <= policy.source_seq:
            external_change = await session.scalar(
                select(MemoryFileRevisionRow.seq)
                .where(
                    MemoryFileRevisionRow.memory_id == owner.memory_id,
                    MemoryFileRevisionRow.seq > policy.source_seq,
                    MemoryFileRevisionRow.run_id.is_distinct_from(run.id),
                )
                .limit(1)
            )
            if external_change is None:
                policy.processed_seq = store.seq
    return False


async def _admit(session: AsyncSession, runtime: Runtime, owner: SessionRow, policy: SessionMemoryRow) -> None:
    assert policy.authority is not None and policy.agent_id is not None and policy.revision_id is not None
    assert owner.memory_id is not None
    scope = WorkspaceScope(owner.organization_id, owner.workspace_id)
    authority = ExecutionAuthority.model_validate(policy.authority)
    actor = await principal_for(session, runtime.access, authority.principal_id, confinement=scope)
    await require_active_workspace(session, scope.workspace_id)
    authorize(actor, scope, "run", authority=authority)
    authorize(actor, scope, "read", authority=authority)
    thread = new_thread(owner, mcp_headers={})
    thread.purpose = "memory_organization"
    message = Message(
        agent_id=policy.agent_id,
        agent_revision_id=policy.revision_id,
        payload=MessagePayload(
            content=(TextPart(type="text", text="Organize the current saved session memory files."),)
        ),
        options=RunOptions(max_usage=UsageLimit(requests=200)),
    )
    revision, overrides = await validate_message(session, runtime, actor, scope, thread, message, authority=authority)
    session.add(thread)
    await session.flush()
    await mount_memories(
        session, thread, [MemoryMount(name="session", memory_id=owner.memory_id, access="write")], limit=1
    )
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
    policy.active_run_id = run.id
    policy.pending = False
    policy.failure = None
    policy.due_at = await now(session) + timedelta(seconds=30)


async def organize(runtime: Runtime) -> None:
    """Each candidate uses its own short transaction; the policy lock serializes settings and admission."""
    due = or_(SessionMemoryRow.due_at.is_(None), SessionMemoryRow.due_at <= func.clock_timestamp())
    async with short_session(runtime.storage) as session:
        candidates = list(
            await session.scalars(
                select(SessionMemoryRow.session_id)
                .where(or_(SessionMemoryRow.pending, SessionMemoryRow.active_run_id.is_not(None)), due)
                .order_by(SessionMemoryRow.due_at.asc().nullsfirst(), SessionMemoryRow.session_id)
                .limit(runtime.settings.control.sweep_batch)
            )
        )
    for session_id in candidates:
        async with transaction(runtime.storage) as session:
            policy = await session.scalar(
                select(SessionMemoryRow)
                .where(SessionMemoryRow.session_id == session_id, due)
                .with_for_update(skip_locked=True)
            )
            if policy is None:
                continue
            owner = await session.get_one(SessionRow, session_id)
            if not await _settle(session, owner, policy):
                continue
            settings = SessionMemorySettings.model_validate(owner.memory)
            if not settings.enabled or not settings.auto_organize or owner.memory_id is None:
                policy.pending = False
                continue
            if not policy.pending:
                continue
            store = await session.scalar(
                select(MemoryFileStoreRow).where(MemoryFileStoreRow.memory_id == owner.memory_id)
            )
            if store is None or store.file_count == 0 or store.seq == policy.processed_seq:
                policy.pending = False
                continue
            policy.source_seq = store.seq
            try:
                async with session.begin_nested():
                    await _admit(session, runtime, owner, policy)
            except ServiceError as error:
                policy.failure = Failure.of(error).model_dump()
                policy.due_at = await now(session) + COOLDOWN


def organization_sweep(runtime: Runtime) -> Sweep:
    return Sweep(name="organize_session_memory", every=10, run=partial(organize, runtime), timeout=30)
