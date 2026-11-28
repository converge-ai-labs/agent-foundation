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
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, replace
from functools import partial
from typing import Literal, cast
from uuid import uuid4

import anyio
from a13n_harness import (
    AgentContext,
    DeferredToolResume,
    ExecutableAgent,
    HarnessError,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessRunResult,
    HarnessRunStream,
    HarnessState,
    HarnessStreamEvent,
    PostRunSession,
    RequestContext,
    RunBindings,
    RunConfiguration,
    RunError,
    RunModelResolver,
    RunPreparationContext,
)
from a13n_harness.capabilities import MemoryCursors
from a13n_harness.capabilities.steering import steering_input_ids, without_input
from a13n_harness.compaction import CompactionResult
from a13n_harness.content import request_input_content
from a13n_harness.environment.errors import EnvironmentProviderError, EnvironmentProviderErrorCategory
from a13n_harness.environment.models import EnvironmentInvocationError
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.identity import AgentIdentityRef, AgentInstanceContext
from a13n_harness.live import HarnessLiveStream, LiveWebRTC
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from a13n_harness.recovery import normalize_interrupted_history
from a13n_harness.state import encode_messages
from a13n_harness.tool_execution import RetainedScope
from a13n_harness.usage import without_usage
from a13n_logging import exception_details, get_logger
from pydantic import JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import HandleDeferredToolCalls
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    BinaryContent,
    EnqueuedMessagesEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RealtimeInputSpeechStartEvent,
    RealtimeOutputSpeechStartEvent,
    RealtimeResponseInterruptedEvent,
    RealtimeSessionReconnectEvent,
    RealtimeTurnCompleteEvent,
    SpeechPart,
    SpeechPartDelta,
    TextContent,
    ToolReturnPart,
    UserContent,
)
from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.realtime import RealtimeModelSettings
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from pydantic_ai.usage import UsageLimits
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.app_plugins import AppActivation
from a13n_service.app_sdk.hooks import EnvironmentHookAbort
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError, invalid
from a13n_service.infra.telemetry import attempt_observation
from a13n_service.providers.realtime import PROTOCOLS
from a13n_service.resources.agents import stored
from a13n_service.resources.assets.service import read_asset_content
from a13n_service.resources.models.service import resolve_model
from a13n_service.runs import agent, checkpoints, claim, compactions, deferred, definitions, inbox, inputs, placement
from a13n_service.runs.accept import advance, delegation
from a13n_service.runs.admission import (
    CallContext,
    RealtimeBudgetContext,
    RealtimeBudgetPolicy,
    requires_model_reservation,
)
from a13n_service.runs.agent import ResolvedAgent
from a13n_service.runs.attachments import Recipient
from a13n_service.runs.attempts import (
    AttemptControl,
    AuthorityRevoked,
    Lease,
    LeaseLost,
    authorize_execution,
    lock_thread_lease,
    prove,
)
from a13n_service.runs.boundaries import Boundaries, InputBoundary, Staged, close_realtime_tool
from a13n_service.runs.calls import CallCheck
from a13n_service.runs.checkpoints import CHECKPOINT_DURATION, Committed, RunObjects, RunState, near_deadline
from a13n_service.runs.child_messages import ParentMessageCapability
from a13n_service.runs.coalesce import Coalescer
from a13n_service.runs.compaction_model import CompactionRun, Selection
from a13n_service.runs.contents import Contents
from a13n_service.runs.display import DisplayFold, Snapshot, Tail, open_tool_calls
from a13n_service.runs.dynamic_subagents import SpawnCapability
from a13n_service.runs.environments.clients.recovery import Recovery as ClientRecovery
from a13n_service.runs.environments.execution import EnvironmentUnavailable, Source, open_mounts, prepare
from a13n_service.runs.environments.lifecycle import public_message
from a13n_service.runs.environments.mounts import PRIMARY
from a13n_service.runs.history import HISTORY, MessageHistory, initial
from a13n_service.runs.host import Host, HostPlan, open_host, resolve_host
from a13n_service.runs.inputs import Offered
from a13n_service.runs.mcp_apps.capture import Captures
from a13n_service.runs.notices import KINDS as NOTICE_KINDS
from a13n_service.runs.notices import StaleNotice, require_notices
from a13n_service.runs.realtime import (
    BUDGET_RESTRICTION,
    INBOX_METADATA,
    Channel,
    Media,
    MessageInput,
    validate_realtime_image,
    validate_realtime_image_count,
    worker_channel,
)
from a13n_service.runs.realtime_cleanup import provider_call
from a13n_service.runs.realtime_transcript import LiveTranscripts
from a13n_service.runs.realtime_transport import ATTACH_SECONDS, TransportError
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import (
    EnvironmentMount,
    Failure,
    MessagePayload,
    Outcome,
    Pending,
    Resume,
    RunOptions,
    require_output_size,
)
from a13n_service.runs.seal import release_attempt, seal, seal_attempt
from a13n_service.runs.search import Index, seed
from a13n_service.runs.skill_cache import Cache
from a13n_service.runs.stream import ThreadStream
from a13n_service.runs.subagents import ChildRuns
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.runs.tool_executions.owner import resources
from a13n_service.runs.tool_executions.recovery import acknowledge as acknowledge_executions
from a13n_service.runs.tool_executions.recovery import restore as restore_executions
from a13n_service.runs.tool_executions.state import SharedCodeActState
from a13n_service.runs.usage import DeltaReporter, UsageBuffer, ingest_late, price_snapshot, totals
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

_IMAGE_PROMPT = "Look at the attached image(s)."

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Plan:
    """What an attempt runs and where it continues from, read and restored before anything is delivered."""

    session_id: str
    app_activation: AppActivation
    app_id: str | None
    retain_tools: bool
    subagent_controls: bool
    child_messages: bool
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
    obsolete_notice: bool = False


