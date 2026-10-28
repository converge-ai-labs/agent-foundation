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
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
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
    RequestContext,
    RunBindings,
    RunConfiguration,
    RunError,
    RunModelResolver,
    RunPreparationContext,
)
from a13n_harness.capabilities import MemoryCursors
from a13n_harness.capabilities.steering import steering_input_ids
from a13n_harness.compaction import CompactionResult
from a13n_harness.environment.errors import EnvironmentProviderError, EnvironmentProviderErrorCategory
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState
from a13n_harness.identity import AgentIdentityRef, AgentInstanceContext
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from a13n_harness.usage import without_usage
from a13n_logging import exception_details, get_logger
from pydantic import JsonValue
from pydantic_ai.messages import ModelMessage, TextContent, UserContent
from pydantic_ai.usage import UsageLimits
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.telemetry import attempt_observation
from a13n_service.resources.agents.service import load_revision
from a13n_service.resources.agents.toolsets import enabled_tools
from a13n_service.runs import agent, checkpoints, claim, compactions, deferred, inbox, inputs
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
from a13n_service.runs.boundaries import Boundaries, Staged
from a13n_service.runs.calls import CallCheck
from a13n_service.runs.checkpoints import CHECKPOINT_DURATION, Committed, RunObjects, RunState, near_deadline
from a13n_service.runs.coalesce import Coalescer
from a13n_service.runs.compaction_model import CompactionRun, Selection
from a13n_service.runs.contents import Contents
from a13n_service.runs.desktop_assistance import execution as desktop_execution
from a13n_service.runs.desktop_assistance.execution import ExecutionFence
from a13n_service.runs.desktop_assistance.tools import DesktopAssistanceCapability
from a13n_service.runs.display import DisplayFold, Snapshot, Tail, open_tool_calls
from a13n_service.runs.environments.execution import EnvironmentUnavailable, open_mounts, prepare
from a13n_service.runs.environments.lifecycle import public_message
from a13n_service.runs.environments.mounts import PRIMARY
from a13n_service.runs.history import HISTORY, MessageHistory, initial
from a13n_service.runs.host import Host, HostPlan, open_host, resolve_host
from a13n_service.runs.inputs import Offered
from a13n_service.runs.mcp_apps.capture import Captures
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import (
    EnvironmentMount,
    Failure,
    MessagePayload,
    Outcome,
    Pending,
    Resume,
    RunOptions,
    canonical_json,
)
from a13n_service.runs.seal import release_attempt, seal, seal_attempt
from a13n_service.runs.search import Index, seed
from a13n_service.runs.skill_cache import Cache
from a13n_service.runs.stream import ThreadStream
from a13n_service.runs.subagents import ChildRuns
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.runs.tool_executions.owner import resources
from a13n_service.runs.tool_executions.recovery import restore as restore_executions
from a13n_service.runs.tool_executions.state import SharedCodeActState
from a13n_service.runs.usage import DeltaReporter, UsageBuffer, ingest_late, totals
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Plan:
    """What an attempt runs and where it continues from, read and restored before anything is delivered."""

    session_id: str
    retain_tools: bool
    background_compaction: bool
    compaction_result: CompactionResult | None
    configuration: RunConfiguration
    request_context: RequestContext
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
    envd_sessions: dict[str, EnvdSessionState]
    resume: DeferredToolResume | None
    resume_input: Offered | None
    # What changed since the run whose history this one continues; it precedes the input, or is the input of a resume
    # that has none, until a model request carries it.
    reminder: TextContent | None
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
    # The attempt's monitor TaskGroup also wraps failures from its stream body.
    while isinstance(error, ExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    current: BaseException = error
    while isinstance(current, HarnessError) and current.__cause__ is not None:
        current = current.__cause__
    return (
        current
        if isinstance(current, ServiceError | LeaseLost | EnvironmentUnavailable | EndpointPolicyError)
        else error
    )


def _deterministic(error: Exception) -> Outcome | None:
    if isinstance(error, AuthorityRevoked):
        return error.outcome
    if isinstance(error, ServiceError):
        return None if error.code == "unavailable" else Outcome.refused(error)
    if isinstance(error, EndpointPolicyError):
        return Outcome.failed("provider_endpoint_denied", str(error))
    if isinstance(error, HarnessError):
        return Outcome.failed(error.code, _harness_failure_message(str(error), error.details))
    if isinstance(error, EnvironmentUnavailable):
        return Outcome.failed("environment_unavailable", str(error))
    if isinstance(error, EnvironmentProviderError) and error.category not in _TRANSIENT_ENVIRONMENT:
        return Outcome.failed("environment_unavailable", public_message(error))
    return None


def _harness_failure_message(message: str, details: Mapping[str, JsonValue]) -> str:
    """Keep the Harness's safe HTTP status without exposing an upstream body or exception message."""
    status = details.get("status_code")
    # Harness records status_code for ModelHTTPError and its provider-specific subclasses.
    if type(status) is int and 400 <= status <= 599:
        return f"{message} Model provider returned HTTP {status}."
    return message


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
        previous = [EnvironmentMount.model_validate(mount) for mount in baseline.environment_mounts] if baseline else []
        baseline_checkpoint = checkpoints.require_compatible(baseline) if baseline is not None else None
        baseline_run_id = baseline.id if baseline is not None else None
        pending = Pending.model_validate(parent.pending) if parent is not None and parent.pending is not None else None
        answers = Resume.model_validate(run.resume) if run.resume is not None else None
        history = HISTORY.validate_python(thread.message_history)
        # Until its own first checkpoint the run continues its parent's; a takeover after it repeats its own calls.
        unknown_outcomes = checkpoint is None and parent is not None and parent.status in {"failed", "cancelled"}
        session_id, source_entry_id = run.session_id, run.source_entry_id
        source = await session.get(InboxEntryRow, source_entry_id) if source_entry_id else None
        input_text = (
            MessagePayload.model_validate(source.payload).text()
            if source is not None and source.kind == "message"
            else answers.input.text()
            if answers is not None and answers.input is not None
            else ""
        )
        mounts = tuple(EnvironmentMount.model_validate(mount) for mount in run.environment_mounts)
        memory_cursors = dict(run.memory_cursors)
        # Space organization keeps its memory-only surface and completes calls within the round.
        retain_tools = thread.purpose == "conversation"
        root_compaction = thread.origin != "child" and retain_tools
        background_compaction = root_compaction and resolved.config.background_compaction.enabled
    own, base, tail = await asyncio.gather(
        checkpoints.load_state(runtime.objects, checkpoint),
        checkpoints.load_state(runtime.objects, baseline_checkpoint),
        checkpoints.load_tail(runtime.objects, tail_pointer, hydrate=True),
    )
    if tail.search is None:
        initial_index = await asyncio.to_thread(
            seed, input_text, source_entry_id or lease.run_id, runtime.settings.run_search
        )
        tail.search = initial_index.state
    state, resume = _initial(lease.thread_id, base, pending=pending, answers=answers, history=history)
    consumed = own is not None and own.resume_input_consumed
    resume_input = (
        Offered(lease.run_id, "message", lease.workspace_id, answers.input.model_dump(mode="json"))
        if answers is not None and answers.input is not None and not consumed
        else None
    )
    reminder = None if consumed else inputs.reminder(inputs.mount_changes(previous, mounts))
    if own is not None:
        state = own.harness
        resume = replace(resume, recovery=True) if resume is not None else None
    origin_run_id = lease.run_id if own is not None else baseline_run_id
    if origin_run_id is not None:
        state = await restore_executions(runtime, state, thread_id=lease.thread_id, origin_run_id=origin_run_id)
    if resume is not None:
        resume = resume.remaining(state.message_history)
    compaction = await runtime.compactions.inspect(lease.thread_id) if root_compaction else None
    return _Plan(
        session_id=session_id,
        background_compaction=background_compaction,
        compaction_result=compaction.result if compaction is not None else None,
        retain_tools=retain_tools,
        configuration=options.configuration or RunConfiguration(),
        request_context=RequestContext(options.context.headers if options.context else {}),
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
        envd_sessions=dict(own.envd_sessions) if own is not None else {},
        resume=resume,
        resume_input=resume_input,
        reminder=reminder,
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
    if pending.desktop_control_id and not pending.calls and not pending.approvals:
        return state, None
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

    def incorporated(self, messages: Sequence[ModelMessage]) -> list[str]:
        ids = set(steering_input_ids(messages))
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
        self.envd_sessions = {name: EnvdSessionRecovery(state) for name, state in plan.envd_sessions.items()}
        self.contents = Contents(plan.tail.items, plan.tail.refs)
        assert plan.tail.search is not None
        self.search = Index(plan.tail.search, runtime.settings.run_search)
        worker = runtime.settings.worker
        self.fold = DisplayFold(
            lease.run_id,
            plan.tail,
            attempt=lease.number,
            page_items=worker.page_items,
            page_bytes=worker.page_bytes,
            memory_ids={memory.mount.name: memory.mount.memory_id for memory in plan.host.memories},
        )
        if plan.tail.position.attempt != lease.number:
            self.fold.interrupt(open_tool_calls(plan.state.message_history))
        self.offers = _Offers(plan.assigned)
        self.app_captures = Captures(lease.run_id)
        self.offer_lock = asyncio.Lock()
        self.incoming: asyncio.Task[None] | None = None
        self.recipient = Recipient(
            plan.agent.model.config.characteristics.capabilities,
            primary=any(mount.name == PRIMARY for mount in plan.mounts),
        )
        # The memory cursors the run's history holds context as of; recovery starts from the committed ones.
        self.cursors = MemoryCursors(plan.memory_cursors)
        self.boundaries = Boundaries(self.cursors.snapshot)
        models = {model.key: model for model in plan.agent.models()}
        self.check = CallCheck(runtime, control, self._call_context(), models=models, used=plan.used, limit=plan.limit)
        self.compaction = (
            CompactionRun(
                runtime.compactions,
                lease,
                Selection(
                    plan.agent.model,
                    self._call_context(),
                    plan.principal.id,
                    plan.request_context,
                    plan.configuration,
                    plan.limit,
                ),
            )
            if plan.background_compaction
            else None
        )
        self.usage = UsageBuffer(self.check.calls)
        self.usage_reporter = DeltaReporter(
            runtime.storage, lease.run_id, lease.attempt_id, self.usage, check=self.check.reconcile
        )
        self.yielding = False
        self.desktop_paused = False
        self.desktop_enabled = bool(enabled_tools(plan.agent.config.toolsets, "computer"))
        self.boundaries.wait_for_model = self.desktop_enabled
        self.live: ThreadStream | None = None

    async def run(self) -> None:
        try:
            result = await self._stream()
            if result is None:
                if self.control.stopped.is_set():
                    await self._seal_interrupted(self.control.outcome)
                else:
                    # Another worker resumes instance preparation under the same durable operation.
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

    @asynccontextmanager
    async def _open_resources(self) -> AsyncIterator[Host]:
        runtime, lease, root = self.runtime, self.lease, self.plan.agent
        skill_cache = Cache()
        async with AsyncExitStack() as stack:
            environments = await stack.enter_async_context(
                open_mounts(
                    runtime,
                    lease,
                    self.plan.principal,
                    self.plan.authority,
                    self.plan.mounts,
                    configuration=self.plan.configuration,
                    skill_cache=skill_cache,
                    envd_sessions=self.envd_sessions,
                )
            )
            models = await agent.open_models(
                stack, runtime, root, configuration=self.plan.configuration, request_context=self.plan.request_context
            )
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
                    request_context=self.plan.request_context,
                    configuration=self.plan.configuration,
                    skill_cache=skill_cache,
                    environments=environments,
                    app_captures=self.app_captures,
                )
            )
            yield host

    async def _stream(self) -> HarnessRunResult | None:
        """The Harness result, or None when stop or handoff interrupted Service instance preparation."""
        runtime, lease = self.runtime, self.lease
        root = self.plan.agent
        if self.desktop_enabled:
            await desktop_execution.prepare(runtime, lease, supported=not root.subagents)
        if not root.config.lazy_environment and not await self._prepare_environments():
            return None
        async with resources(lease, self._open_resources()) as host, AsyncExitStack() as stack:
            executable = agent.build(
                root,
                capabilities=lambda node: (
                    [
                        self.boundaries,
                        *([ExecutionFence(runtime, lease)] if self.desktop_enabled else []),
                        *host.capabilities(node, retain_tools=self.plan.retain_tools),
                        *host.memory,
                        *(
                            [DesktopAssistanceCapability(runtime, lease, self.plan.principal)]
                            if enabled_tools(root.config.toolsets, "computer")
                            else []
                        ),
                    ]
                    if node is root
                    else host.capabilities(node)
                ),
                tool_sources=host.sources,
                child_bindings=host.bindings,
                operators=self._operator,
                plugins=runtime.plugins,
                instrumentation=runtime.instrumentation,
                compaction=self.compaction,
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
                Coalescer(
                    self.fold,
                    live,
                    window=runtime.settings.worker.stream_coalesce_seconds,
                    contents=self.contents,
                    app_captures=self.app_captures,
                )
            )

            def freeze_display(_open_calls: frozenset[str]) -> Snapshot:
                output.flush()
                return self._snapshot()

            self.boundaries.freeze_display = freeze_display
            bindings = replace(
                host.bindings(root, self._bindings(agent.model_resolver(root, host.models))),
                producer_observer=output.observe,
                environment=host.environment,
            )
            start = partial(
                executable.stream,
                input_factory=self._assigned_input
                if self.plan.assigned or self.plan.resume_input or self.plan.reminder
                else None,
                previous_state=self.plan.state,
                compaction_result=self.plan.compaction_result,
                # Recovery creates a new writer/attempt, not a serialized same-writer accounting resume.
                resume_usage=False,
                deferred_resume=self.plan.resume,
                tool_recovery=self.plan.tool_recovery,
                bindings=bindings,
                # The call check enforces the run's own request limit across attempts.
                usage_limits=UsageLimits(request_limit=None),
            )
            stream = start()
            await claim.start(runtime, self.lease, harness_run_id=stream.run_id)
            async with stream, asyncio.TaskGroup() as monitors:
                interrupt = monitors.create_task(self._cancel_when_stopped(stream))
                if self.plan.host.execution.steering == "immediate":
                    self.incoming = monitors.create_task(self._receive_input(stream))
                try:
                    async with self.boundaries.process(partial(self._boundary, stream=stream, output=output)):
                        async for item in stream:
                            await self._observe(item, output)
                finally:
                    interrupt.cancel()
                    if self.incoming is not None:
                        self.incoming.cancel()
                        self.incoming = None
        if stream.result is None:
            raise ServiceError("unavailable", "The Harness run ended without a result", {"dependency": "harness"})
        return stream.result

    async def _prepare_environments(self) -> bool:
        """Prepare instances in Service before entering Harness; stop or handoff interrupts the wait."""
        if self.control.stopped.is_set() or self.control.handoff.is_set():
            return False
        if not self.plan.mounts:
            return True
        logger.info("Preparing run environment instances before entering Harness")
        prepared = self.runtime.tasks.start(
            self._prepare_instances(), name=f"prepare-environments-{self.lease.attempt_id}"
        )
        signals = [asyncio.create_task(event.wait()) for event in (self.control.stopped, self.control.handoff)]
        try:
            await asyncio.wait([prepared, *signals], return_when=asyncio.FIRST_COMPLETED)
            if self.control.stopped.is_set() or self.control.handoff.is_set():
                return False
            await prepared
            return True
        finally:
            # The process retains the preparation child while it finishes cancellation cleanup.
            # Run interruption must not wait for that cleanup or cancel it a second time.
            for task in [prepared, *signals]:
                if not task.done() and not task.cancelling():
                    task.cancel()
            await asyncio.gather(*signals, return_exceptions=True)

    async def _prepare_instances(self) -> None:
        readiness = [
            asyncio.create_task(prepare(self.runtime, self.lease, self.plan.principal, self.plan.authority, mount))
            for mount in self.plan.mounts
        ]
        try:
            await asyncio.gather(*readiness)
        finally:
            for task in readiness:
                if not task.done() and not task.cancelling():
                    task.cancel()
            await asyncio.gather(*readiness, return_exceptions=True)

    async def _observe(self, item: HarnessStreamEvent, output: Coalescer) -> None:
        event = item.event if isinstance(item, HarnessEvent) else None
        if isinstance(event, HarnessExtensionEvent) and event.kind == "usage":
            self.usage.report(event.payload)  # Every charge of the run, an inline child run's included.
        if not isinstance(item, HarnessEvent):
            # Checkpoint writes cannot race the terminal display or final commit.
            await self.boundaries.drain()
            output.observe(item)

    async def _boundary(self, staged: Staged, *, stream: HarnessRunStream, output: Coalescer) -> None:
        if staged.at == "model":
            self.offers.requested = True
        steers = await self._commit(
            staged.state,
            cursors=staged.cursors,
            snapshot=staged.display,
            pause_boundary=staged.at == "model" and self.desktop_enabled,
        )
        if self.desktop_paused:
            self.boundaries.stop()
            stream.cancel()
            return
        output.stream.boundary(staged.display.tail.position.sequence, resume_after=staged.display.tail.resume_after)
        if staged.at == "model" and self.control.handoff.is_set():
            # Yield where the request in flight is simply sent again; at a tool boundary recovery would have to
            # treat calls that never ran as unknown effects. Steers just assigned stay with the run.
            self.yielding = True
            self.boundaries.stop()
            stream.cancel()
            return
        # A tool boundary waits for the acknowledgement, so steers pending there join the request after the tools.
        await self._offer(stream, steers)

    async def _commit(
        self,
        state: HarnessState,
        *,
        cursors: dict[str, str | None],
        snapshot: Snapshot,
        pause_boundary: bool = False,
        outcome: Outcome | None = None,
        deferred: JsonValue = None,
    ) -> list[Offered]:
        """Commit a checkpoint and what follows it in one fenced transaction: a completed or waiting outcome seals
        the run; otherwise a bounded batch of compatible pending steers is assigned to it, and returned.
        The caller freezes the matching display before entering this persistence path."""
        snapshot = self.fold.pending(snapshot)
        if near_deadline(self.runtime, self.control):
            raise LeaseLost()
        started = time.monotonic()
        acknowledgement = (
            await compactions.acknowledgement(self.runtime, state, RunObjects(self.runtime, self.lease, self.control))
            if self.plan.background_compaction or self.plan.compaction_result is not None
            else None
        )
        messages = state.message_history
        consumed = self.offers.incorporated(messages)
        del messages
        usage = self.usage.pending()
        search, snapshot = await self._search(snapshot, consumed)
        pointer, display = await checkpoints.publish_checkpoint(
            self.runtime,
            self.lease,
            RunState(
                # The Service never restores accounting from state: every charge is in its usage records.
                harness=without_usage(state),
                seq=self.seq + 1,
                attempt=self.lease.number,
                deferred=deferred,
                envd_sessions={
                    name: recovery.state for name, recovery in self.envd_sessions.items() if recovery.state is not None
                }
                if outcome is None
                else {},
                resume_input_consumed=(self.plan.resume_input is None and self.plan.reminder is None)
                or self.offers.requested,
            ),
            snapshot,
            contents=self.contents,
            control=self.control,
        )
        display = display.model_copy(update={"search_terms": tuple(sorted(search.counts)) if search.changed else None})
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
            await compactions.acknowledge(session, self.runtime, thread.id, acknowledgement, at=current)
            if pause_boundary or (self.desktop_enabled and outcome is not None and outcome.status == "waiting"):
                pending = await desktop_execution.pending(session, run, outcome.pending if outcome else None)
                if pending is not None and pending.desktop_control_id:
                    outcome = Outcome(status="waiting", pending=pending)
            if outcome is not None:
                # Stop input polling before our seal releases the lease. A poll already waiting for
                # this transaction's Thread lock must not mistake our own completion for lease loss.
                if self.incoming is not None:
                    self.incoming.cancel()
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
        if outcome is not None and outcome.pending is not None and outcome.pending.desktop_control_id:
            self.desktop_paused = True
        CHECKPOINT_DURATION.record(time.monotonic() - started)
        self.search = search
        self.committed, self.seq = committed, self.seq + 1
        self.fold.committed(snapshot)
        self.contents.retire([item.id for page in snapshot.pages for item in page.items])
        self.offers.consumed(consumed)
        self.usage.ingested(usage)
        if outcome is not None:
            await advance(self.runtime, self.lease.thread_id)
        return steers

    async def _offer(self, stream: HarnessRunStream, steers: list[Offered]) -> None:
        """Offer the steers assigned at this boundary, and any the Harness could not take before, to the run's next
        request."""
        async with self.offer_lock:
            self.offers.unsent.extend(steers)
            while self.offers.unsent and self.boundaries.accepting_steers:
                entry = self.offers.unsent[0]
                content = await self._read(entry, stream.context.environment)
                if content is None:
                    self.offers.unsent.pop(0)
                    continue
                if not self.boundaries.accepting_steers:
                    return  # Keep the unconsumed entry for continuation after native completion.
                try:
                    await stream.steer(
                        content,
                        input_id=entry.id,
                        immediate=entry.kind == "message" and self.plan.host.execution.steering == "immediate",
                    )
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
        if not parts and (self.plan.assigned or self.plan.resume_input):
            # Each entry was a steer that failed alone; the next attempt continues without them.
            raise ServiceError("unavailable", "No assigned input could be read", {"dependency": "input"})
        return tuple(parts) if self.plan.reminder is None else (self.plan.reminder, *parts)

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

    async def _receive_input(self, stream: HarnessRunStream) -> None:
        """Immediate input uses ordinary mailbox assignment; checkpoints still own incorporation."""
        async with self.runtime.mailbox.listen(self.lease.thread_id) as ready:
            while True:
                await ready.wait()
                ready.clear()
                if not self.boundaries.accepting_steers:
                    continue
                try:
                    async with transaction(self.runtime.storage) as session:
                        thread, run, _, _ = await lock_thread_lease(session, self.lease)
                        await authorize_execution(session, self.runtime.access, run)
                        entries = await inbox.assign_steers(
                            session,
                            thread,
                            run,
                            max_count=self.runtime.settings.worker.delivery_count,
                            max_bytes=self.runtime.settings.worker.delivery_bytes,
                            scan=self.runtime.settings.control.inbox_count,
                        )
                        offers = [Offered.of(entry) for entry in entries]
                except ServiceError as error:
                    if error.code != "unavailable":
                        raise
                    continue
                await self._offer(stream, offers)

    async def _cancel_when_stopped(self, stream: HarnessRunStream) -> None:
        await self.control.stopped.wait()
        stream.cancel()

    async def _end(self, result: HarnessRunResult) -> None:
        if self.desktop_paused:
            return
        self.usage.add(result.usage_records)
        if result.status == "failed":
            assert result.failure is not None
            await self._record_usage()
            failed = Outcome.failed(
                result.failure.code, _harness_failure_message(result.failure.message, result.failure.details)
            )
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
        self.fold.interrupt(open_tool_calls(result.all_messages()))
        snapshot = self._snapshot()
        if result.status == "completed":
            outcome = Outcome(status="completed", output=self._output(result.output))
            await self._commit(result.state, cursors=self.cursors.snapshot(), snapshot=snapshot, outcome=outcome)
        else:
            assert result.deferred is not None
            outcome = Outcome(status="waiting", pending=deferred.pending(result.deferred))
            await self._commit(
                result.state,
                cursors=self.cursors.snapshot(),
                snapshot=snapshot,
                outcome=outcome,
                deferred=deferred.dump(result.deferred),
            )

    async def _seal_interrupted(self, outcome: Outcome) -> None:
        """Seal a failure or cancellation with the display this attempt folded, its unfinished items interrupted."""
        display = None
        self.fold.interrupt()
        if not near_deadline(self.runtime, self.control):
            snapshot = self.fold.pending(self._snapshot())
            # Only user sources actually visible in this display join a failed/cancelled run's index.
            visible = {
                str(metadata["source_id"])
                for item in (*[item for page in snapshot.pages for item in page.items], *snapshot.tail.items)
                if isinstance(metadata := item.content.get("metadata"), dict) and "source_id" in metadata
            }
            search, snapshot = await self._search(snapshot, visible)
            display = await checkpoints.publish_display(
                self.runtime, self.lease, snapshot, contents=self.contents, control=self.control
            )
            display = display.model_copy(
                update={"search_terms": tuple(sorted(search.counts)) if search.changed else None}
            )
        await seal_attempt(self.runtime, self.lease, outcome, display=display)

    async def _search(self, snapshot: Snapshot, consumed: Iterable[str]) -> tuple[Index, Snapshot]:
        inputs = [
            (entry.id, MessagePayload.model_validate(entry.payload).text())
            for key in consumed
            if (entry := self.offers.open.get(key)) is not None and entry.kind == "message"
        ]
        items = [item for page in snapshot.pages for item in page.items] + snapshot.tail.items
        search = await asyncio.to_thread(self.search.prepare, items, inputs)
        return search, Snapshot(snapshot.pages, snapshot.tail.model_copy(update={"search": search.state}))

    def _snapshot(self) -> Snapshot:
        """The display to commit, with a safe resume hint captured without waiting for queued Redis writes.

        The writer replaces one immutable value on the same event loop. Terminal callers read it after close.
        """
        snapshot = self.fold.snapshot()
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
            tool_execution=self.runtime.executions if self.plan.retain_tools else None,
            codeact_state=SharedCodeActState(self.runtime.storage, lease.thread_id),
            configuration=self.plan.configuration,
            request_context=self.plan.request_context,
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
