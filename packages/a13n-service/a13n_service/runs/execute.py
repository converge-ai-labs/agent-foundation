"""Executing one attempt of a run: restore it, run the Harness, commit checkpoints at safe boundaries, end it.

`execute` owns the orchestration. Adapters (the model, connections, environments) own their I/O and never
run persistence, and no database session survives an external call.

1. Plan: revalidate the run's principal, authority, revision and model in one short session, then restore its
   checkpoint or start from its parent's state.
2. Stream: offer the entries assigned to the run as the input; at every boundary the `Boundaries` capability
   marks, commit a checkpoint and assign compatible pending steers in one transaction, then offer them.
3. End: seal a completed or waiting outcome in the transaction that commits it as the final checkpoint; seal a
   failure or cancellation with the display's interrupted tail; give the run back on handoff or a transient
   failure.
"""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from functools import partial

import anyio
from a13n_harness import (
    DeferredToolResume,
    HarnessError,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessRunResult,
    HarnessRunStream,
    HarnessState,
    HarnessStreamEvent,
    RunBindings,
    RunError,
    RunModelResolver,
    RunPreparationContext,
)
from a13n_harness.capabilities import MemoryCursors
from a13n_harness.capabilities.steering import steering_input_ids
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.identity import AgentIdentityRef, AgentInstanceContext
from a13n_harness.providers.environment.errors import EnvironmentProviderError, EnvironmentProviderErrorCategory
from a13n_logging import exception_details, get_logger
from pydantic import JsonValue
from pydantic_ai.messages import UserContent
from pydantic_ai.usage import UsageLimits

from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.telemetry import attempt_observation
from a13n_service.resources.agents.service import load_revision
from a13n_service.runs import agent, checkpoints, claim, deferred, inbox, inputs
from a13n_service.runs.accept import advance, delegation
from a13n_service.runs.admission import CallContext
from a13n_service.runs.agent import ResolvedAgent
from a13n_service.runs.attachments import Recipient
from a13n_service.runs.attempts import (
    AttemptControl,
    AuthorityRevoked,
    Lease,
    LeaseLost,
    authorize_execution,
    lock_thread_lease,
)
from a13n_service.runs.boundaries import Boundaries, SafeBoundary
from a13n_service.runs.calls import CallCheck
from a13n_service.runs.checkpoints import Committed, RunState
from a13n_service.runs.coalesce import Coalescer
from a13n_service.runs.display import Display, DisplayFold
from a13n_service.runs.environments.execution import PreparedMount, open_mounts, prepare_mounts
from a13n_service.runs.environments.mounts import PRIMARY
from a13n_service.runs.host import HostPlan, open_host, resolve_host
from a13n_service.runs.inputs import Offered
from a13n_service.runs.resume import normalize
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import (
    EnvironmentMount,
    Failure,
    Outcome,
    Pending,
    Resume,
    ResumeRequest,
    RunOptions,
    canonical_json,
)
from a13n_service.runs.seal import release_attempt, seal, seal_attempt
from a13n_service.runs.stream import ThreadStream
from a13n_service.runs.subagents import ChildRuns
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.runs.usage import SnapshotReporter, UsageBuffer, ingest_late, totals
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Plan:
    """What an attempt runs and where it continues from, read and restored before anything is delivered."""

    session_id: str
    source_entry_id: str | None
    mounts: tuple[EnvironmentMount, ...]
    memory_cursors: dict[str, str | None]
    principal: Principal
    authority: ExecutionAuthority
    agent: ResolvedAgent
    host: HostPlan
    root_run_id: str
    # Model requests the run made so far, across attempts, and the run's own limit on them.
    used: int
    limit: int | None
    # Entries assigned to the run and not yet consumed, in position order: this attempt's input.
    assigned: list[Offered]
    state: HarnessState
    resume: DeferredToolResume | None
    display: Display
    committed: Committed | None
    seq: int