async def execute(runtime: Runtime, lease: Lease, control: AttemptControl) -> None:
    """The worker's execution of one leased attempt. Without the lease nothing is written: `LeaseLost` escapes."""
    async with AsyncExitStack() as stack:
        channel = None
        try:
            if lease.realtime:
                channel = await stack.enter_async_context(worker_channel(runtime, lease))
            plan = await _plan(runtime, lease)
        except LeaseLost:
            raise
        except Exception as error:
            outcome = _failure(lease, error)
            if outcome is None and lease.realtime:
                outcome = Outcome.failed("realtime_unavailable", "The realtime attempt could not start")
            if outcome is None:
                await _release(runtime, lease, error)
            else:
                await seal_attempt(runtime, lease, outcome)
            if channel is not None and outcome is not None:
                channel.finish(lease.run_id, outcome.status, outcome.failure.code if outcome.failure else None)
            return
        await _Attempt(runtime, lease, control, plan, channel=channel).run()
        if channel is not None:
            async with short_session(runtime.storage) as session:
                row = await session.get(RunRow, lease.run_id)
                if row is None or row.status not in {"completed", "cancelled", "failed", "waiting"}:
                    return
                run_id, status = row.id, row.status
                code = Failure.model_validate(row.failure).code if row.failure is not None else None
            channel.finish(run_id, status, code)


def _realtime_activity(channel: Channel, event: object) -> None:
    if isinstance(event, FunctionToolCallEvent):
        channel.foreground.add(event.part.tool_call_id)
    elif isinstance(event, FunctionToolResultEvent):
        channel.foreground.discard(event.part.tool_call_id)
    elif not (
        isinstance(event, (RealtimeInputSpeechStartEvent, RealtimeOutputSpeechStartEvent))
        or (isinstance(event, PartDeltaEvent) and isinstance(event.delta, SpeechPartDelta))
        or (isinstance(event, (PartStartEvent, PartEndEvent)) and isinstance(event.part, SpeechPart))
    ):
        return
    channel.touch()


async def _watch_realtime_limits(channel: Channel, start: float, duration: int, idle: int) -> None:
    """Watch wall-clock and semantic idle deadlines independently of media traffic."""
    loop = asyncio.get_running_loop()
    expires = start + duration
    warn_at = expires - 60 if duration > 60 else None
    while not channel.closed.is_set():
        channel.activity.clear()
        now = loop.time()
        if now >= expires:
            channel.end_for_limit("duration_limit")
            return
        idle_at = channel.last_activity + idle if idle and not (channel.foreground or channel.approvals) else expires
        if now >= idle_at:
            channel.end_for_limit("idle_timeout")
            return
        if warn_at is not None and now >= warn_at:
            channel.emit({"type": "limit_warning", "reason": "duration_limit", "remaining_seconds": 60})
            warn_at = None
        try:
            async with asyncio.timeout_at(min(expires, idle_at, warn_at if warn_at is not None else expires)):
                await channel.activity.wait()
        except TimeoutError:
            pass


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
    # Harness suppresses arbitrary extension exceptions. Recover only the explicitly safe App abort.
    if (
        isinstance(current, EnvironmentInvocationError)
        and current.code == "environment_extension_bind_failed"
        and isinstance(current.__context__, EnvironmentHookAbort)
    ):
        return current.__context__
    return (
        current
        if isinstance(
            current, ServiceError | LeaseLost | EnvironmentUnavailable | EndpointPolicyError | EnvironmentHookAbort
        )
        else error
    )


def _deterministic(error: Exception) -> Outcome | None:
    if isinstance(error, EnvironmentHookAbort):
        return Outcome.failed(error.code, error.message)
    if isinstance(error, AuthorityRevoked):
        return error.outcome
    if isinstance(error, TransportError):
        return Outcome.failed("realtime_transport_failed", error.message)
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
        options = stored.require(RunOptions, run.options, kind="run", id=run.id)
        app_id = run.app_id
        app_activation = stored.require(AppActivation, run.app_activation, kind="run", id=run.id)
        revision = await definitions.resolve(
            session, thread, run.agent_id, run.agent_revision_id, options.overrides, accepting=False
        )
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
        child_messages = thread.origin == "child"
        subagent_controls = (
            await session.scalar(
                select(ThreadRow.id)
                .where(ThreadRow.origin_thread_id == thread.id, ThreadRow.origin == "child")
                .limit(1)
            )
            is not None
        )
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
        obsolete_notice = (
            not assigned
            and source is not None
            and source.kind in NOTICE_KINDS
            and source.status == "failed"
            and source.failure is not None
            and source.failure.get("code") in {"browser_notice_stale", "desktop_notice_stale"}
        )
        if obsolete_notice:
            # A corrected first cut may already contain other incorporated input which
            # still needs its model response. Only a notice-only Run completes as a no-op.
            obsolete_notice = not await session.scalar(
                select(
                    select(InboxEntryRow.id)
                    .where(
                        InboxEntryRow.assigned_run_id == run.id,
                        InboxEntryRow.status == "consumed",
                    )
                    .exists()
                )
            )
        input_text = (
            MessagePayload.model_validate(source.payload).text()
            if not lease.realtime and source is not None and source.kind == "message"
            else answers.input.text()
            if answers is not None and answers.input is not None
            else ""
        )
        mounts = tuple(EnvironmentMount.model_validate(mount) for mount in run.environment_mounts)
        memory_cursors = dict(run.memory_cursors)
        # Space organization keeps its memory-only surface and completes calls within the round.
        retain_tools = thread.purpose == "conversation"
        root_compaction = thread.origin != "child" and thread.agent_snapshot is None and retain_tools
        background_compaction = root_compaction and resolved.config.background_compaction.enabled
    app_activation.require_installed(runtime.apps)
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
        app_activation=app_activation,
        app_id=app_id,
        background_compaction=background_compaction,
        compaction_result=compaction.result if compaction is not None else None,
        retain_tools=retain_tools,
        subagent_controls=subagent_controls,
        child_messages=child_messages,
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
        resume=resume,
        resume_input=resume_input,
        reminder=reminder,
        tool_recovery="never" if unknown_outcomes else "declared",
        tail=tail,
        committed=committed,
        seq=own.seq if own is not None else 0,
        obsolete_notice=obsolete_notice,
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
        if any(call.terminal for call in pending.calls):
            messages, _ = normalize_interrupted_history(state.message_history, close_pending_tools=True)
            return state.model_copy(update={"message_history_json": encode_messages(messages)}), None
        answers = deferred.fork_results(pending)
    if answers is None:
        raise ValueError("A waiting parent requires explicit resume results")
    return state, deferred.resume(deferred.load(base.deferred), answers)


