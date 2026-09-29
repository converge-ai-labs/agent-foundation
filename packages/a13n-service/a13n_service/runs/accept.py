"""Turning a thread's eligible source into its next run.

`start_run` is the only function that creates a run; `accept` picks a queued source for it, and resume,
fork and spawn call it with their own source. A source arrives loaded and checked: the principal it runs as,
the revision and the options it runs with. Both are SQL-only and run under the caller's thread lock.
"""

from dataclasses import dataclass
from functools import partial
from typing import Literal

from a13n_logging import exception_details, get_logger
from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import after_commit, now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.telemetry import meter
from a13n_service.resources.agents.schemas import AgentConfig, AgentOverride
from a13n_service.resources.agents.service import SelectedRevision, select_revision, validate_override
from a13n_service.runs import inbox
from a13n_service.runs.admission import AcceptedIntent
from a13n_service.runs.attachments import require_readable
from a13n_service.runs.environments.mounts import freeze_mounts, has_primary, reserve_primary
from a13n_service.runs.memories.mounts import freeze_memories, inherited_cursors
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Failure, MessagePayload, Resume, RunOptions, Trigger
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow
from a13n_service.runs.webhooks import notify_subscribers
from a13n_service.tenancy.access import principal_for, require_active_workspace
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope, authorize

logger = get_logger(__name__)

RUNS_ACCEPTED = meter.create_counter("a13n.runs.accepted", unit="{run}", description="Accepted runs by trigger")

# Entries examined per acceptance; rejected ones are failed, so a later call makes progress.
SCAN = 16


def eligible(thread: ThreadRow, head: RunRow | None) -> bool:
    """Ordinary queued input cannot resolve a wait; only an explicit resume can."""
    return thread.archived_at is None and thread.current_run_id is None and (head is None or head.status != "waiting")


def paused(last: RunRow | None) -> bool:
    """After a failed or cancelled run only an explicit action continues the thread."""
    return last is not None and last.status in {"failed", "cancelled"}


async def run_revision(
    session: AsyncSession,
    runtime: Runtime,
    principal: Principal,
    scope: WorkspaceScope,
    agent_id: str,
    revision_id: str | None,
    overrides: AgentOverride | None,
    *,
    authority: ExecutionAuthority,
) -> tuple[SelectedRevision, AgentOverride | None]:
    """The revision a message selects, and its overrides validated against it as a run freezes them."""
    revision = await select_revision(session, scope.workspace_id, agent_id, revision_id)
    if overrides is not None:
        overrides = await validate_override(
            session,
            principal,
            scope,
            revision,
            overrides,
            authority=authority,
            registry=runtime.registry,
            plugins=runtime.plugins,
        )
    return revision, overrides


async def _authorized(
    session: AsyncSession, runtime: Runtime, scope: WorkspaceScope, principal_id: str, authority: ExecutionAuthority
) -> Principal:
    """The principal with its current grants, which must still allow its frozen authority to run."""
    principal = await principal_for(session, runtime.access, principal_id, confinement=scope)
    authorize(principal, scope, "run", authority=authority)
    return principal