async def execute(runtime: Runtime, lease: Lease, control: AttemptControl) -> None:
    """The worker's execution of one leased attempt. Without the lease nothing is written: `LeaseLost` escapes."""
    try:
        plan = await _plan(runtime, lease)
    except LeaseLost:
        raise
    except Exception as error:
        outcome = _failure(lease, error)
        if outcome is None:
            await _release(runtime, lease, error)
        else:
            await seal_attempt(runtime, lease, outcome)
        return
    await _Attempt(runtime, lease, control, plan).run()


class _EnvironmentUnavailable(Exception):
    """A mounted environment can no longer be used by this run; a new mount is the user's decision."""


# Provider failures that a later attempt may not repeat; the others need a changed mount or configuration.
_TRANSIENT_ENVIRONMENT = frozenset(
    {
        EnvironmentProviderErrorCategory.UNAVAILABLE,
        EnvironmentProviderErrorCategory.TIMEOUT,
        EnvironmentProviderErrorCategory.UNKNOWN_OUTCOME,
        EnvironmentProviderErrorCategory.PROVIDER_FAILURE,
    }
)


def _failure(lease: Lease, error: Exception) -> Outcome | None:
    """The failure a deterministic error seals, logged with its cause, or None for a transient one the run
    recovers from."""
    outcome = _deterministic(error)
    if outcome is not None:
        logger.warning(
            "Attempt sealed a failure",
            extra={
                "run_id": lease.run_id,
                "attempt_id": lease.attempt_id,
                "exception_details": exception_details(error),
            },
        )
    return outcome


def _own(error: Exception) -> Exception:
    """The Service's own failure inside a Harness hook, such as a skill package read or a lease proof, which the
    Harness wraps and keeps as the cause; it keeps its meaning. Any other error is itself."""
    cause = error.__cause__
    return cause if isinstance(error, HarnessError) and isinstance(cause, ServiceError | LeaseLost) else error


def _deterministic(error: Exception) -> Outcome | None:
    if isinstance(error, AuthorityRevoked):
        return error.outcome
    if isinstance(error, ServiceError):
        return None if error.code == "unavailable" else Outcome.refused(error)
    if isinstance(error, HarnessError):
        return Outcome.failed(error.code, str(error))
    if isinstance(error, _EnvironmentUnavailable):
        return Outcome.failed("environment_unavailable", str(error))
    if isinstance(error, EnvironmentProviderError) and error.category not in _TRANSIENT_ENVIRONMENT:
        return Outcome.failed("environment_unavailable", error.safe_projection().message)
    return None


async def _release(runtime: Runtime, lease: Lease, error: Exception) -> None:
    logger.error(
        "Attempt failed",
        extra={"run_id": lease.run_id, "attempt_id": lease.attempt_id, "exception_details": exception_details(error)},
    )
    failure = Failure(code="attempt_failed", message=f"The attempt failed with {type(error).__name__}")
    await release_attempt(runtime, lease, status="failed", failure=failure)