class _Offers:
    """Entries offered to this attempt's Harness run, until a checkpoint commit consumes them."""

    def __init__(self, initial: list[Offered], *, realtime: bool = False):
        self.realtime = realtime
        self.initial = {entry.id for entry in initial}
        self.open = {entry.id: entry for entry in initial}
        # Assigned steers the Harness could not take yet: between its native attempts none is active.
        self.unsent: list[Offered] = []
        # Whether a model request carried the input: it is in every state exported after that.
        self.requested = False

    def incorporated(self, messages: Sequence[ModelMessage]) -> list[str]:
        ids = set(steering_input_ids(messages))
        if self.realtime:
            ids.update(
                item.metadata.source_id
                for message in messages
                if isinstance(message, ModelRequest)
                for item in request_input_content(message)
                if item.metadata.source_id and (item.metadata.model_extra or {}).get(INBOX_METADATA) is True
            )
        if self.requested:
            ids |= self.initial
        return [entry_id for entry_id in self.open if entry_id in ids]

    def pending_notice(self, messages: list[ModelMessage]) -> bool:
        ids = set(steering_input_ids(messages)) | self.initial
        return any(entry.kind in NOTICE_KINDS and entry.id in ids for entry in self.open.values())

    def consumed(self, entry_ids: list[str]) -> None:
        for entry_id in entry_ids:
            del self.open[entry_id]


def _post_seal_model_resolver(
    runtime: Runtime, call: CallContext, root: ResolvedAgent, models: Mapping[str, Model]
) -> RunModelResolver:
    selected = {model.key: model for model in root.models()}
    resolve = agent.model_resolver(root, models)

    async def authorized(context: ModelResolutionContext[AgentContext], model_id: str) -> Model:
        model = selected[model_id]
        async with short_session(runtime.storage) as session:
            run = await session.get(RunRow, call.run_id)
            if run is None or run.cancel_requested_at is not None:
                raise AuthorityRevoked()
            principal = await authorize_execution(session, runtime.access, run)
            current = await resolve_model(
                session,
                principal,
                WorkspaceScope(run.organization_id, run.workspace_id),
                model_id,
                authority=ExecutionAuthority.model_validate(run.authority),
            )
            if (current.id, current.provider.id) != (model.id, model.provider.id):
                raise AuthorityRevoked()
            if runtime.admission is not None:
                await runtime.admission.proceed(
                    session,
                    replace(
                        call,
                        call_id=f"call_{uuid4().hex}",
                        source="plugin_post_run",
                        provider_id=model.provider.id,
                        model_id=model.id,
                        price_snapshot=price_snapshot(model),
                    ),
                )
        return await resolve(context, model_id)

    return authorized