@dataclass(frozen=True, slots=True)
class Source:
    """What a run starts from, a queued entry or the answers resuming a waiting run, with the principal it runs
    as and the revision and options it runs with, loaded and checked by whoever built it."""

    trigger: Trigger
    principal: Principal
    authority: ExecutionAuthority
    revision: SelectedRevision
    revision_selection: Literal["pinned", "default", "inherited"]
    # Frozen: a message's overrides validated against `revision`; inherited options as their run froze them.
    options: RunOptions
    # The digest of the options as submitted, which inherited sources keep from the run they come from.
    options_digest: str
    entry: InboxEntryRow | None = None
    resume: Resume | None = None
    resumed_by_id: str | None = None
    request_key: str | None = None
    request_digest: str | None = None

    @classmethod
    def message(
        cls,
        entry: InboxEntryRow,
        trigger: Trigger,
        principal: Principal,
        revision: SelectedRevision,
        overrides: AgentOverride | None,
    ) -> "Source":
        """A message whose principal the caller authorized and whose revision and overrides it validated."""
        options = RunOptions.model_validate(entry.options)
        return cls(
            trigger=trigger,
            principal=principal,
            authority=ExecutionAuthority.model_validate(entry.authority),
            revision=revision,
            revision_selection="pinned" if entry.agent_revision_id else "default",
            options=options.model_copy(update={"overrides": overrides}),
            options_digest=options.digest(),
            entry=entry,
        )

    @classmethod
    async def load(
        cls, session: AsyncSession, runtime: Runtime, thread: ThreadRow, entry: InboxEntryRow, trigger: Trigger
    ) -> "Source":
        """A message as its principal's current grants and its agent's current revisions accept it now."""
        assert entry.agent_id is not None
        scope = WorkspaceScope(thread.organization_id, thread.workspace_id)
        authority = ExecutionAuthority.model_validate(entry.authority)
        principal = await _authorized(session, runtime, scope, entry.principal_id, authority)
        overrides = RunOptions.model_validate(entry.options).overrides
        revision, overrides = await run_revision(
            session, runtime, principal, scope, entry.agent_id, entry.agent_revision_id, overrides, authority=authority
        )
        return cls.message(entry, trigger, principal, revision, overrides)

    @classmethod
    async def inherited(
        cls,
        session: AsyncSession,
        runtime: Runtime,
        run: RunRow,
        trigger: Trigger,
        *,
        entry: InboxEntryRow | None = None,
        resume: Resume | None = None,
        resumed_by_id: str | None = None,
        request_key: str | None = None,
        request_digest: str | None = None,
    ) -> "Source":
        """Resume and child results continue with the identity, revision and options of an earlier run, while its
        principal's current grants allow them and its agent is not archived."""
        scope = WorkspaceScope(run.organization_id, run.workspace_id)
        authority = ExecutionAuthority.model_validate(run.authority)
        return cls(
            trigger=trigger,
            principal=await _authorized(session, runtime, scope, run.principal_id, authority),
            authority=authority,
            revision=await select_revision(session, run.workspace_id, run.agent_id, run.agent_revision_id),
            revision_selection="inherited",
            options=RunOptions.model_validate(run.options),
            options_digest=run.options_digest,
            entry=entry,
            resume=resume,
            resumed_by_id=resumed_by_id,
            request_key=request_key,
            request_digest=request_digest,
        )


async def _run(session: AsyncSession, run_id: str | None) -> RunRow | None:
    return await session.get(RunRow, run_id) if run_id is not None else None


async def parent_of(
    session: AsyncSession, thread: ThreadRow
) -> tuple[RunRow | None, Literal["root", "continue", "fork"]]:
    """Initial history has one rule: this thread's head, else a fork's origin, else empty.

    A failed run is never a baseline, so a fork keeps its origin until the thread has its own head.
    """
    if thread.head_run_id is not None:
        return await _run(session, thread.head_run_id), "continue"
    if thread.origin == "fork":
        return await _run(session, thread.origin_run_id), "fork"
    return None, "root"


@dataclass(frozen=True, slots=True)
class Delegation:
    """Where a run stands in its delegation chain."""

    # The run the chain started from; children share its allowance, so a budget cannot be escaped by spawning.
    root_run_id: str
    # How many delegations deep the run's thread is; a thread that is not a child is at depth 0.
    depth: int


async def delegation(session: AsyncSession, thread: ThreadRow, run_id: str) -> Delegation:
    """Follow run `run_id` of `thread` up through the runs that spawned its child threads."""
    depth = 0
    while thread.origin == "child" and thread.origin_run_id is not None:
        origin = await session.get(RunRow, thread.origin_run_id)
        parent_thread = await session.get(ThreadRow, origin.thread_id) if origin else None
        if origin is None or parent_thread is None:
            break
        run_id, thread, depth = origin.id, parent_thread, depth + 1
    return Delegation(root_run_id=run_id, depth=depth)


def primary_template(thread: ThreadRow, config: AgentConfig) -> str | None:
    """The template acceptance reserves a primary sandbox from when the thread mounts none: the agent's, except on
    a child thread, whose environments the edge that delegates it decides when it is spawned."""
    return None if thread.origin == "child" else config.default_environment_template_id


