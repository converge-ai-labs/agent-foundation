"""App-owned persistent asynchronous subagent execution."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from a13n_harness import (
    AgentInstanceContext,
    DeferredToolResume,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessRunStream,
    HarnessState,
    RunBindings,
    SafeFailure,
)
from a13n_harness import __version__ as harness_version
from a13n_harness.capabilities import (
    AsyncDelegateRequest,
    AsyncExecutionView,
    AsyncResumeRequest,
    SubagentActivitySnapshot,
    SubagentCancelRequest,
    SubagentCancelResult,
    SubagentDelegationPlan,
    SubagentExecutionView,
    SubagentInfoRequest,
    SubagentInfoResult,
    SubagentOperator,
    SubagentOperatorContext,
    SubagentSteerRequest,
    SubagentSteerResult,
    SubagentToolCallSnapshot,
    SubagentWaitRequest,
    SubagentWaitResult,
)
from a13n_stream_protocol import HarnessAguiObserver
from ag_ui.core import Event as AguiEvent
from ag_ui.core.events import (
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    RunErrorEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from anyio import CancelScope, Event, Lock, create_task_group, get_cancelled_exc_class, move_on_after
from anyio.abc import TaskGroup
from pydantic import JsonValue, TypeAdapter
from pydantic_ai import ToolDenied, ToolReturn
from pydantic_ai.tools import DeferredToolApprovalResult, DeferredToolRequests, DeferredToolResults

from a13n_ui.capability_runtime import production_run_capabilities
from a13n_ui.environment_runtime import (
    EnvironmentRunPlan,
    EnvironmentRunService,
    EnvironmentSnapshotReconstructor,
    WorkspaceBinding,
)
from a13n_ui.errors import AgentUiError, RunCoordinationError, StoreError
from a13n_ui.live import AgentUiLiveHub
from a13n_ui.model_runtime import AgentUiModelResolver
from a13n_ui.storage import (
    ChildExecutionHead,
    CompactChildActivity,
    CompactChildDisplay,
    LocalStore,
    ObjectKind,
    ObjectRef,
    Session,
    StoredChildCheckpoint,
)

_DEFINITION_ID = re.compile(r"^agent-ui:(?P<digest>[0-9a-f]{64})$")
_JSON_ADAPTER = TypeAdapter(JsonValue)
_MAX_WAIT_SECONDS = 60.0
_DEFAULT_WAIT_SECONDS = 30.0
_MAX_ACTIVITY_TEXT = 32 * 1024
_MAX_DISPLAY_ACTIVITIES = 512
_MAX_TOOL_VALUE_TEXT = 8 * 1024
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "bearer_token",
        "client_secret",
        "credential",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "token",
    }
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(api[-_]?key|access[-_]?token|authorization|bearer[-_]?token|client[-_]?secret|credential|"
    r"password|private[-_]?key|refresh[-_]?token|secret|token)(\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)


@dataclass(frozen=True, slots=True)
class ParentRunScope:
    """Exact active parent correlation plus inherited child runtime inputs."""

    session_id: str
    thread_id: str
    run_id: str
    agent_instance_id: str
    binding: WorkspaceBinding
    model_resolver: AgentUiModelResolver


@dataclass(slots=True)
class _ActiveSegment:
    execution_id: str
    session_id: str
    parent_thread_id: str
    stream: HarnessRunStream[Any]
    done: Event


@dataclass(frozen=True, slots=True)
class _PreparedSegment:
    head: ChildExecutionHead
    plan: SubagentDelegationPlan
    scope: ParentRunScope
    session: Session
    state: HarnessState
    environment: EnvironmentRunPlan
    stream: HarnessRunStream[Any]
    agent_instance_id: str
    display: CompactChildDisplay


class AgentUiSubagentOperator(SubagentOperator):
    """Persist and own Agent UI child Threads and independent Harness segments."""

    def __init__(
        self,
        *,
        store: LocalStore,
        environment_reconstructor: EnvironmentSnapshotReconstructor,
        live_hub: AgentUiLiveHub | None = None,
        cleanup_timeout_seconds: float = 30.0,
    ) -> None:
        self._store = store
        self._environments = EnvironmentRunService(store, environment_reconstructor)
        self._live_hub = live_hub
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._lock = Lock()
        self._parents: dict[tuple[str, str, str], ParentRunScope] = {}
        self._active: dict[str, _ActiveSegment] = {}
        self._revision = Event()
        self._task_group_context: Any | None = None
        self._task_group: TaskGroup | None = None
        self._accepting = False

    async def start(self) -> None:
        """Open the App-owned task lifetime and begin accepting child work."""

        async with self._lock:
            if self._task_group is not None:
                raise RunCoordinationError(
                    "The subagent operator is already started.", code="subagent_operator_started"
                )
            context = create_task_group()
            task_group = await context.__aenter__()
            self._task_group_context = context
            self._task_group = task_group
            self._accepting = True

    async def close(self, *, timeout_seconds: float = 30.0) -> None:
        """Stop admission, cancel owned streams, and close the task lifetime."""

        task_group = self._task_group
        if task_group is not None:
            task_group.cancel_scope.shield = True
        async with self._lock:
            self._accepting = False
            active = tuple(self._active.values())
            context = self._task_group_context
            task_group = self._task_group
        for segment in active:
            segment.stream.cancel()
        if context is None or task_group is None:
            return
        task_group.cancel_scope.shield = True
        with move_on_after(timeout_seconds, shield=True) as scope:
            for segment in active:
                await segment.done.wait()
        if scope.cancel_called:
            task_group.cancel_scope.cancel()
        await context.__aexit__(None, None, None)
        with CancelScope(shield=True):
            await self._store.child_executions.mark_owner_lost(
                owner_process_generation=self._store.process_generation,
            )
        async with self._lock:
            self._task_group_context = None
            self._task_group = None
            self._parents.clear()
            self._active.clear()
            self._signal_revision_locked()

    @asynccontextmanager
    async def bind_parent_run(
        self,
        *,
        session_id: str,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        binding: WorkspaceBinding,
        model_resolver: AgentUiModelResolver,
    ) -> AsyncGenerator[None]:
        """Authorize operator calls from one exact currently active Harness Run."""

        if not isinstance(model_resolver, AgentUiModelResolver):
            raise TypeError("model_resolver must be an AgentUiModelResolver")
        if not await self._store.child_executions.owns_thread(session_id=session_id, thread_id=thread_id):
            raise RunCoordinationError(
                "The parent Thread is outside the selected Session.",
                code="subagent_parent_scope_invalid",
            )
        key = (thread_id, run_id, agent_instance_id)
        scope = ParentRunScope(
            session_id=session_id,
            thread_id=thread_id,
            run_id=run_id,
            agent_instance_id=agent_instance_id,
            binding=binding,
            model_resolver=model_resolver,
        )
        async with self._lock:
            self._require_started_locked()
            if key in self._parents:
                raise RunCoordinationError(
                    "The parent Harness Run is already registered.",
                    code="subagent_parent_active",
                )
            self._parents[key] = scope
        try:
            yield
        finally:
            with CancelScope(shield=True):
                async with self._lock:
                    self._parents.pop(key, None)

    async def delegate(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncDelegateRequest,
    ) -> AsyncExecutionView:
        scope = await self._require_parent(plan.parent)
        if request.subagent_name != plan.child.declaration.name:
            raise RunCoordinationError("The child admission was retargeted.", code="subagent_plan_invalid")
        definition_id = plan.child.definition.definition_id
        definition_digest = _definition_digest(definition_id)
        session = await self._require_session(scope.session_id)
        state = HarnessState.new()
        execution_id = _public_id("execution")
        agent_instance_id = _public_id("agent")
        environment = await self._environments.prepare(session, scope.binding)
        stream = self._new_stream(
            plan=plan,
            scope=scope,
            state=state,
            environment=environment,
            execution_id=execution_id,
            agent_instance_id=agent_instance_id,
        )
        try:
            head = await self._store.child_executions.create(
                execution_id=execution_id,
                session_id=session.session_id,
                parent_thread_id=plan.parent.parent_thread_id,
                child_thread_id=state.thread_id,
                child_run_id=stream.run_id,
                subagent_name=request.subagent_name,
                child_definition_id=definition_id,
                child_definition_digest=definition_digest,
                input=request.prompt,
                owner_process_generation=self._store.process_generation,
            )
        except BaseException as exc:
            await _finalize_rejected(
                environment,
                exc,
                timeout_seconds=self._cleanup_timeout_seconds,
            )
            raise
        prepared = _PreparedSegment(
            head=head,
            plan=plan,
            scope=scope,
            session=session,
            state=state,
            environment=environment,
            stream=stream,
            agent_instance_id=agent_instance_id,
            display=CompactChildDisplay(),
        )
        await self._start_segment(prepared)
        return _async_view(head)

    async def info(
        self,
        context: SubagentOperatorContext,
        request: SubagentInfoRequest,
    ) -> SubagentInfoResult:
        scope = await self._require_parent(context)
        if request.execution_id is not None:
            head = await self._require_execution(scope, request.execution_id)
            return SubagentInfoResult(
                executions=(await self._execution_view(head),),
                execution_offset=0,
                total=1,
            )
        heads, total = await self._store.child_executions.list_scope(
            session_id=scope.session_id,
            parent_thread_id=scope.thread_id,
            offset=request.execution_offset,
            limit=request.execution_limit,
        )
        views = tuple([await self._execution_view(head) for head in heads])
        next_offset = request.execution_offset + len(views)
        return SubagentInfoResult(
            executions=views,
            execution_offset=request.execution_offset,
            total=total,
            next_offset=next_offset if next_offset < total else None,
        )

    async def wait(
        self,
        context: SubagentOperatorContext,
        request: SubagentWaitRequest,
    ) -> SubagentWaitResult:
        scope = await self._require_parent(context)
        timeout = min(request.timeout_seconds or _DEFAULT_WAIT_SECONDS, _MAX_WAIT_SECONDS)
        if request.execution_id is not None:
            head = await self._require_execution(scope, request.execution_id)
            if head.status == "running":
                event = await self._wait_event(head.execution_id)
                with move_on_after(timeout):
                    await event.wait()
            refreshed = await self._require_execution(scope, request.execution_id)
            return SubagentWaitResult(
                executions=(await self._execution_view(refreshed),),
                execution_offset=0,
                total=1,
            )

        heads, _total = await self._store.child_executions.list_scope(
            session_id=scope.session_id,
            parent_thread_id=scope.thread_id,
            offset=request.execution_offset,
            limit=request.execution_limit,
        )
        if any(head.status == "running" for head in heads):
            async with self._lock:
                revision = self._revision
            with move_on_after(timeout):
                await revision.wait()
        info = await self.info(
            context,
            SubagentInfoRequest(
                execution_offset=request.execution_offset,
                execution_limit=request.execution_limit,
            ),
        )
        return SubagentWaitResult(**info.model_dump(mode="python"))

    async def steer(
        self,
        context: SubagentOperatorContext,
        request: SubagentSteerRequest,
    ) -> SubagentSteerResult:
        scope = await self._require_parent(context)
        head = await self._require_execution(scope, request.execution_id)
        active = await self._active_segment(head)
        if active is None:
            return SubagentSteerResult(execution_id=request.execution_id, accepted=False)
        try:
            enqueue_id = await active.stream.steer(request.message)
        except Exception:
            refreshed = await self._require_execution(scope, request.execution_id)
            if refreshed.status != "running":
                return SubagentSteerResult(execution_id=request.execution_id, accepted=False)
            return SubagentSteerResult(execution_id=request.execution_id, accepted=False)
        return SubagentSteerResult(
            execution_id=request.execution_id,
            accepted=True,
            enqueue_id=enqueue_id,
        )

    async def cancel(
        self,
        context: SubagentOperatorContext,
        request: SubagentCancelRequest,
    ) -> SubagentCancelResult:
        scope = await self._require_parent(context)
        head = await self._require_execution(scope, request.execution_id)
        active = await self._active_segment(head)
        if active is None:
            return SubagentCancelResult(
                execution_id=request.execution_id,
                accepted=False,
                status=head.status,
            )
        active.stream.cancel()
        return SubagentCancelResult(
            execution_id=request.execution_id,
            accepted=True,
            status="running",
        )

    async def resume(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncResumeRequest,
    ) -> AsyncExecutionView:
        scope = await self._require_parent(plan.parent)
        previous = await self._require_execution(scope, request.execution_id)
        definition_id = plan.child.definition.definition_id
        definition_digest = _definition_digest(definition_id)
        if (
            previous.subagent_name != plan.child.declaration.name
            or previous.child_definition_id != definition_id
            or previous.child_definition_digest != definition_digest
            or not previous.resumable
            or previous.selected_checkpoint is None
        ):
            raise RunCoordinationError(
                "The retained child execution is not a compatible resumable checkpoint.",
                code="subagent_resume_incompatible",
            )
        checkpoint = await self._read_checkpoint(previous)
        session = await self._require_session(scope.session_id)
        environment = await self._environments.prepare(session, scope.binding)
        agent_instance_id = _public_id("agent")
        execution_id = _public_id("execution")
        stream = self._new_stream(
            plan=plan,
            scope=scope,
            state=checkpoint.harness_state,
            environment=environment,
            execution_id=execution_id,
            agent_instance_id=agent_instance_id,
        )
        try:
            head = await self._store.child_executions.resume(
                previous_execution_id=previous.execution_id,
                execution_id=execution_id,
                child_run_id=stream.run_id,
                child_definition_digest=definition_digest,
                input=request.prompt,
                owner_process_generation=self._store.process_generation,
                session_id=scope.session_id,
                parent_thread_id=scope.thread_id,
            )
        except BaseException as exc:
            await _finalize_rejected(
                environment,
                exc,
                timeout_seconds=self._cleanup_timeout_seconds,
            )
            raise
        prepared = _PreparedSegment(
            head=head,
            plan=plan,
            scope=scope,
            session=session,
            state=checkpoint.harness_state,
            environment=environment,
            stream=stream,
            agent_instance_id=agent_instance_id,
            display=CompactChildDisplay(),
        )
        await self._start_segment(prepared)
        return _async_view(head)

    def _new_stream(
        self,
        *,
        plan: SubagentDelegationPlan,
        scope: ParentRunScope,
        state: HarnessState,
        environment: EnvironmentRunPlan,
        execution_id: str,
        agent_instance_id: str,
        deferred_resume: DeferredToolResume | None = None,
    ) -> HarnessRunStream[Any]:
        bindings = RunBindings(
            instance=AgentInstanceContext(
                identity=plan.child_identity,
                agent_instance_id=agent_instance_id,
                parent_agent_instance_id=plan.parent.parent_agent_instance_id,
                delegation_id=execution_id,
                actor="agent-ui.subagent",
                host_refs={
                    "session_id": scope.session_id,
                    "thread_id": state.thread_id,
                },
            ),
            model_resolver=scope.model_resolver.fresh(),
            capabilities=production_run_capabilities(
                {capability.id for capability in plan.child.definition.capabilities if capability.id is not None}
            ),
        )
        return plan.child.executable.stream(
            plan.context.input if deferred_resume is None else None,
            environments=environment.environments,
            default_environment=environment.default_environment,
            bindings=bindings,
            previous_state=state,
            deferred_resume=deferred_resume,
            usage_limits=plan.usage_limits,
        )

    async def _start_segment(self, prepared: _PreparedSegment) -> None:
        done = Event()
        active = _ActiveSegment(
            execution_id=prepared.head.execution_id,
            session_id=prepared.head.session_id,
            parent_thread_id=prepared.head.parent_thread_id,
            stream=prepared.stream,
            done=done,
        )
        async with self._lock:
            self._require_started_locked()
            task_group = self._task_group
            assert task_group is not None
            self._active[prepared.head.execution_id] = active
            task_group.start_soon(self._run_segment, prepared, active)

    async def _run_segment(self, prepared: _PreparedSegment, active: _ActiveSegment) -> None:
        current = prepared
        expected_checkpoint = current.head.selected_checkpoint
        try:
            while True:
                result, display, terminal_events = await self._consume_run(current)
                if result.status == "suspended":
                    state = result.state
                    deferred = result.deferred
                    assert state is not None and deferred is not None
                    expected_checkpoint = await self._publish_checkpoint(
                        head=current.head,
                        run_id=result.run_id,
                        state=state,
                        display=display,
                        terminal=False,
                        expected=expected_checkpoint,
                    )
                    await self._publish_live(current, terminal_events)
                    next_environment = await self._environments.prepare(current.session, current.scope.binding)
                    deferred_resume = _deny_deferred(deferred)
                    next_stream = self._new_stream(
                        plan=current.plan,
                        scope=current.scope,
                        state=state,
                        environment=next_environment,
                        execution_id=current.head.execution_id,
                        agent_instance_id=current.agent_instance_id,
                        deferred_resume=deferred_resume,
                    )
                    async with self._lock:
                        retained = self._active.get(current.head.execution_id)
                        if retained is not None:
                            retained.stream = next_stream
                    current = _PreparedSegment(
                        head=current.head,
                        plan=current.plan,
                        scope=current.scope,
                        session=current.session,
                        state=state,
                        environment=next_environment,
                        stream=next_stream,
                        agent_instance_id=current.agent_instance_id,
                        display=display,
                    )
                    continue
                durable_terminal_events = await self._finish_result(
                    current.head,
                    result,
                    display,
                    expected_checkpoint,
                    terminal_events,
                )
                await self._publish_live(current, durable_terminal_events)
                return
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            with CancelScope(shield=True):
                if isinstance(exc, get_cancelled_exc_class()):
                    await self._lose_after_acceptance(current.head.execution_id)
                else:
                    await self._fail_after_acceptance(current.head.execution_id, exc)
        finally:
            with CancelScope(shield=True):
                async with self._lock:
                    self._active.pop(current.head.execution_id, None)
                    active.done.set()
                    self._signal_revision_locked()

    async def _consume_run(
        self,
        prepared: _PreparedSegment,
    ) -> tuple[HarnessRunResult[Any], CompactChildDisplay, tuple[AguiEvent, ...]]:
        observer = HarnessAguiObserver()
        compactor = _DisplayCompactor(prepared.display)
        result: HarnessRunResult[Any] | None = None
        terminal_events: tuple[AguiEvent, ...] = ()
        run_error: BaseException | None = None
        try:
            async with self.bind_parent_run(
                session_id=prepared.scope.session_id,
                thread_id=prepared.stream.thread_id,
                run_id=prepared.stream.run_id,
                agent_instance_id=prepared.agent_instance_id,
                binding=prepared.scope.binding,
                model_resolver=prepared.scope.model_resolver.fresh(),
            ):
                async with prepared.stream as stream:
                    async for item in stream:
                        events = observer.observe(item)
                        compactor.observe(events)
                        if isinstance(item, HarnessRunResultEvent):
                            result = item.result
                            terminal_events = events
                        else:
                            await self._publish_live(prepared, events)
        except BaseException as exc:
            run_error = exc
        finalization_error: BaseException | None = None
        with CancelScope(shield=True):
            try:
                await prepared.environment.finalize(
                    timeout_seconds=self._cleanup_timeout_seconds,
                )
            except BaseException as exc:
                finalization_error = exc
        if run_error is not None:
            if finalization_error is not None:
                run_error.add_note(f"Environment finalization also failed: {finalization_error!r}")
            raise run_error
        if finalization_error is not None:
            raise finalization_error
        if result is None:
            raise RunCoordinationError("Child Harness Run produced no result.", code="subagent_result_missing")
        return result, compactor.snapshot(), terminal_events

    async def _publish_live(
        self,
        prepared: _PreparedSegment,
        events: Sequence[AguiEvent],
    ) -> None:
        if self._live_hub is None:
            return
        try:
            await self._live_hub.publish(
                run_kind="child",
                session_id=prepared.scope.session_id,
                thread_id=prepared.stream.thread_id,
                run_id=prepared.stream.run_id,
                execution_id=prepared.head.execution_id,
                events=events,
            )
        except Exception:
            return

    async def _finish_result(
        self,
        head: ChildExecutionHead,
        result: HarnessRunResult[Any],
        display: CompactChildDisplay,
        expected: ObjectRef | None,
        terminal_events: tuple[AguiEvent, ...],
    ) -> tuple[AguiEvent, ...]:
        if result.status == "completed":
            state = result.state
            assert state is not None
            terminal_display = _with_completion(display, output=result.output)
            try:
                await self._publish_checkpoint(
                    head=head,
                    run_id=result.run_id,
                    state=state,
                    display=terminal_display,
                    terminal=True,
                    expected=expected,
                    terminal_status="succeeded",
                    resumable=True,
                )
            except BaseException as exc:
                failure = await self._fail_terminal_persistence(head.execution_id, exc)
                return (_terminal_persistence_error(head, result.run_id, failure),)
            return terminal_events
        if result.status == "failed":
            failure = result.failure or SafeFailure(code="subagent_failed", message="The child execution failed.")
            await self._store.child_executions.finish_without_checkpoint(
                execution_id=head.execution_id,
                status="failed",
                failure=failure,
            )
            return terminal_events
        if result.status == "cancelled":
            await self._store.child_executions.finish_without_checkpoint(
                execution_id=head.execution_id,
                status="cancelled",
            )
            return terminal_events
        raise RunCoordinationError(
            "A deferred child result escaped denial continuation.",
            code="subagent_deferred_unresolved",
        )

    async def _publish_checkpoint(
        self,
        *,
        head: ChildExecutionHead,
        run_id: str,
        state: HarnessState,
        display: CompactChildDisplay,
        terminal: bool,
        expected: ObjectRef | None,
        terminal_status: str | None = None,
        resumable: bool = False,
    ) -> ObjectRef:
        value = StoredChildCheckpoint(
            harness_release=harness_version,
            execution_id=head.execution_id,
            child_thread_id=head.child_thread_id,
            child_run_id=run_id,
            segment_index=head.segment_index,
            harness_state=state,
            display=display,
            terminal=terminal,
            created_at=datetime.now(UTC),
        )
        published = await self._store.objects.publish_model(
            object_kind=ObjectKind.child_checkpoint,
            value=value,
        )
        await self._store.child_executions.select_checkpoint(
            execution_id=head.execution_id,
            expected=expected,
            checkpoint=published.ref,
            child_run_id=run_id,
            terminal_status=cast(Any, terminal_status),
            resumable=resumable,
        )
        async with self._lock:
            self._signal_revision_locked()
        return published.ref

    async def _fail_terminal_persistence(self, execution_id: str, exc: BaseException) -> SafeFailure:
        failure = _safe_failure(exc, fallback_code="subagent_checkpoint_failed")
        try:
            await self._store.child_executions.fail_terminal_persistence(
                execution_id=execution_id,
                failure=failure,
            )
        except StoreError:
            pass
        return failure

    async def _fail_after_acceptance(self, execution_id: str, exc: BaseException) -> None:
        failure = _safe_failure(exc, fallback_code="subagent_execution_failed")
        try:
            await self._store.child_executions.finish_without_checkpoint(
                execution_id=execution_id,
                status="failed",
                failure=failure,
            )
        except StoreError:
            return

    async def _lose_after_acceptance(self, execution_id: str) -> None:
        try:
            await self._store.child_executions.finish_without_checkpoint(
                execution_id=execution_id,
                status="lost",
                resumable=True,
            )
        except StoreError:
            return

    async def _require_parent(self, context: SubagentOperatorContext) -> ParentRunScope:
        session_id = context.host_refs.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise RunCoordinationError(
                "The subagent parent correlation has no Session.",
                code="subagent_parent_scope_invalid",
            )
        key = (context.parent_thread_id, context.parent_run_id, context.parent_agent_instance_id)
        async with self._lock:
            self._require_started_locked()
            scope = self._parents.get(key)
        if scope is None or scope.session_id != session_id:
            raise RunCoordinationError(
                "The subagent request is outside the active parent Run.",
                code="subagent_parent_scope_invalid",
            )
        if not await self._store.child_executions.owns_thread(
            session_id=scope.session_id,
            thread_id=scope.thread_id,
        ):
            raise RunCoordinationError(
                "The parent Thread is outside the selected Session.",
                code="subagent_parent_scope_invalid",
            )
        return scope

    async def _require_session(self, session_id: str) -> Session:
        session = await self._store.sessions.get(session_id)
        if session is None or session.status != "active":
            raise RunCoordinationError("The parent Session is unavailable.", code="subagent_session_unavailable")
        return session

    async def _require_execution(self, scope: ParentRunScope, execution_id: str) -> ChildExecutionHead:
        head = await self._store.child_executions.get_scoped(
            execution_id=execution_id,
            session_id=scope.session_id,
            parent_thread_id=scope.thread_id,
        )
        if head is None:
            raise RunCoordinationError(
                "The child execution is unavailable in this parent scope.",
                code="subagent_execution_unavailable",
            )
        return head

    async def _read_checkpoint(self, head: ChildExecutionHead) -> StoredChildCheckpoint:
        reference = head.selected_checkpoint
        if reference is None:
            raise RunCoordinationError(
                "The child execution has no selected checkpoint.", code="subagent_checkpoint_missing"
            )
        checkpoint = await self._store.objects.read_model(reference, StoredChildCheckpoint)
        if (
            checkpoint.harness_release != harness_version
            or checkpoint.execution_id != head.execution_id
            or checkpoint.child_thread_id != head.child_thread_id
            or checkpoint.child_run_id != head.child_run_id
            or checkpoint.segment_index != head.segment_index
            or checkpoint.terminal != head.selected_checkpoint_terminal
        ):
            raise RunCoordinationError(
                "The selected child checkpoint is incompatible with its execution head.",
                code="subagent_checkpoint_incompatible",
            )
        return checkpoint

    async def _execution_view(self, head: ChildExecutionHead) -> SubagentExecutionView:
        display = CompactChildDisplay()
        if head.selected_checkpoint is not None:
            display = (await self._read_checkpoint(head)).display
        activity = _activity_snapshot(display)
        return SubagentExecutionView(
            execution_id=head.execution_id,
            subagent_name=head.subagent_name,
            child_definition_id=head.child_definition_id,
            status=head.status,
            resumed_from=head.resumed_from,
            failure=None if head.failure is None else head.failure.model_dump(mode="json"),
            resumable=head.resumable,
            thread_id=head.child_thread_id,
            child_run_id=head.child_run_id,
            segment_index=head.segment_index,
            input=head.input,
            activity=activity,
        )

    async def _wait_event(self, execution_id: str) -> Event:
        async with self._lock:
            active = self._active.get(execution_id)
            if active is not None:
                return active.done
            event = Event()
            event.set()
            return event

    async def _active_segment(self, head: ChildExecutionHead) -> _ActiveSegment | None:
        if head.status != "running" or head.owner_process_generation != self._store.process_generation:
            return None
        async with self._lock:
            return self._active.get(head.execution_id)

    def _require_started_locked(self) -> None:
        if not self._accepting or self._task_group is None:
            raise RunCoordinationError(
                "The subagent operator is not accepting work.",
                code="subagent_operator_unavailable",
            )

    def _signal_revision_locked(self) -> None:
        self._revision.set()
        self._revision = Event()


class _DisplayCompactor:
    """Retain only bounded closed AG-UI activity."""

    def __init__(self, initial: CompactChildDisplay) -> None:
        self._activities = list(initial.activities)
        self._text: dict[str, str] = {}
        self._reasoning: dict[str, str] = {}
        self._tool_names: dict[str, str] = {}
        self._tool_arguments: dict[str, str] = {}
        self._tool_results: dict[str, str] = {}
        self._tool_ended: set[str] = set()

    def observe(self, events: Sequence[AguiEvent]) -> None:
        for event in events:
            if isinstance(event, TextMessageStartEvent):
                self._text[event.message_id] = ""
            elif isinstance(event, TextMessageContentEvent):
                self._text[event.message_id] = _append_bounded(self._text.get(event.message_id, ""), event.delta)
            elif isinstance(event, TextMessageEndEvent):
                text = self._text.pop(event.message_id, "")
                if text:
                    self._append(CompactChildActivity(kind="text", text=text))
            elif isinstance(event, ReasoningMessageStartEvent):
                self._reasoning[event.message_id] = ""
            elif isinstance(event, ReasoningMessageContentEvent):
                self._reasoning[event.message_id] = _append_bounded(
                    self._reasoning.get(event.message_id, ""),
                    event.delta,
                )
            elif isinstance(event, ReasoningMessageEndEvent):
                text = self._reasoning.pop(event.message_id, "")
                if text:
                    self._append(CompactChildActivity(kind="thinking", text=text))
            elif isinstance(event, ToolCallStartEvent):
                self._tool_names[event.tool_call_id] = event.tool_call_name
            elif isinstance(event, ToolCallArgsEvent):
                self._tool_arguments[event.tool_call_id] = _append_bounded(
                    self._tool_arguments.get(event.tool_call_id, ""),
                    event.delta,
                    limit=_MAX_TOOL_VALUE_TEXT,
                )
            elif isinstance(event, ToolCallResultEvent):
                tool_call_id = event.tool_call_id
                self._tool_results[tool_call_id] = _append_bounded(
                    self._tool_results.get(tool_call_id, ""),
                    event.content,
                    limit=_MAX_TOOL_VALUE_TEXT,
                )
                if tool_call_id in self._tool_ended:
                    self._finish_tool(tool_call_id)
            elif isinstance(event, ToolCallEndEvent):
                tool_call_id = event.tool_call_id
                self._tool_ended.add(tool_call_id)
                if tool_call_id in self._tool_results:
                    self._finish_tool(tool_call_id)

    def snapshot(self) -> CompactChildDisplay:
        return CompactChildDisplay(activities=tuple(self._activities[-_MAX_DISPLAY_ACTIVITIES:]))

    def _finish_tool(self, tool_call_id: str) -> None:
        name = self._tool_names.pop(tool_call_id, None)
        self._tool_ended.discard(tool_call_id)
        if name is None:
            return
        self._append(
            CompactChildActivity(
                kind="tool",
                tool_name=name[:128],
                arguments=_safe_json(self._tool_arguments.pop(tool_call_id, None)),
                result=_safe_json(self._tool_results.pop(tool_call_id, None)),
            )
        )

    def _append(self, activity: CompactChildActivity) -> None:
        self._activities.append(activity)
        if len(self._activities) > _MAX_DISPLAY_ACTIVITIES:
            del self._activities[: len(self._activities) - _MAX_DISPLAY_ACTIVITIES]


async def _finalize_rejected(
    environment: EnvironmentRunPlan,
    original: BaseException,
    *,
    timeout_seconds: float,
) -> None:
    cleanup_error: BaseException | None = None
    with CancelScope(shield=True):
        try:
            await environment.finalize(timeout_seconds=timeout_seconds)
        except BaseException as exc:
            cleanup_error = exc
    if cleanup_error is not None:
        original.add_note(f"Rejected child Environment finalization also failed: {cleanup_error!r}")


def _definition_digest(definition_id: str) -> str:
    match = _DEFINITION_ID.fullmatch(definition_id)
    if match is None:
        raise RunCoordinationError(
            "The child definition identity is not an Agent UI snapshot identity.",
            code="subagent_definition_invalid",
        )
    return match.group("digest")


def _public_id(kind: str) -> str:
    return f"{kind}-{uuid4().hex[:20]}"


def _async_view(head: ChildExecutionHead) -> AsyncExecutionView:
    return AsyncExecutionView(
        execution_id=head.execution_id,
        subagent_name=head.subagent_name,
        child_definition_id=head.child_definition_id,
        status=head.status,
        resumed_from=head.resumed_from,
        resumable=head.resumable,
        thread_id=head.child_thread_id,
        child_run_id=head.child_run_id,
        segment_index=head.segment_index,
    )


def _deny_deferred(requests: DeferredToolRequests) -> DeferredToolResume:
    calls: dict[str, Any] = {}
    for request in requests.calls:
        if request.tool_name == "ask_user_question":
            calls[request.tool_call_id] = {
                "answers": {},
                "response": "No user response is available to an asynchronous child execution.",
            }
        else:
            calls[request.tool_call_id] = ToolReturn(
                {"status": "unavailable", "reason": "Deferred interaction is unavailable to async child runs."}
            )
    approvals: dict[str, bool | DeferredToolApprovalResult] = {
        request.tool_call_id: ToolDenied("Approval is unavailable to an asynchronous child execution.")
        for request in requests.approvals
    }
    return DeferredToolResume(
        requests=requests,
        results=DeferredToolResults(calls=calls, approvals=approvals),
    )


def _with_completion(display: CompactChildDisplay, *, output: object) -> CompactChildDisplay:
    activities = list(display.activities)
    if (
        isinstance(output, str)
        and output
        and not (activities and activities[-1].kind == "text" and activities[-1].text == output)
    ):
        activities.append(CompactChildActivity(kind="text", text=output[:_MAX_ACTIVITY_TEXT]))
    activities.append(CompactChildActivity(kind="completion"))
    return CompactChildDisplay(activities=tuple(activities[-_MAX_DISPLAY_ACTIVITIES:]))


def _activity_snapshot(display: CompactChildDisplay) -> SubagentActivitySnapshot | None:
    if not display.activities:
        return None
    previews = [activity.text for activity in display.activities if activity.kind in {"text", "thinking"}]
    output_preview = "\n\n".join(text for text in previews if text)[-_MAX_ACTIVITY_TEXT:]
    tools = [activity for activity in display.activities if activity.kind == "tool"][-20:]
    tool_calls = tuple(
        SubagentToolCallSnapshot(
            tool_call_id=f"tool-{index + 1}",
            tool_name=activity.tool_name or "unknown",
            status="success",
            arguments=activity.arguments,
            result=activity.result,
        )
        for index, activity in enumerate(tools)
    )
    return SubagentActivitySnapshot(
        sequence=len(display.activities),
        output_preview=output_preview,
        output_truncated=sum(len(text or "") for text in previews) > len(output_preview),
        recent_tool_calls=tool_calls,
        dropped_tool_calls=max(0, len([item for item in display.activities if item.kind == "tool"]) - len(tools)),
    )


def _append_bounded(current: str, delta: str, *, limit: int = _MAX_ACTIVITY_TEXT) -> str:
    value = current + delta
    return value if len(value) <= limit else value[-limit:]


def _safe_json(value: str | None) -> JsonValue | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        parsed = _redact_text(value)
    try:
        validated = _JSON_ADAPTER.validate_python(parsed)
    except ValueError:
        return _redact_text(str(parsed))[:_MAX_TOOL_VALUE_TEXT]
    return _redact_json(validated)


def _redact_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _is_sensitive_key(key) else _redact_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in _SENSITIVE_FIELD_NAMES or normalized.endswith("_token") or normalized.endswith("_secret")


def _redact_text(value: str) -> str:
    without_bearer = _BEARER_VALUE.sub("Bearer [REDACTED]", value)
    return _SENSITIVE_ASSIGNMENT.sub(lambda match: f'{match.group(1)}{match.group(2)}"[REDACTED]"', without_bearer)


def _terminal_persistence_error(
    head: ChildExecutionHead,
    run_id: str,
    failure: SafeFailure,
) -> RunErrorEvent:
    now = datetime.now(UTC)
    return RunErrorEvent(
        timestamp=int(now.timestamp() * 1000),
        raw_event={
            "thread_id": head.child_thread_id,
            "run_id": run_id,
            "status": "failed",
            "failure": failure.model_dump(mode="json"),
        },
        message=failure.message,
        code=failure.code,
    )


def _safe_failure(exc: BaseException, *, fallback_code: str) -> SafeFailure:
    if isinstance(exc, AgentUiError):
        return SafeFailure(code=exc.code, message=str(exc)[:4096])
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or not code:
        code = fallback_code
    message = str(exc).strip() or "The asynchronous child execution failed."
    return SafeFailure(code=code[:128], message=message[:4096])


__all__ = ["AgentUiSubagentOperator", "ParentRunScope"]
