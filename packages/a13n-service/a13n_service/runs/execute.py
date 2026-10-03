"""Executing one attempt of a run: restore it, run the Harness, commit checkpoints at safe boundaries, end it.

`execute` owns the orchestration. Adapters (the model, connections, environments) own their I/O and never
run persistence, and no database session survives an external call.

1. Plan: revalidate the run's principal, authority, revision and model in one short session, then restore its
   checkpoint or start from the last checkpoint of its nearest ancestor that has one.
2. Stream: offer the entries assigned to the run as the input; at every boundary the `Boundaries` capability
   marks, commit a checkpoint and assign compatible pending steers in one transaction, then offer them.
3. End: seal a completed or waiting outcome in the transaction that commits it as the final checkpoint; seal a
   failure or cancellation with the display's unfinished items interrupted; give the run back on handoff or a
   transient failure.
"""

import asyncio
import time
from collections.abc import Collection
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from functools import partial
from typing import Literal

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
    RunConfiguration,
    RunError,
    RunModelResolver,
    RunPreparationContext,
)
from a13n_harness.capabilities import MemoryCursors
from a13n_harness.capabilities.steering import steering_input_ids
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.identity import AgentIdentityRef, AgentInstanceContext
from a13n_harness.providers.environment.errors import EnvironmentProviderError, EnvironmentProviderErrorCategory
from a13n_harness.usage import without_usage
from a13n_logging import exception_details, get_logger
from a13n_stream_protocol.display import DisplayFold, Snapshot, Tail, open_tool_calls
from pydantic import JsonValue
from pydantic_ai.messages import UserContent
from pydantic_ai.usage import UsageLimits
from sqlalchemy.ext.asyncio import AsyncSession

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
from a13n_service.runs.checkpoints import CHECKPOINT_DURATION, Committed, RunObjects, RunState, near_deadline
from a13n_service.runs.coalesce import Coalescer
from a13n_service.runs.environments.execution import PreparedMount, open_mounts, prepare_mounts
from a13n_service.runs.environments.mounts import PRIMARY
from a13n_service.runs.history import HISTORY, MessageHistory, initial
from a13n_service.runs.host import HostPlan, open_host, resolve_host
from a13n_service.runs.inputs import Offered
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import (
    EnvironmentMount,
    Failure,
    Outcome,
    Pending,
    Resume,
    RunOptions,
    canonical_json,
)
from a13n_service.runs.seal import release_attempt, seal, seal_attempt
from a13n_service.runs.stream import ThreadStream
from a13n_service.runs.subagents import ChildRuns
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.runs.usage import DeltaReporter, UsageBuffer, ingest_late, totals
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Plan:
    """What an attempt runs and where it continues from, read and restored before anything is delivered."""

    session_id: str
    configuration: RunConfiguration
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
    resume_input: Offered | None
    # A run that starts from a failed or cancelled one never repeats that run's unanswered tool calls.
    tool_recovery: Literal["declared", "never"]
    tail: Tail
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
            session,
            principal,
            scope,
            revision,
            authority=authority,
            override=options.overrides,
            registry=runtime.registry,
        )
        host = await resolve_host(session, run, principal, scope, resolved, authority=authority)
        assigned = [Offered.of(entry) for entry in await inbox.assigned_entries(session, run.id)]
        used = (await totals(session, run.id))["requests"]
        root = (await delegation(session, thread, run.id)).root_run_id
        parent = await session.get(RunRow, run.parent_run_id) if run.parent_run_id is not None else None
        checkpoint = checkpoints.require_compatible(run)
        tail_pointer = checkpoints.TailPointer.model_validate(run.tail) if run.tail else None
        committed = Committed.of(run)
        baseline = await _baseline(session, parent)
        baseline_checkpoint = checkpoints.require_compatible(baseline) if baseline is not None else None
        pending = Pending.model_validate(parent.pending) if parent is not None and parent.pending is not None else None
        answers = Resume.model_validate(run.resume) if run.resume is not None else None
        history = HISTORY.validate_python(thread.message_history)
        # Until its own first checkpoint the run continues its parent's; a takeover after it repeats its own calls.
        unknown_outcomes = checkpoint is None and parent is not None and parent.status in {"failed", "cancelled"}
        session_id, source_entry_id = run.session_id, run.source_entry_id
        mounts = tuple(EnvironmentMount.model_validate(mount) for mount in run.environment_mounts)
        memory_cursors = dict(run.memory_cursors)
    own, base, tail = await asyncio.gather(
        checkpoints.load_state(runtime.objects, checkpoint),
        checkpoints.load_state(runtime.objects, baseline_checkpoint),
        checkpoints.load_tail(runtime.objects, tail_pointer),
    )
    state, resume = _initial(lease.thread_id, base, pending=pending, answers=answers, history=history)
    resume_input = (
        Offered(lease.run_id, "message", lease.workspace_id, answers.input.model_dump(mode="json"))
        if answers is not None and answers.input is not None and not (own and own.resume_input_consumed)
        else None
    )
    if own is not None:
        state = own.harness
        resume = replace(resume, recovery=True).remaining(state.message_history) if resume is not None else None
    return _Plan(
        session_id=session_id,
        configuration=options.configuration or RunConfiguration(),
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
        resume_input=resume_input,
        tool_recovery="never" if unknown_outcomes else "declared",
        tail=tail,
        committed=committed,
        seq=own.seq if own is not None else 0,
    )