async def start_run(session: AsyncSession, runtime: Runtime, thread: ThreadRow, source: Source) -> RunRow:
    """Create the accepted run. The caller holds the thread lock and has checked eligibility.

    Every check that can refuse the source, admission included, runs before any row the caller loaded changes,
    so a refusal rolled back by the caller's savepoint leaves its thread and entries as they were.
    """
    scope = WorkspaceScope(thread.organization_id, thread.workspace_id)
    await require_active_workspace(session, thread.workspace_id)
    principal, revision, options = source.principal, source.revision, source.options
    parent, lineage = await parent_of(session, thread)
    # An instance reserved here is a new row that a refusal discards with it; freezing checks the mounts are usable.
    template_id = primary_template(thread, revision.config)
    if template_id is not None:
        await reserve_primary(
            session, principal, thread, template_id=template_id, limit=runtime.settings.environments.managed_count
        )
    mounts = await freeze_mounts(session, thread, principal_id=principal.id)
    memories = await freeze_memories(
        session, thread, revision.config.memory_mounts, limit=runtime.settings.memory.mounts_per_thread
    )
    payload = source.resume.input if source.resume is not None else None
    if source.entry is not None and source.entry.kind == "message":
        payload = MessagePayload.model_validate(source.entry.payload)
    if payload is not None:
        await require_readable(
            session,
            principal,
            scope,
            revision,
            options.overrides,
            payload,
            authority=source.authority,
            primary=has_primary(mounts),
        )
    run_id = new_object_id("run")
    if runtime.admission is not None:
        await runtime.admission.accept(
            session,
            AcceptedIntent(
                organization_id=thread.organization_id,
                workspace_id=thread.workspace_id,
                session_id=thread.session_id,
                thread_id=thread.id,
                run_id=run_id,
                principal_id=principal.id,
                agent_id=revision.agent_id,
                agent_revision_id=revision.revision_id,
                trigger=source.trigger,
                root_run_id=(await delegation(session, thread, run_id)).root_run_id,
            ),
        )
    current = await now(session)
    run = RunRow(
        id=run_id,
        organization_id=thread.organization_id,
        workspace_id=thread.workspace_id,
        session_id=thread.session_id,
        thread_id=thread.id,
        agent_id=revision.agent_id,
        agent_revision_id=revision.revision_id,
        revision_selection=source.revision_selection,
        principal_id=principal.id,
        authority=source.authority.model_dump(mode="json"),
        options=options.model_dump(mode="json", exclude_none=True),
        options_digest=source.options_digest,
        # Frozen here: the thread's headers apply to this run even if the thread is edited later.
        mcp_headers=thread.mcp_headers,
        environment_mounts=mounts,
        memory_mounts=memories,
        memory_cursors=inherited_cursors(parent, memories),
        source_entry_id=source.entry.id if source.entry is not None else None,
        resume=source.resume.model_dump(mode="json") if source.resume is not None else None,
        resumed_by_id=source.resumed_by_id,
        request_key=source.request_key,
        request_digest=source.request_digest,
        trigger=source.trigger,
        lineage=lineage,
        parent_run_id=parent.id if parent is not None else None,
        status="accepted",
        available_at=current,
        max_attempts=runtime.settings.worker.max_attempts,
        labels=dict(options.labels),
    )
    session.add(run)
    await session.flush()
    if source.entry is not None:
        inbox.assign(source.entry, run)
    thread.current_run_id = run.id
    await session.execute(
        update(SessionRow)
        .where(SessionRow.id == thread.session_id)
        .values(last_run_id=run.id, updated_at=func.clock_timestamp())
    )
    await session.flush()
    await notify_subscribers(session, runtime, run, ["run.accepted"], at=current)
    runtime.wake_workers(session)
    after_commit(session, partial(_accepted, run.id, thread.id, source.trigger))
    return run