async def _plan(runtime: Runtime, lease: Lease) -> _Plan:
    async with short_session(runtime.storage) as session:
        run = await session.get(RunRow, lease.run_id)
        thread = await session.get(ThreadRow, lease.thread_id)
        if run is None or thread is None:
            raise LeaseLost()
        # The run keeps its accepted principal and authority, but only while both are still valid.
        principal = await authorize_execution(session, runtime.access, run)
        scope = WorkspaceScope(run.organization_id, run.workspace_id)
        authority = ExecutionAuthority.model_validate(run.authority)
        revision = await load_revision(session, run.agent_id, run.agent_revision_id)
        options = RunOptions.model_validate(run.options)
        resolved = await agent.resolve(
            session, principal, scope, revision, authority=authority, override=options.overrides
        )
        host = await resolve_host(session, run, principal, scope, resolved, authority=authority)
        assigned = [Offered.of(entry) for entry in await inbox.assigned_entries(session, run.id)]
        used = (await totals(session, run.id))["requests"]
        root = (await delegation(session, thread, run.id)).root_run_id
        parent = await session.get(RunRow, run.parent_run_id) if run.parent_run_id is not None else None
        checkpoint = checkpoints.StatePointer.model_validate(run.checkpoint) if run.checkpoint else None
        display_pointer = checkpoints.DisplayPointer.model_validate(run.display) if run.display else None
        committed = Committed.of(run)
        parent_id = parent.id if parent is not None else None
        parent_checkpoint = checkpoints.require_compatible(parent.id, parent.checkpoint) if parent is not None else None
        pending = Pending.model_validate(parent.pending) if parent is not None and parent.pending is not None else None
        answers = Resume.model_validate(run.resume) if run.resume is not None else None
        fork = run.lineage != "continue"
        session_id, source_entry_id = run.session_id, run.source_entry_id
        mounts = tuple(EnvironmentMount.model_validate(mount) for mount in run.environment_mounts)
        memory_cursors = dict(run.memory_cursors)
    own, base, display = await asyncio.gather(
        checkpoints.load_state(runtime.objects, lease.organization_id, lease.run_id, checkpoint),
        checkpoints.load_state(runtime.objects, lease.organization_id, parent_id or lease.run_id, parent_checkpoint),
        checkpoints.load_display(runtime.objects, lease.organization_id, lease.run_id, display_pointer),
    )
    state, resume = _initial(lease.thread_id, base, fork=fork, pending=pending, answers=answers)
    if own is not None:
        state = own.harness
        resume = replace(resume, recovery=True).remaining(state.message_history) if resume is not None else None
    return _Plan(
        session_id=session_id,
        source_entry_id=source_entry_id,
        mounts=mounts,
        memory_cursors=memory_cursors,
        principal=principal,
        authority=authority,
        agent=resolved,
        host=host,
        root_run_id=root,
        used=used,
        limit=options.max_usage.requests if options.max_usage is not None else None,
        assigned=assigned,
        state=state,
        resume=resume,
        display=display or Display(),
        committed=committed,
        seq=own.seq if own is not None else 0,
    )


def _initial(
    thread_id: str, base: RunState | None, *, fork: bool, pending: Pending | None, answers: Resume | None
) -> tuple[HarnessState, DeferredToolResume | None]:
    """Continue or fork the parent's history, resolving its wait before the successor consumes input."""
    if base is None:
        return HarnessState.new(thread_id=thread_id), None
    state = base.harness.fork(thread_id=thread_id) if fork else base.harness
    if pending is None:
        return state, None
    return state, deferred.resume(deferred.load(base.deferred), answers or normalize(pending, ResumeRequest()))


class _Offers:
    """Entries offered to this attempt's Harness run, until a checkpoint commit consumes them."""

    def __init__(self, initial: list[Offered]):
        self.initial = {entry.id for entry in initial}
        self.open = {entry.id: entry for entry in initial}
        # Assigned steers the Harness could not take yet: between its native attempts none is active.
        self.unsent: list[Offered] = []
        # Whether a model request carried the input: it is in every state exported after that.
        self.requested = False

    def incorporated(self, state: HarnessState) -> list[str]:
        ids = set(steering_input_ids(state.message_history))
        if self.requested:
            ids |= self.initial
        return [entry_id for entry_id in self.open if entry_id in ids]

    def consumed(self, entry_ids: list[str]) -> None:
        for entry_id in entry_ids:
            del self.open[entry_id]