class _Attempt:
    """One Harness run of the attempt: the display it folds, the input it offered and what it committed."""

    def __init__(
        self, runtime: Runtime, lease: Lease, control: AttemptControl, plan: _Plan, *, channel: Channel | None = None
    ):
        self.channel = channel
        self.runtime, self.lease, self.control, self.plan = runtime, lease, control, plan
        self.committed, self.seq = plan.committed, plan.seq
        self.last_committed_tail_bytes: bytes | None = None
        self.contents = Contents(plan.tail.items, plan.tail.refs)
        assert plan.tail.search is not None
        self.search = Index(plan.tail.search, runtime.settings.run_search)
        worker = runtime.settings.worker
        self.realtime_protocol = PROTOCOLS.get(plan.agent.model.config.model_api)
        self.fold = DisplayFold(
            lease.run_id,
            plan.tail,
            attempt=lease.number,
            page_items=worker.page_items,
            page_bytes=worker.page_bytes,
            memory_ids={memory.mount.name: memory.mount.memory_id for memory in plan.host.memories},
            group_live_user_speech=bool(self.realtime_protocol and self.realtime_protocol.group_user_speech),
        )
        if plan.tail.position.attempt != lease.number:
            self.fold.interrupt(open_tool_calls(plan.state.message_history))
        self.offers = _Offers(plan.assigned, realtime=lease.realtime)
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
        self.client_recovery: ClientRecovery | None = None
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
        self.boundaries.wait_for_model = any(entry.kind in NOTICE_KINDS for entry in plan.assigned)
        self.live: ThreadStream | None = None

    async def run(self) -> None:
        try:
            if self.plan.obsolete_notice:
                # A notification-only continuation lost its source before its first checkpoint.
                # Preserve inherited history and complete without a model call or a Thread pause.
                await self._commit(
                    self.plan.state,
                    cursors=self.cursors.snapshot(),
                    snapshot=self._snapshot(),
                    outcome=Outcome(status="completed"),
                )
                return
            mode = self.plan.agent.config.lazy_environment
            if mode == "warmup" and any(
                node.config.toolsets[key].enabled
                for node in self.plan.agent.agents()
                for key in ("files", "shell", "computer")
            ):
                for mount in self.plan.mounts:
                    self.runtime.tasks.start(
                        prepare(self.runtime, self.lease, self.plan.principal, self.plan.authority, mount, warmup=True),
                        name=f"warmup-environment-{self.lease.attempt_id}-{mount.name}",
                    )
            if (mode is False or mode == "eager") and not await self._prepare_environments():
                if self.control.stopped.is_set():
                    await self._seal_interrupted(self.control.outcome)
                else:
                    # Another worker resumes instance preparation under the same durable operation.
                    await release_attempt(self.runtime, self.lease, status="yielded", yield_reason="handoff")
                return
            model_scope = RetainedScope(self._open_models())
            try:
                models = await model_scope.ready()
                result, post_run = await self._stream(models, model_scope)
                await self._end(result)
                if post_run is not None:
                    resolve = _post_seal_model_resolver(self.runtime, self._call_context(), self.plan.agent, models)

                    self.runtime.start_post_run(
                        self.lease.run_id, model_scope, partial(post_run.run, model_resolver=resolve)
                    )
            finally:
                await model_scope.close()
        except (LeaseLost, asyncio.CancelledError):
            # Neither seals the run, but its charges so far stay recorded, even while the task is cancelled.
            await self._record_usage()
            raise
        except Exception as wrapped:
            await self._record_usage()
            error = _own(wrapped)
            if isinstance(error, LeaseLost):
                raise error from None
            if isinstance(error, StaleNotice):
                # Input-read rejection has no new work. Checkpoint rejection has already saved
                # the same cut without the notice, including completed tools and child state.
                async with transaction(self.runtime.storage) as session:
                    _, run, _, current = await lock_thread_lease(session, self.lease)
                    await inbox.fail_assigned(session, run.id, error.entry_id, Failure.of(error), at=current)
                await release_attempt(self.runtime, self.lease, status="yielded", yield_reason="handoff")
                return
            if isinstance(error, AuthorityRevoked):
                outcome = _failure(self.lease, error)
            elif self.channel is not None and self.channel.end_reason == "usage_limit_exceeded":
                outcome = self._budget_failure()
            else:
                outcome = self._refusal() or _failure(self.lease, error)
            if outcome is None and self.channel is not None:
                outcome = Outcome.failed("realtime_unavailable", "The realtime attempt ended before completing")
            if outcome is None:
                await _release(self.runtime, self.lease, error)
            else:
                await self._seal_interrupted(outcome)

    @asynccontextmanager
    async def _open_models(self) -> AsyncIterator[dict[str, Model]]:
        async with AsyncExitStack() as stack:
            yield await agent.open_models(
                stack,
                self.runtime,
                self.plan.agent,
                configuration=self.plan.configuration,
                request_context=self.plan.request_context,
            )

    @asynccontextmanager
    async def _open_resources(
        self,
        models: Mapping[str, Model],
        model_scope: RetainedScope[dict[str, Model]],
    ) -> AsyncIterator[Host]:
        runtime, lease = self.runtime, self.lease
        skill_cache = Cache()
        async with AsyncExitStack() as stack:
            stack.push_async_callback(model_scope.retain())
            environments = await stack.enter_async_context(
                open_mounts(
                    runtime,
                    lease,
                    self.plan.principal,
                    self.plan.authority,
                    self.plan.mounts,
                    configuration=self.plan.configuration,
                    skill_cache=skill_cache,
                    defer_bootstrap=self.plan.agent.config.lazy_environment not in (False, "eager"),
                )
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
                    app_activation=self.plan.app_activation,
                    owner_app_id=self.plan.app_id,
                )
            )
            recovery = ClientRecovery(
                runtime,
                lease,
                host.environment,
                environments,
                {
                    name: mount.source.session_recovery
                    for name, mount in environments.items()
                    if isinstance(mount.source, Source) and mount.source.session_recovery is not None
                },
            )
            self.client_recovery = recovery if recovery.mounts else None
            yield host

    async def _stream(
        self,
        models: dict[str, Model],
        model_scope: RetainedScope[dict[str, Model]],
    ) -> tuple[HarnessRunResult, PostRunSession | None]:
        runtime, lease = self.runtime, self.lease
        root = self.plan.agent
        async with (
            resources(lease, self._open_resources(models, model_scope)) as host,
            AsyncExitStack() as stack,
        ):
            executable = agent.build(
                root,
                capabilities=lambda node: (
                    [
                        self.boundaries,
                        *([HandleDeferredToolCalls(handler=self._realtime_approval)] if self.channel else []),
                        *([self.client_recovery] if self.client_recovery is not None else []),
                        InputBoundary(self.boundaries, self.offers.pending_notice),
                        *(
                            [ParentMessageCapability(runtime, lease)]
                            if self.plan.child_messages
                            and not any(tool.name == "send_parent_message" for tool in node.config.client_tools)
                            else []
                        ),
                        *host.capabilities(node, retain_tools=self.plan.retain_tools),
                        *(
                            [
                                SpawnCapability(
                                    self._operator(node).spawn, node.config, realtime_parent=self.lease.realtime
                                )
                            ]
                            if node.revision_id is not None
                            and node.config.subagents.dynamic.enabled
                            and self.plan.root_run_id == self.lease.run_id
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
                app_activation=self.plan.app_activation,
                apps=runtime.apps,
                instrumentation=runtime.instrumentation,
                compaction=self.compaction,
                subagent_controls=self.plan.subagent_controls,
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
            if self.realtime_protocol is not None:
                return await self._realtime(executable, bindings, output), None
            if self.lease.realtime:
                raise ServiceError("conflict", "The Agent's primary model no longer uses a realtime API")
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
        return stream.result, stream.post_run

    async def _realtime(
        self, executable: ExecutableAgent, bindings: RunBindings, output: Coalescer
    ) -> HarnessRunResult:
        channel = self.channel
        if (
            channel is None
            or channel.attached
            or channel.closed.is_set()
            or channel.principal_id != self.plan.principal.id
            or channel.workspace_id != self.lease.workspace_id
        ):
            raise ServiceError("conflict", "The realtime connection is no longer available")
        if self.plan.resume is not None or self.lease.number != 1:
            raise ServiceError("conflict", "Start a new browser connection to continue this realtime Thread")
        if requires_model_reservation(self.runtime.admission):
            raise ServiceError("conflict", BUDGET_RESTRICTION)
        channel.attached = True
        await prove(self.runtime.storage, self.lease)
        protocol = self.realtime_protocol
        assert protocol is not None
        transcripts = LiveTranscripts(group_user_speech=protocol.group_user_speech)
        webrtc = (
            LiveWebRTC(
                channel.webrtc_offer,
                channel.answer_webrtc,
                partial(provider_call, self.runtime, self.lease, self.plan.agent.model),
            )
            if channel.webrtc_offer is not None
            else None
        )
        settings = (protocol.audio_defaults if channel.audio else protocol.text_defaults).copy()
        settings.update(
            cast(
                RealtimeModelSettings,
                {**self.plan.agent.model.config.defaults(), **self.plan.agent.config.model_settings},
            )
        )
        self.boundaries.before_dispatch = channel.verify_dispatch
        duration = self.runtime.settings.worker.realtime_max_duration_seconds
        duration = min(duration, channel.max_duration_seconds or duration)
        async with (
            agent.open_realtime(
                self.runtime,
                self.plan.agent,
                executable,
                bindings=replace(bindings, model_call_check=None),
                usage_limits=UsageLimits(request_limit=self.plan.limit),
                previous_state=self.plan.state,
                model_settings=settings,
                webrtc=webrtc,
            ) as stream,
            AsyncExitStack() as stack,
        ):
            await claim.start(self.runtime, self.lease, harness_run_id=stream.run_id)
            self.boundaries.primary = stream.run_id
            await stack.enter_async_context(
                self.boundaries.process(partial(self._boundary, stream=stream, output=output))
            )
            try:
                async with asyncio.timeout(ATTACH_SECONDS):
                    await stack.enter_async_context(stream)
                    self.offers.requested = True
                    part_audio = webrtc is None and stream.relay_part_audio(channel.emit)
                    pcm = stream.stream_audio() if webrtc is None and not part_audio else None
                    opening = await self._realtime_notices(stream, channel)
                    opening_id = None
                    images = await self._realtime_images(channel.draft_asset_ids, field="draft_asset_ids")
                    if opening:
                        if channel.draft:
                            await stream.recall(channel.draft)
                        if images:
                            # The enqueued prompt is the only responding turn. Preserve its Inbox metadata.
                            await stream.send(images, respond=False)
                        if channel.draft or images:
                            opening.append(
                                TextContent(
                                    channel.draft or _IMAGE_PROMPT,
                                    metadata={"a13n.steering-source": "user", "display": False},
                                )
                            )
                        opening_id = stream.enqueue_notice(opening)
                    elif images:
                        await self._send_realtime_images(stream, channel.draft, images, respond=True)
                    elif channel.draft:
                        await stream.send(channel.draft, respond=True)
                    if channel.draft or channel.draft_asset_ids:
                        output.realtime_input(channel.draft, channel.draft_asset_ids)
            except TimeoutError:
                raise TransportError("The realtime provider did not become ready") from None
            tasks = await stack.enter_async_context(asyncio.TaskGroup())
            started = asyncio.get_running_loop().time()
            channel.touch()
            limits = tasks.create_task(
                _watch_realtime_limits(channel, started, duration, self.runtime.settings.worker.realtime_idle_seconds)
            )
            profile = protocol.for_model(self.plan.agent.model.config.model_name)
            ready: dict[str, str | int | bool] = {
                "type": "ready",
                "accepts_text": True,
                "supports_interruption": stream.supports_interruption,
                "audio_input_hz": profile.audio_input_hz,
                "audio_output_hz": profile.audio_output_hz,
            }
            if webrtc is None:
                ready["audio_hz"] = profile.audio_output_hz
            else:
                ready["transport"] = "webrtc"

            audio: asyncio.Task[None] | None = None

            def start_input() -> asyncio.Task[None]:
                nonlocal audio
                channel.touch()
                channel.emit(ready)
                if pcm is not None:
                    audio = tasks.create_task(self._realtime_audio(pcm, channel))
                return tasks.create_task(self._realtime_input(stream, channel, output, tasks))

            receive = start_input() if opening_id is None and not channel.closed.is_set() else None
            stop = tasks.create_task(self._close_realtime(stream, channel, lambda: receive))
            notices = tasks.create_task(self._receive_realtime_notices(stream, channel))
            interrupted_approvals: set[str] = set()
            try:
                async for item in stream:
                    if isinstance(item, HarnessEvent) and item.run_id == stream.run_id:
                        _realtime_activity(channel, item.event)
                    await self._observe(item, output)
                    if isinstance(item, HarnessEvent) and item.run_id == stream.run_id:
                        if (
                            receive is None
                            and isinstance(item.event, EnqueuedMessagesEvent)
                            and item.event.enqueue_id == opening_id
                            and not channel.closed.is_set()
                        ):
                            receive = start_input()
                        if (
                            webrtc
                            and isinstance(item.event, RealtimeOutputSpeechStartEvent)
                            and not channel.closed.is_set()
                        ):
                            channel.emit({"type": "playback_start"})
                        if (
                            isinstance(item.event, (RealtimeInputSpeechStartEvent, RealtimeResponseInterruptedEvent))
                            and not channel.closed.is_set()
                        ):
                            channel.emit({"type": "barge_in"})
                        # The provider settles lost tool calls before reporting reconnect. Keep
                        # their IDs so a prompt from the new connection is not cleared with them.
                        if (
                            isinstance(item.event, FunctionToolResultEvent)
                            and isinstance(item.event.part, ToolReturnPart)
                            and item.event.part.outcome == "interrupted"
                            and item.event.part.tool_call_id in channel.decisions
                            and channel.decisions[item.event.part.tool_call_id] is None
                        ):
                            interrupted_approvals.add(item.event.part.tool_call_id)
                        if isinstance(item.event, RealtimeSessionReconnectEvent) and not item.event.state_restored:
                            if not channel.closed.is_set():
                                for call_id in interrupted_approvals:
                                    channel.emit({"type": "approval_expired", "call_id": call_id})
                            interrupted_approvals.clear()
                        if isinstance(item.event, RealtimeTurnCompleteEvent):
                            channel.flush_transcripts()
                        if isinstance(item.event, (RealtimeTurnCompleteEvent, EnqueuedMessagesEvent)):
                            await self.boundaries.stage_live(stream.export_state)
                        if isinstance(item.event, RealtimeTurnCompleteEvent):
                            await self._check_realtime_budget(channel)
                        if not channel.closed.is_set() and (transcript := transcripts.observe(item)) is not None:
                            channel.transcript(transcript)
            finally:
                if receive is not None:
                    receive.cancel()
                limits.cancel()
                stop.cancel()
                notices.cancel()
                if audio is not None and channel.closed.is_set():
                    audio.cancel()
                # Overflow already marks the channel failed. Keep any cancellation or lease loss in flight.
                with suppress(TransportError):
                    channel.flush_transcripts()
        if (channel.failed and channel.end_reason != "usage_limit_exceeded") or stream.result is None:
            if channel.failed:
                raise TransportError("The realtime connection failed")
            raise ServiceError("unavailable", "The realtime run ended before it completed")
        return stream.result

    async def _check_realtime_budget(self, channel: Channel) -> None:
        policy = self.runtime.admission
        if channel.end_reason == "usage_limit_exceeded" or not isinstance(policy, RealtimeBudgetPolicy):
            return
        # Harness attributes the native turn before publishing its event; reports may be retried by flush.
        await self.usage_reporter.flush()
        context = RealtimeBudgetContext(
            self.lease.organization_id, self.lease.workspace_id, self.lease.run_id, self.lease.attempt_id
        )
        try:
            async with transaction(self.runtime.storage) as session:
                await policy.check_realtime_budget(session, context)
        except (ServiceError, UsageLimitExceeded):
            channel.end_for_limit("usage_limit_exceeded")

    async def _receive_realtime_notices(self, stream: HarnessLiveStream, channel: Channel) -> None:
        """Assign durable completion notices and consume them only after native delivery."""
        async with self.runtime.mailbox.listen(self.lease.thread_id, realtime=True) as ready:
            while not channel.closed.is_set():
                await ready.wait()
                ready.clear()
                try:
                    texts = await self._realtime_notices(stream, channel)
                    if texts:
                        stream.enqueue_notice(texts)
                except ServiceError as error:
                    if error.code != "unavailable":
                        raise
                    continue  # The next mailbox scan retries the still-pending rows.

    async def _realtime_notices(self, stream: HarnessLiveStream, channel: Channel) -> list[TextContent]:
        async with transaction(self.runtime.storage) as session:
            thread, run, _, _ = await lock_thread_lease(session, self.lease)
            await authorize_execution(session, self.runtime.access, run)
            rows = await inbox.assign_steers(
                session,
                thread,
                run,
                max_count=self.runtime.settings.worker.delivery_count,
                max_bytes=self.runtime.settings.worker.delivery_bytes,
                scan=self.runtime.settings.control.inbox_count,
                kinds=inbox.BACKGROUND_NOTICES,
            )
            entries = [Offered.of(row) for row in rows]
        texts = []
        for entry in entries:
            content = await self._read(entry, stream.context.environment)
            if content and not channel.closed.is_set():
                source = "tool_execution" if entry.kind == "tool_result" else "async_subagent"
                texts.extend(
                    replace(
                        part,
                        metadata={
                            **(part.metadata or {}),
                            INBOX_METADATA: True,
                            "display": False,
                            "a13n.steering-source": source,
                        },
                    )
                    for part in content
                    if isinstance(part, TextContent)
                )
                self.offers.open[entry.id] = entry
        return texts

    async def _realtime_approval(self, ctx: RunContext, requests: DeferredToolRequests) -> DeferredToolResults | None:
        channel = self.channel
        if channel is None or ctx.run_id != self.boundaries.primary or not requests.approvals:
            return None
        results = DeferredToolResults()
        for call in requests.approvals:
            decision = await channel.approve(call.tool_call_id, call.tool_name, call.args_as_json_str())
            if decision is None:
                await close_realtime_tool(ctx)
            results.approvals[call.tool_call_id] = decision
        return results

    async def _realtime_images(self, asset_ids: tuple[str, ...], *, field: str) -> list[BinaryContent]:
        if not asset_ids:
            return []
        protocol = self.realtime_protocol
        assert protocol is not None
        profile = protocol.for_model(self.plan.agent.model.config.model_name)
        validate_realtime_image_count(asset_ids, profile.max_images_per_input, field=field)
        storage, objects = self.runtime.storage, self.runtime.objects
        principal, workspace_id = self.plan.principal, self.lease.workspace_id
        images: list[BinaryContent] = []
        for index, asset_id in enumerate(asset_ids):
            path = f"{field}.{index}"
            try:
                asset, data = await read_asset_content(
                    storage,
                    objects,
                    principal,
                    workspace_id,
                    asset_id,
                    before_read=partial(
                        validate_realtime_image, supported_types=profile.supported_image_types, field=path
                    ),
                )
            except ServiceError as error:
                if error.code in {"not_found", "forbidden", "unavailable"}:
                    raise invalid(path, "Choose an available image asset from this workspace") from None
                raise
            images.append(BinaryContent(data, media_type=asset.content_type, identifier=asset_id))
        return images

    async def _send_realtime_images(
        self, stream: HarnessLiveStream, text: str, images: list[BinaryContent], *, respond: bool
    ) -> None:
        protocol = self.realtime_protocol
        assert protocol is not None
        profile = protocol.model_profile(self.plan.agent.model.config.model_name)
        prompt = [text] if text.strip() else []
        if respond and not profile.get("supports_manual_turn_control"):
            # Automatic-VAD models reply to a committed text turn, not to a video frame.
            await stream.send([*images, *(prompt or [_IMAGE_PROMPT])], respond=True)
        else:
            await stream.send([*prompt, *images], respond=respond)

    async def _realtime_input(
        self, stream: HarnessLiveStream, channel: Channel, output: Coalescer, tasks: asyncio.TaskGroup
    ) -> None:
        async def resume_playback() -> None:
            await stream.wait_for_reply()
            if not channel.closed.is_set():
                channel.emit({"type": "playback_end"})

        resume: asyncio.Task[None] | None = None
        while True:
            payload = await channel.incoming.get()
            if channel.closed.is_set():
                return
            if isinstance(payload, bytes):
                if channel.audio:
                    await stream.send_audio(payload)
            elif isinstance(payload, Media):
                if payload.type == "interrupt":
                    channel.playback_epoch += 1
                    cancelled = await stream.barge_in(
                        played_bytes=payload.played_bytes if channel.webrtc_offer is None else None
                    )
                    channel.emit({"type": "interrupted"})
                    if channel.webrtc_offer is not None and not cancelled:
                        if resume is not None:
                            resume.cancel()
                        resume = tasks.create_task(resume_playback())
                        # Snapshot the outstanding reply before forwarding another queued text turn.
                        await asyncio.sleep(0)
                elif payload.type == "audio_commit":
                    await stream.commit_audio()
                else:
                    await stream.clear_audio()
            elif isinstance(payload, MessageInput):
                try:
                    images = await self._realtime_images(payload.asset_ids, field="asset_ids")
                except ServiceError as error:
                    if error.code != "invalid_argument":
                        raise
                    channel.emit({"type": "error", "code": error.code, "message": error.message, "recoverable": True})
                    continue
                channel.touch()
                if images:
                    await self._send_realtime_images(stream, payload.text, images, respond=payload.respond)
                else:
                    await stream.send(payload.text, respond=payload.respond)
                output.realtime_input(payload.text, payload.asset_ids)
            else:
                await stream.send(payload, respond=True)
                output.realtime_input(payload)

    async def _realtime_audio(self, pcm: AsyncIterator[bytes], channel: Channel) -> None:
        async for chunk in pcm:
            if not channel.closed.is_set():
                channel.emit(chunk)

    async def _close_realtime(
        self, stream: HarnessLiveStream, channel: Channel, receiving: Callable[[], asyncio.Task[None] | None]
    ) -> None:
        stopped = asyncio.create_task(self.control.stopped.wait())
        handoff = asyncio.create_task(self.control.handoff.wait())
        closed = asyncio.create_task(channel.closed.wait())
        signals = (stopped, handoff, closed)
        try:
            await asyncio.wait(signals, return_when=asyncio.FIRST_COMPLETED)
            if (receive := receiving()) is not None:
                receive.cancel()
                await asyncio.gather(receive, return_exceptions=True)
            if self.control.stopped.is_set():
                stream.cancel()
            else:
                if self.control.handoff.is_set():
                    channel.draining = True
                    channel.emit({"type": "draining", "code": "worker_draining"})
                async with asyncio.timeout(self.runtime.settings.worker.drain_seconds):
                    await stream.close()
        finally:
            for signal in signals:
                signal.cancel()
            await asyncio.gather(*signals, return_exceptions=True)

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

    async def _boundary(
        self, staged: Staged, *, stream: HarnessRunStream | HarnessLiveStream, output: Coalescer
    ) -> None:
        if staged.at in {"input", "model"}:
            self.offers.requested = True
        steers = await self._commit(
            staged.state,
            cursors=staged.cursors,
            snapshot=staged.display,
        )
        output.stream.boundary(staged.display.tail.position.sequence, resume_after=staged.display.tail.resume_after)
        if staged.at == "model" and self.control.handoff.is_set():
            # Yield where the request in flight is simply sent again; at a tool boundary recovery would have to
            # treat calls that never ran as unknown effects. Steers just assigned stay with the run.
            self.yielding = True
            self.boundaries.stop()
            stream.cancel()
            return
        # A tool boundary waits for the acknowledgement, so steers pending there join the request after the tools.
        if not isinstance(stream, HarnessLiveStream):
            await self._offer(stream, steers)

    async def _commit(
        self,
        state: HarnessState,
        *,
        cursors: dict[str, str | None],
        snapshot: Snapshot,
        outcome: Outcome | None = None,
        deferred: JsonValue = None,
    ) -> list[Offered]:
        rejected: list[StaleNotice] = []
        while True:
            try:
                steers = await self._commit_cut(
                    state,
                    cursors=cursors,
                    snapshot=snapshot,
                    outcome=outcome,
                    deferred=deferred,
                    rejected=rejected,
                )
                break
            except StaleNotice as error:
                # The model waits at this cut. Preserve its completed work and remove only
                # the expired hint from history and compact/recovery replay state.
                rejected.append(error)
                state = without_input(state, input_id=error.entry_id, source_id=error.entry_id)
                self.offers.open.pop(error.entry_id)
        if rejected:
            # The live Harness still holds the old input. End this attempt before dispatch;
            # ordinary recovery restores the corrected, durably committed continuation.
            raise rejected[0]
        return steers

    async def _commit_cut(
        self,
        state: HarnessState,
        *,
        cursors: dict[str, str | None],
        snapshot: Snapshot,
        outcome: Outcome | None,
        deferred: JsonValue,
        rejected: list[StaleNotice],
    ) -> list[Offered]:
        """Commit a checkpoint and what follows it in one fenced transaction: an outcome seals the run;
        otherwise a bounded batch of compatible pending steers is assigned to it, and returned.
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
        consumed: list[str] = []
        if self.offers.open:
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
                resume_input_consumed=(self.plan.resume_input is None and self.plan.reminder is None)
                or self.offers.requested,
            ),
            snapshot,
            contents=self.contents,
            control=self.control,
            previous_tail=self.committed.tail if self.committed else None,
            previous_tail_bytes=self.last_committed_tail_bytes,
        )
        display = display.model_copy(update={"search_terms": tuple(sorted(search.counts)) if search.changed else None})
        worker = self.runtime.settings.worker
        steers: list[Offered] = []
        async with transaction(self.runtime.storage) as session:
            # Thread first: consuming entries fires the thread-version trigger, which updates the thread row.
            thread, run, attempt, current = await checkpoints.lock(session, self.lease)
            # This transaction is the observation boundary: retake either suppresses the hint
            # first, or follows its durable incorporation. No model request carrying a new
            # Browser notice dispatches before this commit, and no lock spans external I/O.
            notices = [
                await session.get_one(InboxEntryRow, entry_id)
                for entry_id in consumed
                if self.offers.open[entry_id].kind in NOTICE_KINDS
            ]
            await require_notices(session, self.runtime, notices)
            for error in rejected:
                await inbox.fail_assigned(session, run.id, error.entry_id, Failure.of(error), at=current)
            # Usage/consumption use explicit SQL; pending checkpoint fields need not flush for those reads.
            # seal() flushes the final checkpoint and outcome together; ordinary boundaries flush on commit.
            with session.no_autoflush:
                committed = await checkpoints.commit(
                    session,
                    run,
                    attempt,
                    previous=self.committed,
                    state=pointer,
                    display=display,
                    memory_cursors=cursors,
                    consumed=consumed,
                    result_retention_seconds=self.runtime.settings.tool_executions.delivered_retention_seconds,
                    usage=usage,
                    at=current,
                )
                await acknowledge_executions(
                    session,
                    run,
                    state,
                    at=current,
                    seconds=self.runtime.settings.tool_executions.delivered_retention_seconds,
                )
                checkpoints.retire(session, run, self.committed, committed)
                await compactions.acknowledge(session, self.runtime, thread.id, acknowledgement, at=current)
                if outcome is not None:
                    # Stop input polling before our seal releases the lease. A poll already waiting for
                    # this transaction's Thread lock must not mistake our own completion for lease loss.
                    if self.incoming is not None:
                        self.incoming.cancel()
                    await seal(session, self.runtime, thread, run, attempt, outcome, at=current)
                elif not self.lease.realtime:
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
        self.search = search
        self.committed, self.seq = committed, self.seq + 1
        self.last_committed_tail_bytes = display.tail_bytes
        self.fold.committed(snapshot)
        self.contents.retire([item.id for page in snapshot.pages for item in page.items])
        self.offers.consumed(consumed)
        self.usage.ingested(usage)
        if outcome is not None and outcome.status in {"completed", "waiting"}:
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
                if entry.kind in NOTICE_KINDS:
                    self.boundaries.wait_for_model = True
                try:
                    await stream.steer(
                        content,
                        input_id=entry.id,
                        immediate=entry.kind in {"message", "child_message"}
                        and self.plan.host.execution.steering == "immediate",
                        best_effort=True,
                    )
                except RunError as error:
                    if error.code not in {"run_not_active", "steering_timeout"}:
                        raise
                    # Neither refusal accepted the input. Keep its assignment and FIFO position
                    # for the next boundary, recovery, or ordinary terminal continuation.
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
                steering=entry.id != self.plan.source_entry_id,
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

    def _budget_failure(self) -> Outcome:
        refusal = self._refusal()
        if self.control.outcome.failure is not None and self.control.outcome.failure.code == "authority_revoked":
            return self.control.outcome
        return refusal or Outcome.failed("usage_limit_exceeded", "The realtime usage budget was exceeded")

    async def _end(self, result: HarnessRunResult) -> None:
        self.usage.add(result.usage_records)
        reason = self.channel.end_reason if self.channel is not None else None
        if reason == "usage_limit_exceeded" and result.status != "completed":
            await self._record_usage()
            await self._seal_interrupted(self._budget_failure())
            return
        if result.status == "failed":
            assert result.failure is not None
            failed = Outcome.failed(
                result.failure.code, _harness_failure_message(result.failure.message, result.failure.details)
            )
            refusal = self._refusal()
            if refusal is None and result.failure.code == "client_output_failed" and result.state is not None:
                # A rejected receipt is a recorded outcome, not an uncertain tool execution.
                self.fold.interrupt(open_tool_calls(result.all_messages()))
                await self._commit(
                    result.state,
                    cursors=self.cursors.snapshot(),
                    snapshot=self._snapshot(terminal=True),
                    outcome=failed,
                )
            else:
                await self._record_usage()
                await self._seal_interrupted(refusal or failed)
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
        snapshot = self._snapshot(terminal=True)
        if result.status == "completed":
            require_output_size(result.output, self.runtime.settings.worker.output_bytes)
            outcome = (
                self._budget_failure()
                if reason == "usage_limit_exceeded"
                else Outcome(status="completed", output=result.output, reason=reason)
            )
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
            snapshot = self.fold.pending(self._snapshot(terminal=True))
            # Only user sources actually visible in this display join a failed/cancelled run's index.
            visible = {
                str(metadata["source_id"])
                for item in (*[item for page in snapshot.pages for item in page.items], *snapshot.tail.items)
                if isinstance(metadata := item.content.get("metadata"), dict) and "source_id" in metadata
            }
            search, snapshot = await self._search(snapshot, visible)
            display = await checkpoints.publish_display(
                self.runtime,
                self.lease,
                snapshot,
                contents=self.contents,
                control=self.control,
                previous_tail=self.committed.tail if self.committed else None,
                previous_tail_bytes=self.last_committed_tail_bytes,
            )
            display = display.model_copy(
                update={"search_terms": tuple(sorted(search.counts)) if search.changed else None}
            )
        await seal_attempt(self.runtime, self.lease, outcome, display=display)

    async def _search(self, snapshot: Snapshot, consumed: Iterable[str]) -> tuple[Index, Snapshot]:
        inputs = [
            (entry.id, MessagePayload.model_validate(entry.payload).text())
            for key in consumed
            if (entry := self.offers.open.get(key)) is not None
            and entry.kind == "message"
            and (not self.lease.realtime or entry.id != self.plan.source_entry_id)
        ]
        items = [item for page in snapshot.pages for item in page.items] + snapshot.tail.items
        search = await asyncio.to_thread(self.search.prepare, items, inputs)
        return search, Snapshot(snapshot.pages, snapshot.tail.model_copy(update={"search": search.state}))

    def _snapshot(self, *, terminal: bool = False) -> Snapshot:
        """The display to commit, with a safe resume hint captured without waiting for queued Redis writes.

        The writer replaces one immutable value on the same event loop. Terminal callers read it after close.
        """
        if terminal and self.channel is not None:
            self.fold.close_realtime_tools(self.channel.decisions, self.channel.dispatched)
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
        return ChildRuns(self.runtime, self.lease, self.control, node.subagents, include_output=self.lease.realtime)

    def _bindings(self, resolver: RunModelResolver) -> RunBindings:
        lease = self.lease
        return RunBindings(
            tool_execution=self.runtime.executions if self.plan.retain_tools else None,
            codeact_state=SharedCodeActState(self.runtime.storage, lease.thread_id),
            configuration=self.plan.configuration,
            context_recovery_scope_id=lease.run_id,
            retained_history_directory=f"{placement.ROOT}/history/{lease.thread_id}",
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