async def _accepted(run_id: str, thread_id: str, trigger: Trigger) -> None:
    # Within a request, the record also carries its request ID: the link from a request to the run it started.
    RUNS_ACCEPTED.add(1, {"trigger": trigger})
    logger.info("Run accepted", extra={"run_id": run_id, "thread_id": thread_id, "trigger": trigger})


async def _source(
    session: AsyncSession, runtime: Runtime, thread: ThreadRow, entry: InboxEntryRow, explicit: Source | None
) -> Source | Failure:
    if explicit is not None and entry is explicit.entry:
        return explicit
    if entry.kind == "message":
        return await Source.load(session, runtime, thread, entry, "queued")
    origin = await _run(session, entry.origin_run_id)
    if origin is None or origin.status not in {"completed", "waiting"}:
        return Failure(code="origin_not_committed", message="The spawning run never became thread history")
    return await Source.inherited(session, runtime, origin, "child_result", entry=entry)


async def accept(
    session: AsyncSession, runtime: Runtime, thread: ThreadRow, *, explicit: Source | None = None
) -> RunRow | None:
    """Start the thread's next run from its eligible queued source, if any. Rejected entries fail in place.

    `explicit` is the source of the entry the caller just appended, already loaded and checked. A transient
    refusal (`unavailable`) is not the entry's fault: it aborts the transaction, so a retry of the caller's
    operation or the advance sweep can still start the entry.
    """
    head = await _run(session, thread.head_run_id)
    if not eligible(thread, head):
        return None
    if paused(await _run(session, thread.last_run_id)):
        # An explicit submission may start that new message; unrelated pending entries never replace it.
        entries = [explicit.entry] if explicit is not None and explicit.entry is not None else []
    else:
        entries = await inbox.pending_entries(session, thread.id, limit=SCAN)
    for entry in entries:
        try:
            source = await _source(session, runtime, thread, entry, explicit)
            if isinstance(source, Source):
                # A savepoint makes one entry's rejection its own; database errors still abort the transaction.
                async with session.begin_nested():
                    return await start_run(session, runtime, thread, source)
        except ServiceError as error:
            if error.code == "unavailable":
                raise
            source = Failure.of(error)
        inbox.fail(entry, source, at=await now(session))
        await session.flush()
    return None


async def advance(runtime: Runtime, thread_id: str, *, skip_locked: bool = False) -> None:
    """Successor acceptance in its own transaction after a seal or delivery; the sweep covers a lost call."""
    try:
        async with transaction(runtime.storage) as session:
            thread = await session.scalar(
                select(ThreadRow).where(ThreadRow.id == thread_id).with_for_update(skip_locked=skip_locked)
            )
            if thread is None:
                return
            await accept(session, runtime, thread)
    except Exception as error:
        # The preceding operation is already committed. The sweep retries this independent transaction.
        logger.warning(
            "Thread advance failed", extra={"thread_id": thread_id, "exception_details": exception_details(error)}
        )
        return


class ThreadAdvancer:
    """The advance_threads sweep: idle, unpaused threads with pending input, visited in rotating ID order.

    Waiting heads are excluded before the batch limit. Rotation keeps a thread that fails to advance
    from holding up the others; it is logged and retried by a later pass.
    """

    def __init__(self, runtime: Runtime, *, batch: int):
        self.runtime, self.batch = runtime, batch
        self.after = ""

    async def __call__(self) -> None:
        async with transaction(self.runtime.storage) as session:
            ids = (
                await session.scalars(
                    select(ThreadRow.id)
                    .where(
                        ThreadRow.current_run_id.is_(None),
                        ~exists().where(RunRow.id == ThreadRow.head_run_id, RunRow.status == "waiting"),
                        ThreadRow.archived_at.is_(None),
                        ThreadRow.last_run_id.is_not_distinct_from(ThreadRow.head_run_id),
                        ThreadRow.id > self.after,
                        exists().where(InboxEntryRow.thread_id == ThreadRow.id, InboxEntryRow.status == "pending"),
                    )
                    .order_by(ThreadRow.id)
                    .limit(self.batch)
                )
            ).all()
        self.after = ids[-1] if len(ids) == self.batch else ""
        for thread_id in ids:
            await advance(self.runtime, thread_id, skip_locked=True)