class _Attempt:
    """One Harness run of the attempt: the display it folds, the input it offered and what it committed."""

    def __init__(self, runtime: Runtime, lease: Lease, control: AttemptControl, plan: _Plan):
        self.runtime, self.lease, self.control, self.plan = runtime, lease, control, plan
        self.committed, self.seq = plan.committed, plan.seq
        worker = runtime.settings.worker
        self.fold = DisplayFold(lease.run_id, plan.display, attempt=lease.number, max_bytes=worker.display_bytes)
        # What an earlier attempt left unfinished continues only if this attempt streams it again.
        self.fold.interrupt()
        self.offers = _Offers(plan.assigned)
        self.recipient = Recipient(
            plan.agent.model.config.characteristics.capabilities,
            primary=any(mount.name == PRIMARY for mount in plan.mounts),
        )
        # The memory cursors the run's history holds context as of; recovery starts from the committed ones.
        self.cursors = MemoryCursors(plan.memory_cursors)
        self.boundaries = Boundaries(self.cursors.snapshot)
        models = {model.key: model for model in plan.agent.models()}
        self.check = CallCheck(runtime, control, self._call_context(), models=models, used=plan.used, limit=plan.limit)
        self.usage = UsageBuffer(self.check.calls)
        self.usage_reporter = SnapshotReporter(runtime.storage, lease.run_id, lease.attempt_id, self.usage)
        self.yielding = False

    async def run(self) -> None:
        try:
            result = await self._stream()
            if result is None:
                # The worker is draining before the Harness run started: another worker prepares the mounts again.
                await release_attempt(self.runtime, self.lease, status="yielded", yield_reason="handoff")
                return
            await self._end(result)
        except (LeaseLost, asyncio.CancelledError):
            # Neither seals the run, but its charges so far stay recorded, even while the task is cancelled.
            await self._record_usage()
            raise
        except Exception as wrapped:
            await self._record_usage()
            error = _own(wrapped)
            if isinstance(error, LeaseLost):
                raise error from None
            outcome = self._refusal() or _failure(self.lease, error)
            if outcome is None:
                await _release(self.runtime, self.lease, error)
            else:
                await self._seal_interrupted(outcome)

    async def _stream(self) -> HarnessRunResult | None:
        """The Harness run's result, or None when the worker drained while the mounts were being prepared."""
        runtime, lease = self.runtime, self.lease
        prepared = await self._prepare_mounts()
        if prepared is None:
            return None
        async with AsyncExitStack() as stack:
            environments = await stack.enter_async_context(open_mounts(runtime, prepared))
            root = self.plan.agent
            models = await agent.open_models(stack, runtime, root)
            host = await stack.enter_async_context(
                open_host(
                    runtime,
                    self.lease,
                    self.check,
                    self.plan.host,
                    models,
                    principal=self.plan.principal,
                    authority=self.plan.authority,
                    cursors=self.cursors,
                )
            )
            executable = agent.build(
                root,
                capabilities=lambda node: (
                    [self.boundaries, *host.capabilities(node), *host.memory]
                    if node is root
                    else host.capabilities(node)
                ),
                child_bindings=host.bindings,
                operators=self._operator,
                plugins=runtime.plugins,
                instrumentation=runtime.instrumentation,
            )
            live = await stack.enter_async_context(
                ThreadStream(
                    runtime.redis,
                    runtime.settings,
                    thread_id=lease.thread_id,
                    run_id=lease.run_id,
                    attempt=lease.number,
                )
            )
            output = await stack.enter_async_context(
                Coalescer(self.fold, live, window=runtime.settings.worker.stream_coalesce_seconds)
            )
            start = partial(
                executable.stream,
                input_factory=self._assigned_input if self.plan.assigned else None,
                previous_state=self.plan.state,
                # Recovery creates a new writer/attempt, not a serialized same-writer accounting resume.
                resume_usage=False,
                deferred_resume=self.plan.resume,
                tool_recovery="declared",
                bindings=host.bindings(root, self._bindings(agent.model_resolver(models))),
                # The call check enforces the run's own request limit across attempts.
                usage_limits=UsageLimits(request_limit=None),
            )
            # The Harness takes no empty environment set; a run without mounts runs without one.
            stream = (
                start(environments=environments, default_environment=PRIMARY if PRIMARY in environments else None)
                if environments
                else start()
            )
            await claim.start(runtime, self.lease, harness_run_id=stream.run_id)
            async with stream:
                interrupt = asyncio.create_task(self._cancel_when_stopped(stream))
                try:
                    async for item in stream:
                        await self._observe(item, stream, output)
                finally:
                    interrupt.cancel()
        if stream.result is None:
            raise ServiceError("unavailable", "The Harness run ended without a result", {"dependency": "harness"})
        return stream.result

    async def _prepare_mounts(self) -> list[PreparedMount] | None:
        """Waiting for sandboxes to start can take minutes, so an interrupt or a handoff stops the wait; None
        means the worker is draining."""
        mounts = list(self.plan.mounts)
        if not mounts:
            return []
        preparing = self.runtime.tasks.start(
            prepare_mounts(self.runtime, self.lease, self.plan.principal, self.plan.authority, mounts),
            name=f"prepare-mounts-{self.lease.attempt_id}",
        )
        stopped = asyncio.create_task(self.control.stopped.wait())
        handoff = asyncio.create_task(self.control.handoff.wait())
        try:
            done, _ = await asyncio.wait((preparing, stopped, handoff), return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Cancelling a finished task does nothing.
            for task in (preparing, stopped, handoff):
                task.cancel()
        if preparing not in done:
            if stopped in done:
                self.check.refuse(self.control.outcome)
            return None
        try:
            return preparing.result()
        except ServiceError as error:
            if error.code == "unavailable":
                raise
            raise _EnvironmentUnavailable(error.message) from error

    async def _observe(self, item: HarnessStreamEvent, stream: HarnessRunStream, output: Coalescer) -> None:
        event = item.event if isinstance(item, HarnessEvent) else None
        if isinstance(event, HarnessExtensionEvent) and event.kind == "usage":
            self.usage.report(event.payload)  # Every charge of the run, an inline child run's included.
        if item.run_id != stream.run_id:
            return  # Other output of an inline child run belongs to that child's own observation.
        if isinstance(event, SafeBoundary):
            await self._boundary(event, stream, output)
            return
        output.observe(item)

    async def _boundary(self, boundary: SafeBoundary, stream: HarnessRunStream, output: Coalescer) -> None:
        if boundary.at == "model":
            self.offers.requested = True
        output.flush()  # The checkpoint's display covers every event observed before the boundary.
        staged = self.boundaries.take(boundary.token)
        steers = await self._commit(staged.state, cursors=staged.cursors)
        output.boundary()
        if boundary.at == "model" and self.control.handoff.is_set():
            # Yield where the request in flight is simply sent again; at a tool boundary recovery would have to
            # treat calls that never ran as unknown effects. Steers just assigned stay with the run.
            self.yielding = True
            stream.cancel()
            return
        # A tool boundary waits for the acknowledgement, so steers pending there join the request after the tools.
        await self._offer(stream, steers)
        self.boundaries.acknowledge(boundary.token)

    async def _commit(
        self,
        state: HarnessState,
        *,
        cursors: dict[str, str | None],
        outcome: Outcome | None = None,
        deferred: JsonValue = None,
    ) -> list[Offered]:
        """Commit a checkpoint and what follows it in one fenced transaction: a completed or waiting outcome seals
        the run; otherwise a bounded batch of compatible pending steers is assigned to it, and returned."""
        if self._near_deadline():
            raise LeaseLost()
        consumed = self.offers.incorporated(state)
        usage = self.usage.pending()
        committed = await checkpoints.publish_checkpoint(
            self.runtime,
            self.lease,
            RunState(harness=state, seq=self.seq + 1, attempt=self.lease.number, deferred=deferred),
            self.fold.snapshot(),
        )
        worker = self.runtime.settings.worker
        steers: list[Offered] = []
        async with transaction(self.runtime.storage) as session:
            # Thread first: consuming entries fires the thread-version trigger, which updates the thread row.
            thread, run, attempt, current = await lock_thread_lease(session, self.lease)
            await checkpoints.commit(
                session,
                run,
                attempt,
                previous=self.committed,
                committed=committed,
                memory_cursors=cursors,
                consumed=consumed,
                usage=usage,
                at=current,
            )
            checkpoints.retire(session, run, self.committed, committed)
            if outcome is not None:
                await seal(session, self.runtime, thread, run, attempt, outcome, at=current)
            else:
                entries = await inbox.assign_steers(
                    session,
                    thread,
                    run,
                    max_count=worker.delivery_count,
                    max_bytes=worker.delivery_bytes,
                    scan=self.runtime.settings.control.inbox_count,
                )
                steers = [Offered.of(entry) for entry in entries]
        self.committed, self.seq = committed, self.seq + 1
        self.offers.consumed(consumed)
        self.usage.ingested(usage)
        if outcome is not None:
            await advance(self.runtime, self.lease.thread_id)
        return steers

    async def _offer(self, stream: HarnessRunStream, steers: list[Offered]) -> None:
        """Offer the steers assigned at this boundary, and any the Harness could not take before, to the run's next
        request."""
        self.offers.unsent.extend(steers)
        while self.offers.unsent:
            entry = self.offers.unsent[0]
            content = await self._read(entry, stream.context.environment)
            if content is None:
                self.offers.unsent.pop(0)
                continue
            try:
                await stream.steer(content, input_id=entry.id)
            except RunError as error:
                if error.code != "run_not_active":
                    raise
                return
            self.offers.open[entry.id] = self.offers.unsent.pop(0)

    async def _assigned_input(self, context: RunPreparationContext) -> tuple[UserContent, ...]:
        """The Harness input factory: the assigned entries' content, read once the environments are ready, so the
        files it refers to are placed first."""
        parts: list[UserContent] = []
        for entry in self.plan.assigned:
            parts.extend(await self._read(entry, context.environment) or ())
        if not parts:
            # Each entry was a steer that failed alone; the next attempt continues without them.
            raise ServiceError("unavailable", "No assigned input could be read", {"dependency": "input"})
        return tuple(parts)

    async def _read(self, entry: Offered, environment: BoundEnvironment) -> tuple[UserContent, ...] | None:
        """The entry's content, its files placed in `environment`, or None for a steer that cannot be read,
        whether offered at a boundary or again by a recovered attempt: it fails alone and the run goes on without
        it. The run's own source entry is what it runs on, so a refusal to read that one fails the run."""
        try:
            return await inputs.content(self.runtime, self.plan.principal, self.recipient, environment, entry)
        except ServiceError as error:
            if error.code == "unavailable" or entry.id == self.plan.source_entry_id:
                raise
            failure = Failure.of(error)
        async with transaction(self.runtime.storage) as session:
            _, run, _, current = await lock_thread_lease(session, self.lease)
            await inbox.fail_assigned(session, run.id, entry.id, failure, at=current)
        self.offers.open.pop(entry.id, None)
        return None

    async def _cancel_when_stopped(self, stream: HarnessRunStream) -> None:
        await self.control.stopped.wait()
        stream.cancel()

    async def _end(self, result: HarnessRunResult) -> None:
        self.usage.add(result.usage_records)
        if result.status == "failed":
            assert result.failure is not None
            await self._record_usage()
            failed = Outcome.failed(result.failure.code, result.failure.message)
            await self._seal_interrupted(self._refusal() or failed)
            return
        if result.status == "cancelled":
            await self._record_usage()
            # The stream is cancelled only to yield at a boundary or because the run was stopped.
            refusal = self._refusal()
            if self.yielding and refusal is None and not self.control.stopped.is_set():
                await release_attempt(self.runtime, self.lease, status="yielded", yield_reason="handoff")
            else:
                await self._seal_interrupted(refusal or self.control.outcome)
            return
        # Completed and suspended results always carry their state.
        assert result.state is not None
        if result.status == "completed":
            outcome = Outcome(status="completed", output=self._output(result.output))
            await self._commit(result.state, cursors=self.cursors.snapshot(), outcome=outcome)
        else:
            assert result.deferred is not None
            outcome = Outcome(status="waiting", pending=deferred.pending(result.deferred))
            await self._commit(
                result.state,
                cursors=self.cursors.snapshot(),
                outcome=outcome,
                deferred=deferred.dump(result.deferred),
            )

    async def _seal_interrupted(self, outcome: Outcome) -> None:
        """Seal a failure or cancellation with the display this attempt folded, its unfinished items interrupted."""
        self.fold.interrupt()
        display = None
        if not self._near_deadline():
            display = await checkpoints.publish_display(self.runtime, self.lease, self.fold.snapshot())
        await seal_attempt(self.runtime, self.lease, outcome, display=display)

    def _near_deadline(self) -> bool:
        """An object write must land before a takeover could clean the run's prefix, so none starts near it."""
        return self.control.expiring(self.runtime.settings.objects.timeout)

    def _refusal(self) -> Outcome | None:
        """What a refused call means for the run: the outcome to seal, or none when nothing was refused.

        A call refused because the lease missed its renewal ends the attempt instead: `LeaseLost` escapes and
        the run is recovered once the lease expires.
        """
        if self.check.lease_missed:
            raise LeaseLost()
        return self.check.refusal

    async def _record_usage(self) -> None:
        """Usage not committed with a checkpoint is a past charge: recorded even when the lease is gone or the
        attempt is being cancelled, within one bounded database operation."""
        with anyio.CancelScope(shield=True), anyio.move_on_after(self.runtime.settings.database.statement_timeout):
            try:
                await self.usage_reporter.flush()
                reports = self.usage.pending()
                await ingest_late(self.runtime.storage, self.lease.run_id, self.lease.attempt_id, reports)
            except Exception as error:
                logger.error(
                    "Usage could not be recorded",
                    extra={"run_id": self.lease.run_id, "exception_details": exception_details(error)},
                )
            else:
                self.usage.ingested(reports)

    def _output(self, output: JsonValue) -> JsonValue:
        limit = self.runtime.settings.worker.output_bytes
        if len(canonical_json(output)) > limit:
            raise ServiceError("payload_too_large", "Run output exceeds its byte limit", {"limit": limit})
        return output

    def _call_context(self) -> CallContext:
        """Each call fills in its own identity and what serves it."""
        lease = self.lease
        return CallContext(
            organization_id=lease.organization_id,
            workspace_id=lease.workspace_id,
            session_id=self.plan.session_id,
            thread_id=lease.thread_id,
            run_id=lease.run_id,
            run_attempt_id=self.lease.attempt_id,
            root_run_id=self.plan.root_run_id,
            call_id="",
            source="",
            provider_id=None,
        )

    def _operator(self, node: ResolvedAgent) -> ChildRuns:
        """The child runs one async agent of the graph starts through its own edges."""
        return ChildRuns(self.runtime, self.lease, self.control, node.subagents)

    def _bindings(self, resolver: RunModelResolver) -> RunBindings:
        lease = self.lease
        return RunBindings(
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="a13n-service", subject=self.plan.principal.id),
                agent_instance_id=lease.run_id,
                host_refs={"run_id": lease.run_id, "thread_id": lease.thread_id, "attempt_id": lease.attempt_id},
            ),
            model_resolver=resolver,
            model_call_check=self.check,
            usage_reporter=self.usage_reporter,
            observation=attempt_observation(
                organization_id=lease.organization_id,
                workspace_id=lease.workspace_id,
                session_id=self.plan.session_id,
                thread_id=lease.thread_id,
                run_id=lease.run_id,
                run_attempt_id=lease.attempt_id,
            ),
        )