async def _baseline(session: AsyncSession, run: RunRow | None) -> RunRow | None:
    """The nearest of `run` and its ancestors that has a checkpoint: a run sealed before its first checkpoint
    leaves the state it started from."""
    while run is not None and run.checkpoint is None:
        run = await session.get(RunRow, run.parent_run_id) if run.parent_run_id is not None else None
    return run


def _initial(
    thread_id: str,
    base: RunState | None,
    *,
    pending: Pending | None,
    answers: Resume | None,
    history: MessageHistory | None = None,
) -> tuple[HarnessState, DeferredToolResume | None]:
    """Continue the baseline's history, forking it when it belongs to another thread, and resolve the parent's wait
    before the successor consumes input."""
    if base is None:
        return HarnessState.new(thread_id=thread_id, message_history=initial(history or [])), None
    fork = base.harness.thread_id != thread_id
    state = base.harness.fork(thread_id=thread_id) if fork else base.harness
    if pending is None:
        return state, None
    if fork:
        answers = deferred.fork_results(pending)
    if answers is None:
        raise ValueError("A waiting parent requires explicit resume results")
    return state, deferred.resume(deferred.load(base.deferred), answers)


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
        self.fold = DisplayFold(
            lease.run_id, plan.tail, attempt=lease.number, page_items=worker.page_items, page_bytes=worker.page_bytes
        )
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
        self.usage_reporter = DeltaReporter(runtime.storage, lease.run_id, lease.attempt_id, self.usage)
        self.yielding = False
        self.live: ThreadStream | None = None

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
            environments = await stack.enter_async_context(
                open_mounts(runtime, prepared, configuration=self.plan.configuration)
            )
            root = self.plan.agent
            models = await agent.open_models(stack, runtime, root, configuration=self.plan.configuration)
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
                    configuration=self.plan.configuration,
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
            self.live = live
            output = await stack.enter_async_context(
                Coalescer(self.fold, live, window=runtime.settings.worker.stream_coalesce_seconds)
            )
            start = partial(
                executable.stream,
                input_factory=self._assigned_input if self.plan.assigned or self.plan.resume_input else None,
                previous_state=self.plan.state,
                # Recovery creates a new writer/attempt, not a serialized same-writer accounting resume.
                resume_usage=False,
                deferred_resume=self.plan.resume,
                tool_recovery=self.plan.tool_recovery,
                bindings=host.bindings(root, self._bindings(agent.model_resolver(root, models))),
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
        if isinstance(event, SafeBoundary):
            if item.run_id == stream.run_id:
                await self._boundary(event, stream, output)
            return
        output.observe(item)

    async def _boundary(self, boundary: SafeBoundary, stream: HarnessRunStream, output: Coalescer) -> None:
        if boundary.at == "model":
            self.offers.requested = True
        output.flush()  # The checkpoint's display covers every event observed before the boundary.
        staged = self.boundaries.take(boundary.token)
        steers = await self._commit(staged.state, cursors=staged.cursors, open_calls=staged.open_calls)
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
        open_calls: Collection[str],
        outcome: Outcome | None = None,
        deferred: JsonValue = None,
    ) -> list[Offered]:
        """Commit a checkpoint and what follows it in one fenced transaction: a completed or waiting outcome seals
        the run; otherwise a bounded batch of compatible pending steers is assigned to it, and returned.

        `open_calls` are the tool calls the state leaves unanswered, whose display items stay unfinished."""
        snapshot = self._snapshot(open_calls)
        if near_deadline(self.runtime, self.control):
            raise LeaseLost()
        started = time.monotonic()
        consumed = self.offers.incorporated(state)
        usage = self.usage.pending()
        pointer, display = await checkpoints.publish_checkpoint(
            self.runtime,
            self.lease,
            RunState(
                # The Service never restores accounting from state: every charge is in its usage records.
                harness=without_usage(state),
                seq=self.seq + 1,
                attempt=self.lease.number,
                deferred=deferred,
                resume_input_consumed=self.plan.resume_input is None or self.offers.requested,
            ),
            snapshot,
        )
        worker = self.runtime.settings.worker
        steers: list[Offered] = []
        async with transaction(self.runtime.storage) as session:
            # Thread first: consuming entries fires the thread-version trigger, which updates the thread row.
            thread, run, attempt, current = await lock_thread_lease(session, self.lease)
            committed = await checkpoints.commit(
                session,
                run,
                attempt,
                previous=self.committed,
                state=pointer,
                display=display,
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
        CHECKPOINT_DURATION.record(time.monotonic() - started)
        self.committed, self.seq = committed, self.seq + 1
        self.fold.committed(snapshot)
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
        if self.plan.resume_input is not None:
            # Unlike a steer, refusal of this frozen initial input fails the whole run.
            parts.extend(
                await inputs.content(
                    self.runtime,
                    self.plan.principal,
                    self.recipient,
                    context.environment,
                    self.plan.resume_input,
                    configuration=self.plan.configuration,
                )
            )
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
            return await inputs.content(
                self.runtime,
                self.plan.principal,
                self.recipient,
                environment,
                entry,
                configuration=self.plan.configuration,
            )
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
        # Completed and suspended results always carry their state; a waiting one leaves its pending calls open.
        assert result.state is not None
        open_calls = open_tool_calls(result.all_messages())
        if result.status == "completed":
            outcome = Outcome(status="completed", output=self._output(result.output))
            await self._commit(result.state, cursors=self.cursors.snapshot(), open_calls=open_calls, outcome=outcome)
        else:
            assert result.deferred is not None
            outcome = Outcome(status="waiting", pending=deferred.pending(result.deferred))
            await self._commit(
                result.state,
                cursors=self.cursors.snapshot(),
                open_calls=open_calls,
                outcome=outcome,
                deferred=deferred.dump(result.deferred),
            )

    async def _seal_interrupted(self, outcome: Outcome) -> None:
        """Seal a failure or cancellation with the display this attempt folded, its unfinished items interrupted."""
        display = None
        if not near_deadline(self.runtime, self.control):
            display = await checkpoints.publish_display(self.runtime, self.lease, self._snapshot())
        await seal_attempt(self.runtime, self.lease, outcome, display=display)

    def _snapshot(self, open_calls: Collection[str] = ()) -> Snapshot:
        """The display to commit, with a safe resume hint captured without waiting for queued Redis writes.

        The writer replaces one immutable value on the same event loop. Terminal callers read it after close.
        """
        snapshot = self.fold.snapshot(open_calls)
        tail, written = snapshot.tail, self.live.last_written if self.live is not None else None
        if (
            written is not None
            and written.attempt == tail.position.attempt
            and written.sequence <= tail.position.sequence
        ):
            tail.resume_after = written.redis_id
        return snapshot

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
            configuration=self.plan.configuration,
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="a13n-service", subject=self.plan.principal.id),
                agent_instance_id=lease.run_id,
                host_refs={"run_id": lease.run_id, "thread_id": lease.thread_id, "attempt_id": lease.attempt_id},
            ),
            model_resolver=resolver,
            model_call_check=self.check,
            usage_reporter=self.usage_reporter,
            state_store=RunObjects(self.runtime, self.lease, self.control),
            observation=attempt_observation(
                organization_id=lease.organization_id,
                workspace_id=lease.workspace_id,
                session_id=self.plan.session_id,
                thread_id=lease.thread_id,
                run_id=lease.run_id,
                run_attempt_id=lease.attempt_id,
            ),
        )
