"""App-owned persistent asynchronous subagent execution."""

from __future__ import annotations

import base64
import json
import math
import re
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial, wraps
from types import CoroutineType
from typing import Any, Literal
from uuid import uuid4

from a13n_harness import (
    AgentIdentityRef,
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
    SubagentToolCallContext,
    SubagentToolCallSnapshot,
    SubagentWaitRequest,
    SubagentWaitResult,
)
from a13n_harness.execution import derive_child_identity
from a13n_harness.input import RunInputValue
from a13n_harness.pricing import get_current_pricing_catalog
from a13n_logging import get_logger
from a13n_stream_protocol import ContentMetadata, HarnessAguiObserver
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
from anyio import CancelScope, Event, Lock, create_task_group, get_cancelled_exc_class, move_on_after, to_thread
from anyio.abc import TaskGroup
from opentelemetry.trace import Span
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_ai import ToolDenied, ToolReturn
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.tools import DeferredToolApprovalResult, DeferredToolRequests, DeferredToolResults
from pydantic_ai.usage import UsageLimits

from a13n_harness_ui.capability_runtime import production_run_bindings
from a13n_harness_ui.composition import (
    AgentReconstructor,
    CompositionAcceptanceService,
    ReconstructedAgent,
    ResolvedAgentNode,
    ResolvedRunComposition,
    ResolvedSubagent,
    RunCompositionService,
    ThreadCompositionSelection,
)
from a13n_harness_ui.diagnostics import exception_feedback
from a13n_harness_ui.environment_runtime import EnvironmentRunPlan, EnvironmentRunService
from a13n_harness_ui.errors import HarnessUiError, RunCoordinationError, StoreError
from a13n_harness_ui.live import HarnessUiLiveHub, HarnessUiSummaryHub
from a13n_harness_ui.model_runtime import SubscriptionSource
from a13n_harness_ui.observation import (
    UiObservation,
    finish_operation,
    record_configuration,
    record_input,
    record_output,
    record_skill_event,
)
from a13n_harness_ui.restart import GracefulRestart, RestartPauseCapability
from a13n_harness_ui.restart_models import RestartItem, RestartPrincipal
from a13n_harness_ui.storage import (
    AgentResourceSource,
    ChildExecutionHead,
    CompactChildActivity,
    CompactChildDisplay,
    LocalStore,
    MarkdownSubagentSource,
    ObjectKind,
    ObjectRef,
    StoredChildCheckpoint,
    StoredThreadInitialState,
    Thread,
    ThreadConfiguration,
)
from a13n_harness_ui.surfaces import (
    ChildActivityView,
    ChildExecutionPage,
    ChildExecutionView,
    ChildToolCallView,
    FailureView,
    SurfaceModel,
)

_JSON_ADAPTER = TypeAdapter(JsonValue)
_MAX_WAIT_SECONDS = 180.0
_DEFAULT_WAIT_SECONDS = 30.0
_MAX_ACTIVITY_TEXT = 32 * 1024
_MAX_DISPLAY_ACTIVITIES = 512
_MAX_TOOL_VALUE_TEXT = 8 * 1024
_MAX_FAILURE_CODE = 256
_MAX_FAILURE_MESSAGE = 32 * 1024
_MAX_FAILURE_DETAILS_BYTES = 64 * 1024
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
_USAGE_LIMIT_FIELDS = (
    "cost_limit",
    "request_limit",
    "tool_calls_limit",
    "input_tokens_limit",
    "output_tokens_limit",
    "total_tokens_limit",
    "per_request_input_tokens_limit",
)


@dataclass(frozen=True, slots=True)
class ParentRunScope:
    """Exact active parent correlation and immutable composition authority."""

    thread_id: str
    root_thread_id: str
    parent_thread_id: str | None
    run_id: str
    instance: AgentInstanceContext
    composition: ResolvedRunComposition

    @property
    def agent_instance_id(self) -> str:
        return self.instance.agent_instance_id


class _ChildCursor(SurfaceModel):
    version: Literal["1"] = "1"
    parent_thread_id: str
    created_at: datetime
    execution_id: str


@dataclass(slots=True)
class _ActiveSegment:
    execution_id: str
    parent_thread_id: str
    stream: HarnessRunStream[Any]
    done: Event
    display: CompactChildDisplay
    cleanup_succeeded: bool = False


@dataclass(frozen=True, slots=True)
class _PreparedSegment:
    head: ChildExecutionHead
    scope: ParentRunScope
    composition: ResolvedRunComposition
    reconstructed: ReconstructedAgent
    input: RunInputValue | None
    usage_limits: UsageLimits | None
    identity: AgentIdentityRef
    state: HarnessState
    environment: EnvironmentRunPlan
    stream: HarnessRunStream[Any]
    agent_instance_id: str
    display: CompactChildDisplay


class _SubagentRequestError(RunCoordinationError):
    """A caller can correct the requested child operation without ending its Run."""


def _subagent_tool[**P, T](function: Callable[P, Awaitable[T]]) -> Callable[P, CoroutineType[Any, Any, T]]:
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await function(*args, **kwargs)
        except _SubagentRequestError as exc:
            raise ToolFailed(f"{exc.code}: {exc}") from exc

    return wrapped


class HarnessUiSubagentOperator(SubagentOperator):
    """Persist child Threads and run each delegate or resume as an owned segment."""

    def __init__(
        self,
        *,
        store: LocalStore,
        configurations: CompositionAcceptanceService,
        compositions: RunCompositionService,
        agent_reconstructor: AgentReconstructor,
        environment_service: EnvironmentRunService,
        subscription_sources: Mapping[str, SubscriptionSource] | None = None,
        live_hub: HarnessUiLiveHub | None = None,
        summary_hub: HarnessUiSummaryHub | None = None,
        cleanup_timeout_seconds: float = 30.0,
        observation: UiObservation | None = None,
        restart_coordinator: GracefulRestart | None = None,
    ) -> None:
        self._observation = observation or UiObservation()
        self._restart = restart_coordinator
        self._store = store
        self._configurations = configurations
        self._compositions = compositions
        self._agents = agent_reconstructor
        self._environments = environment_service
        self._subscription_sources = dict(subscription_sources or {})
        self._live_hub = live_hub
        self._summary_hub = summary_hub
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._lock = Lock()
        self._parents: dict[tuple[str, str, str], ParentRunScope] = {}
        self._active: dict[str, _ActiveSegment] = {}
        self._execution_identities: OrderedDict[tuple[str, ObjectRef], tuple[str, str]] = OrderedDict()
        self._changed = Event()
        self._task_group_context: Any | None = None
        self._task_group: TaskGroup | None = None
        self._accepting = False

    def replace_subscription_sources(self, sources: Mapping[str, SubscriptionSource]) -> None:
        """Use newly discovered stores for future child reconstructions."""
        self._subscription_sources = dict(sources)

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

    async def stop_admission(self) -> None:
        """Reject new parent and child admissions while retaining owned segments."""

        async with self._lock:
            self._accepting = False

    async def close(self, *, timeout_seconds: float = 30.0) -> None:
        """Stop admission, request cancellation, and bound process-local cleanup."""

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        context = self._task_group_context
        task_group = self._task_group
        if task_group is not None:
            task_group.cancel_scope.shield = True
        active: tuple[_ActiveSegment, ...] = ()
        # Shield preparation even when this group's own task has failed.
        # The shield must end before the task group's cancel scope is exited.
        with CancelScope(shield=True):
            async with self._lock:
                self._accepting = False
                active = tuple(self._active.values())
            for segment in active:
                segment.stream.cancel()
        if context is None or task_group is None:
            return

        with move_on_after(timeout_seconds, shield=True) as grace:
            for segment in active:
                await segment.done.wait()
        if grace.cancel_called:
            task_group.cancel_scope.cancel()
        try:
            # Do not abandon children that can still write through the App-owned store.
            # The timeout is a graceful-cancellation bound, not forced Python preemption.
            await context.__aexit__(None, None, None)
        finally:
            with CancelScope(shield=True):
                for segment in active:
                    if not segment.done.is_set():
                        head = await self._store.child_executions.get(segment.execution_id)
                        if head is not None and head.status == "running":
                            await self._lose_after_acceptance(
                                segment.execution_id,
                                expected_checkpoint=head.selected_checkpoint,
                            )
                async with self._lock:
                    self._task_group_context = None
                    self._task_group = None
                    self._parents.clear()
                    self._active.clear()
                    self._signal_change_locked()

    @asynccontextmanager
    async def bind_parent_run(
        self,
        *,
        thread_id: str,
        run_id: str,
        instance: AgentInstanceContext,
        composition: ResolvedRunComposition,
    ) -> AsyncGenerator[None]:
        """Authorize operator calls from one exact currently active Harness Run."""

        if not isinstance(instance, AgentInstanceContext):
            raise TypeError("instance must be an AgentInstanceContext")
        if not isinstance(composition, ResolvedRunComposition):
            raise TypeError("composition must be a ResolvedRunComposition")
        if composition.thread_id != thread_id:
            raise RunCoordinationError(
                "The parent composition belongs to another Thread.",
                code="subagent_parent_scope_invalid",
            )
        thread = await self._store.threads.get(thread_id)
        if thread is None:
            raise RunCoordinationError("The parent Thread is unavailable.", code="subagent_parent_scope_invalid")
        key = (thread_id, run_id, instance.agent_instance_id)
        root_thread_id = await self._root_thread_id(thread)
        scope = ParentRunScope(
            thread_id=thread_id,
            root_thread_id=root_thread_id,
            parent_thread_id=thread.parent_thread_id,
            run_id=run_id,
            instance=instance,
            composition=composition,
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

    @_subagent_tool
    async def delegate(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncDelegateRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> AsyncExecutionView:
        del tool_call
        scope = await self._require_parent(plan.parent)
        edge = _require_edge(scope.composition.root, request.subagent_name)
        if (
            request.subagent_name != plan.child.declaration.name
            or plan.child.definition.definition_id != _definition_id(edge.definition)
        ):
            raise RunCoordinationError("The child admission was retargeted.", code="subagent_plan_invalid")

        source = await self._configurations.load(scope.composition.generation_digest)
        state = HarnessState.new()
        configuration = _initial_child_configuration(scope.composition, edge)
        selection = _selection(state.thread_id, configuration)
        published = await self._compositions.publish(
            source,
            selection,
            parent_node=scope.composition.root,
        )
        if published.value.root != edge.definition:
            raise RunCoordinationError(
                "The child definition changed after parent admission.",
                code="subagent_plan_invalid",
            )
        pricing_catalog = await to_thread.run_sync(get_current_pricing_catalog)
        reconstructed = self._agents.reconstruct(
            published.value,
            pricing_catalog=pricing_catalog,
            subagent_operator=self,
            subscription_sources=self._subscription_sources,
            root_capabilities=()
            if self._restart is None
            else (RestartPauseCapability(self._restart, state.thread_id),),
        )
        environment = await self._environments.prepare(published.value)
        initial = await self._store.objects.publish_model(
            object_kind=ObjectKind.thread_initial_state,
            value=StoredThreadInitialState(harness_state=state, created_at=datetime.now(UTC)),
        )
        await self._store.threads.create(
            thread_id=state.thread_id,
            parent_thread_id=scope.thread_id,
            configuration=configuration,
            initial_state=initial.ref,
            title=request.subagent_name,
        )

        execution_id = _public_id("execution")
        agent_instance_id = _public_id("agent")
        identity = derive_child_identity(
            scope.instance.identity,
            reconstructed.executable.definition.definition_id,
            plan.child.declaration.identity,
        )
        usage_limits = _intersect_usage_limits(
            plan.usage_limits,
            reconstructed.executable._fresh_definition_usage_limits(),
        )
        stream = self._new_stream(
            reconstructed=reconstructed,
            input=plan.context.input,
            usage_limits=usage_limits,
            identity=identity,
            state=state,
            environment=environment,
            execution_id=execution_id,
            parent=scope,
            agent_instance_id=agent_instance_id,
        )
        try:
            head = await self._store.child_executions.create(
                execution_id=execution_id,
                parent_thread_id=scope.thread_id,
                child_thread_id=state.thread_id,
                child_run_id=stream.run_id,
                run_composition=published.reference,
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
            scope=scope,
            composition=published.value,
            reconstructed=reconstructed,
            input=plan.context.input,
            usage_limits=usage_limits,
            identity=identity,
            state=state,
            environment=environment,
            stream=stream,
            agent_instance_id=agent_instance_id,
            display=CompactChildDisplay(),
        )
        try:
            await self._start_segment(prepared)
        except BaseException as exc:
            await self._lose_after_acceptance(head.execution_id, expected_checkpoint=None)
            await _finalize_rejected(
                environment,
                exc,
                timeout_seconds=self._cleanup_timeout_seconds,
            )
            raise
        await self._publish_summary(head)
        return _async_view(
            head,
            subagent_name=request.subagent_name,
            child_definition_id=reconstructed.executable.definition.definition_id,
        )

    @_subagent_tool
    async def info(
        self,
        context: SubagentOperatorContext,
        request: SubagentInfoRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentInfoResult:
        del tool_call
        scope = await self._require_parent(context)
        return await self.inspect_executions(
            parent_thread_id=scope.thread_id,
            execution_id=request.execution_id,
            execution_offset=request.execution_offset,
            execution_limit=request.execution_limit,
        )

    async def inspect_executions(
        self,
        *,
        parent_thread_id: str,
        execution_id: str | None = None,
        execution_offset: int = 0,
        execution_limit: int = 20,
    ) -> SubagentInfoResult:
        """Return detached saved child execution projections for one parent Thread."""

        if execution_id is not None:
            head = await self._require_execution_for_parent(parent_thread_id, execution_id)
            return SubagentInfoResult(
                executions=(await self._execution_view(head),),
                execution_offset=0,
                total=1,
            )
        heads, total = await self._store.child_executions.list_for_parent(
            parent_thread_id,
            offset=execution_offset,
            limit=execution_limit,
        )
        views = tuple([await self._execution_view(head) for head in heads])
        next_offset = execution_offset + len(views)
        return SubagentInfoResult(
            executions=views,
            execution_offset=execution_offset,
            total=total,
            next_offset=next_offset if next_offset < total else None,
        )

    async def active_execution_ids(self) -> frozenset[str]:
        """Return a detached snapshot of locally owned active child executions."""

        async with self._lock:
            return frozenset(self._active)

    async def query_child_executions(
        self,
        *,
        parent_thread_id: str,
        execution_id: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ChildExecutionPage:
        """Query saved child executions without requiring a live parent Run."""

        if not 1 <= limit <= 100:
            raise RunCoordinationError("Child execution page is invalid.", code="child_page_invalid")
        if execution_id is not None:
            if cursor is not None:
                raise RunCoordinationError(
                    "A child cursor cannot be combined with one execution ID.",
                    code="child_cursor_invalid",
                )
            head = await self._require_execution_for_parent(parent_thread_id, execution_id)
            return ChildExecutionPage(
                executions=(await self._execution_projection(head, include_activity=True),),
                total=1,
            )
        after: tuple[datetime, str] | None = None
        if cursor is not None:
            decoded = _decode_child_cursor(cursor)
            if decoded.parent_thread_id != parent_thread_id:
                raise RunCoordinationError(
                    "Child cursor belongs to another parent Thread.",
                    code="child_cursor_mismatch",
                )
            after = (decoded.created_at, decoded.execution_id)
        heads, total = await self._store.child_executions.page_for_parent(
            parent_thread_id,
            after=after,
            limit=limit + 1,
        )
        visible = heads[:limit]
        parent = await self._store.threads.get(parent_thread_id)
        if parent is None:
            raise RunCoordinationError("Parent Thread does not exist.", code="thread_missing")
        root_thread_id = await self._root_thread_id(parent)
        projections = tuple([await self._execution_projection(head, root_thread_id=root_thread_id) for head in visible])
        next_cursor = None
        if len(heads) > limit:
            last = visible[-1]
            next_cursor = _encode_child_cursor(
                _ChildCursor(
                    parent_thread_id=parent_thread_id,
                    created_at=last.created_at,
                    execution_id=last.execution_id,
                )
            )
        return ChildExecutionPage(
            executions=projections,
            total=total,
            next_cursor=next_cursor,
        )

    async def wait_child_executions(
        self,
        *,
        parent_thread_id: str,
        execution_id: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
        timeout_seconds: float | None = None,
    ) -> ChildExecutionPage:
        """Wait only for matching locally owned segments, then return saved state."""

        timeout = _wait_timeout(timeout_seconds)
        page = await self.query_child_executions(
            parent_thread_id=parent_thread_id,
            execution_id=execution_id,
            cursor=cursor,
            limit=limit,
        )
        async with self._lock:
            active_ids = frozenset(self._active)
            changed = self._changed
        if any(item.local_status == "active" and item.execution_id in active_ids for item in page.executions):
            if self._restart is not None:
                await self._restart.wait_child(changed, timeout)
            else:
                with move_on_after(timeout):
                    await changed.wait()
            return await self.query_child_executions(
                parent_thread_id=parent_thread_id,
                execution_id=execution_id,
                cursor=cursor,
                limit=limit,
            )
        return page

    @_subagent_tool
    async def wait(
        self,
        context: SubagentOperatorContext,
        request: SubagentWaitRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentWaitResult:
        del tool_call
        scope = await self._require_parent(context)
        return await self.wait_executions(
            parent_thread_id=scope.thread_id,
            execution_id=request.execution_id,
            execution_offset=request.execution_offset,
            execution_limit=request.execution_limit,
            timeout_seconds=request.timeout_seconds,
        )

    async def wait_executions(
        self,
        *,
        parent_thread_id: str,
        execution_id: str | None = None,
        execution_offset: int = 0,
        execution_limit: int = 20,
        timeout_seconds: float | None = None,
    ) -> SubagentWaitResult:
        """Bound waiting to locally owned segments and return detached projections."""

        timeout = _wait_timeout(timeout_seconds)
        if execution_id is not None:
            head = await self._require_execution_for_parent(parent_thread_id, execution_id)
            if head.status == "running":
                event = await self._wait_event(head.execution_id)
                if self._restart is not None:
                    await self._restart.wait_child(event, timeout)
                else:
                    with move_on_after(timeout):
                        await event.wait()
            refreshed = await self._require_execution_for_parent(parent_thread_id, execution_id)
            return SubagentWaitResult(
                executions=(await self._execution_view(refreshed),),
                execution_offset=0,
                total=1,
            )

        heads, _total = await self._store.child_executions.list_for_parent(
            parent_thread_id,
            offset=execution_offset,
            limit=execution_limit,
        )
        async with self._lock:
            active_ids = frozenset(self._active)
            changed = self._changed
        if any(head.status == "running" and head.execution_id in active_ids for head in heads):
            if self._restart is not None:
                await self._restart.wait_child(changed, timeout)
            else:
                with move_on_after(timeout):
                    await changed.wait()
        info = await self.inspect_executions(
            parent_thread_id=parent_thread_id,
            execution_offset=execution_offset,
            execution_limit=execution_limit,
        )
        return SubagentWaitResult(**info.model_dump(mode="python"))

    @_subagent_tool
    async def steer(
        self,
        context: SubagentOperatorContext,
        request: SubagentSteerRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentSteerResult:
        del tool_call
        scope = await self._require_parent(context)
        return await self.steer_execution(
            parent_thread_id=scope.thread_id,
            execution_id=request.execution_id,
            message=request.message,
        )

    async def steer_execution(
        self,
        *,
        parent_thread_id: str,
        execution_id: str,
        message: str,
    ) -> SubagentSteerResult:
        """Steer a locally owned segment scoped to its saved parent relationship."""

        head = await self._require_execution_for_parent(parent_thread_id, execution_id)
        active = await self._active_segment(head)
        if active is None:
            return SubagentSteerResult(execution_id=execution_id, accepted=False)
        try:
            if self._restart is not None:
                self._restart.require_input()
            enqueue_id = await active.stream.steer(message)
        except Exception:
            return SubagentSteerResult(execution_id=execution_id, accepted=False)
        return SubagentSteerResult(
            execution_id=execution_id,
            accepted=True,
            enqueue_id=enqueue_id,
        )

    @_subagent_tool
    async def cancel(
        self,
        context: SubagentOperatorContext,
        request: SubagentCancelRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentCancelResult:
        del tool_call
        scope = await self._require_parent(context)
        return await self.cancel_execution(
            parent_thread_id=scope.thread_id,
            execution_id=request.execution_id,
        )

    async def cancel_execution(
        self,
        *,
        parent_thread_id: str,
        execution_id: str,
    ) -> SubagentCancelResult:
        """Cancel a locally owned segment scoped to its saved parent relationship."""

        head = await self._require_execution_for_parent(parent_thread_id, execution_id)
        active = await self._active_segment(head)
        if active is None:
            return SubagentCancelResult(
                execution_id=execution_id,
                accepted=False,
                status=head.status,
            )
        if self._restart is not None:
            self._restart.active.pop(head.child_thread_id, None)
            self._restart.signal()
        active.stream.cancel()
        return SubagentCancelResult(
            execution_id=execution_id,
            accepted=True,
            status="running",
        )

    @_subagent_tool
    async def resume(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncResumeRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> AsyncExecutionView:
        del tool_call
        scope = await self._require_parent(plan.parent)
        previous = await self._require_execution(scope, request.execution_id)
        subagent_name, _previous_definition_id = await self._execution_identity(previous)
        _require_edge(scope.composition.root, subagent_name)
        if subagent_name != plan.child.declaration.name or not previous.resumable:
            raise _SubagentRequestError(
                "The retained child execution is not resumable from this roster edge.",
                code="subagent_resume_incompatible",
            )
        checkpoint = await self._read_checkpoint(previous)
        if not checkpoint.terminal or checkpoint.deferred_requests is not None:
            raise _SubagentRequestError(
                "The selected child checkpoint is not terminal.",
                code="subagent_resume_incompatible",
            )
        thread = await self._require_child_thread(previous)
        source = await self._configurations.load(scope.composition.generation_digest)
        published = await self._compositions.publish(
            source,
            _selection(thread.thread_id, thread.configuration),
            parent_node=scope.composition.root,
        )
        pricing_catalog = await to_thread.run_sync(get_current_pricing_catalog)
        reconstructed = self._agents.reconstruct(
            published.value,
            pricing_catalog=pricing_catalog,
            subagent_operator=self,
            subscription_sources=self._subscription_sources,
            root_capabilities=()
            if self._restart is None
            else (RestartPauseCapability(self._restart, thread.thread_id),),
        )
        environment = await self._environments.prepare(published.value)
        execution_id = _public_id("execution")
        agent_instance_id = _public_id("agent")
        identity = derive_child_identity(
            scope.instance.identity,
            reconstructed.executable.definition.definition_id,
            plan.child.declaration.identity,
        )
        usage_limits = _intersect_usage_limits(
            plan.usage_limits,
            reconstructed.executable._fresh_definition_usage_limits(),
        )
        stream = self._new_stream(
            reconstructed=reconstructed,
            input=plan.context.input,
            usage_limits=usage_limits,
            identity=identity,
            state=checkpoint.harness_state,
            environment=environment,
            execution_id=execution_id,
            parent=scope,
            agent_instance_id=agent_instance_id,
        )
        try:
            head = await self._store.child_executions.resume(
                previous_execution_id=previous.execution_id,
                execution_id=execution_id,
                child_run_id=stream.run_id,
                run_composition=published.reference,
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
            scope=scope,
            composition=published.value,
            reconstructed=reconstructed,
            input=plan.context.input,
            usage_limits=usage_limits,
            identity=identity,
            state=checkpoint.harness_state,
            environment=environment,
            stream=stream,
            agent_instance_id=agent_instance_id,
            display=checkpoint.display,
        )
        try:
            await self._start_segment(prepared)
        except BaseException as exc:
            await self._lose_after_acceptance(head.execution_id, expected_checkpoint=None)
            await _finalize_rejected(
                environment,
                exc,
                timeout_seconds=self._cleanup_timeout_seconds,
            )
            raise
        await self._publish_summary(head)
        return _async_view(
            head,
            subagent_name=subagent_name,
            child_definition_id=reconstructed.executable.definition.definition_id,
        )

    async def cancel_restart(self, thread_id: str) -> None:
        async with self._lock:
            active = tuple(entry for entry in self._active.values() if entry.stream.thread_id == thread_id)
        for entry in active:
            entry.stream.cancel()
            await entry.done.wait()

    async def resume_restart(self, item: RestartItem, batch_id: str) -> tuple[str, str]:
        """Reconstruct only a consumed, exact restart checkpoint; no active parent is fabricated."""
        assert item.execution_id is not None and item.parent_thread_id is not None
        if (
            item.parent_composition is None
            or item.identity is None
            or item.parent_identity is None
            or item.parent_run_id is None
        ):
            raise RunCoordinationError("The child handoff is incomplete.", code="restart_incompatible")
        previous = await self._require_execution_for_parent(item.parent_thread_id, item.execution_id)
        if previous.selected_checkpoint != item.checkpoint:
            raise RunCoordinationError("The child handoff checkpoint changed.", code="restart_conflict")
        checkpoint = await self._read_checkpoint(previous)
        if checkpoint.deferred_requests is not None:
            raise RunCoordinationError("A child handoff contains deferred requests.", code="restart_incompatible")
        composition = await self._store.objects.read_model(item.composition, ResolvedRunComposition)
        parent_composition = await self._store.objects.read_model(item.parent_composition, ResolvedRunComposition)
        parent_thread = await self._store.threads.get(item.parent_thread_id)
        if parent_thread is None:
            raise RunCoordinationError("The child parent is missing.", code="restart_incompatible")
        identity = AgentIdentityRef(issuer=item.identity.issuer, subject=item.identity.subject, **item.identity.claims)
        parent_identity = AgentIdentityRef(
            issuer=item.parent_identity.issuer, subject=item.parent_identity.subject, **item.parent_identity.claims
        )
        scope = ParentRunScope(
            thread_id=item.parent_thread_id,
            root_thread_id=item.root_thread_id,
            parent_thread_id=parent_thread.parent_thread_id,
            run_id=item.parent_run_id,
            instance=AgentInstanceContext(identity=parent_identity, agent_instance_id=_public_id("agent")),
            composition=parent_composition,
        )
        pricing = await to_thread.run_sync(get_current_pricing_catalog)
        reconstructed = self._agents.reconstruct(
            composition,
            pricing_catalog=pricing,
            subagent_operator=self,
            subscription_sources=self._subscription_sources,
            root_capabilities=() if self._restart is None else (RestartPauseCapability(self._restart, item.thread_id),),
        )
        environment = await self._environments.prepare(composition)
        execution_id, agent_instance_id = _public_id("execution"), _public_id("agent")
        limits = None if item.usage_limits is None else TypeAdapter(UsageLimits).validate_python(item.usage_limits)
        try:
            stream = self._new_stream(
                reconstructed=reconstructed,
                input=None,
                usage_limits=limits,
                identity=identity,
                state=checkpoint.harness_state,
                environment=environment,
                execution_id=execution_id,
                parent=scope,
                agent_instance_id=agent_instance_id,
            )
            head = await self._store.child_executions.resume(
                previous_execution_id=previous.execution_id,
                execution_id=execution_id,
                child_run_id=stream.run_id,
                run_composition=item.composition,
                restart_batch_id=batch_id,
            )
            prepared = _PreparedSegment(
                head=head,
                scope=scope,
                composition=composition,
                reconstructed=reconstructed,
                input=None,
                usage_limits=limits,
                identity=identity,
                state=checkpoint.harness_state,
                environment=environment,
                stream=stream,
                agent_instance_id=agent_instance_id,
                display=checkpoint.display,
            )
            await self._start_segment(prepared)
        except BaseException as exc:
            await _finalize_rejected(environment, exc, timeout_seconds=self._cleanup_timeout_seconds)
            raise
        return execution_id, stream.run_id

    def _new_stream(
        self,
        *,
        reconstructed: ReconstructedAgent,
        input: RunInputValue | None,
        usage_limits: UsageLimits | None,
        identity: AgentIdentityRef,
        state: HarnessState,
        environment: EnvironmentRunPlan,
        execution_id: str,
        parent: ParentRunScope,
        agent_instance_id: str,
        deferred_resume: DeferredToolResume | None = None,
    ) -> HarnessRunStream[Any]:
        bindings = RunBindings(
            instance=AgentInstanceContext(
                identity=identity,
                agent_instance_id=agent_instance_id,
                parent_agent_instance_id=parent.agent_instance_id,
                delegation_id=execution_id,
                actor="a13n-harness-ui.subagent",
                host_refs={"thread_id": state.thread_id},
            ),
            environment=environment.runtime,
            tool_result_directory=environment.tool_result_directory,
            model_resolver=reconstructed.model_resolver.fresh(),
            file_media_understanding=reconstructed.file_media_understanding(state.thread_id),
        )
        bindings = production_run_bindings(bindings, reconstructed.definition_capability_ids)
        return reconstructed.executable.stream(
            input if deferred_resume is None else None,
            bindings=bindings,
            previous_state=state,
            deferred_resume=deferred_resume,
            usage_limits=usage_limits,
        )

    async def _start_segment(self, prepared: _PreparedSegment) -> None:
        done = Event()
        active = _ActiveSegment(
            execution_id=prepared.head.execution_id,
            parent_thread_id=prepared.head.parent_thread_id,
            stream=prepared.stream,
            done=done,
            display=prepared.display,
        )
        async with self._lock:
            self._require_started_locked()
            task_group = self._task_group
            assert task_group is not None
            self._active[prepared.head.execution_id] = active
            if self._restart is not None:
                self._restart.register(prepared.state.thread_id, prepared.head.execution_id)
            try:
                task_group.start_soon(self._run_segment, prepared, active)
            except BaseException:
                self._active.pop(prepared.head.execution_id, None)
                done.set()
                raise

    async def _run_segment(self, prepared: _PreparedSegment, active: _ActiveSegment) -> None:
        with self._observation.operation(
            "subagent",
            thread_id=prepared.state.thread_id,
            operation_id=prepared.head.execution_id,
            linked=True,
            root_thread_id=prepared.scope.root_thread_id,
            parent_thread_id=prepared.head.parent_thread_id,
            subagent_role=prepared.composition.root.roster_name,
            segment_index=prepared.head.segment_index,
            resumed_from_execution_id=prepared.head.resumed_from,
        ) as span:
            record_configuration(prepared.composition, prepared.reconstructed.definition_capability_ids)
            record_input(prepared.input, kind="resume" if prepared.head.resumed_from is not None else "delegation")
            await self._execute_segment(prepared, active, span)

    async def _execute_segment(self, prepared: _PreparedSegment, active: _ActiveSegment, span: Span) -> None:
        current = prepared
        expected_checkpoint: ObjectRef | None = None
        try:
            while True:
                result, display, terminal_events = await self._consume_run(current, active)
                if await self._finish_restart(current, active, expected_checkpoint):
                    return
                if result.status == "suspended":
                    state = result.state
                    deferred = result.deferred
                    assert state is not None and deferred is not None
                    expected_checkpoint = await self._publish_checkpoint(
                        head=current.head,
                        run_id=result.run_id,
                        state=state,
                        deferred_requests=deferred,
                        display=display,
                        terminal=False,
                        expected=expected_checkpoint,
                    )
                    next_environment = await self._environments.prepare(current.composition)
                    next_stream = self._new_stream(
                        reconstructed=current.reconstructed,
                        input=current.input,
                        usage_limits=current.usage_limits,
                        identity=current.identity,
                        state=state,
                        environment=next_environment,
                        execution_id=current.head.execution_id,
                        parent=current.scope,
                        agent_instance_id=current.agent_instance_id,
                        deferred_resume=_deny_deferred(deferred),
                    )
                    async with self._lock:
                        retained = self._active.get(current.head.execution_id)
                        if retained is not None:
                            retained.stream = next_stream
                            retained.display = display
                            self._signal_change_locked()
                    current = _PreparedSegment(
                        head=current.head,
                        scope=current.scope,
                        composition=current.composition,
                        reconstructed=current.reconstructed,
                        input=current.input,
                        usage_limits=current.usage_limits,
                        identity=current.identity,
                        state=state,
                        environment=next_environment,
                        stream=next_stream,
                        agent_instance_id=current.agent_instance_id,
                        display=display,
                    )
                    continue
                record_output(result.output, status=result.status)
                durable_events = await self._finish_result(
                    current.head,
                    result,
                    display,
                    expected_checkpoint,
                    terminal_events,
                )
                await self._publish_summary(current.head)
                await self._publish_live(current, durable_events)
                terminal_error = next((event for event in durable_events if isinstance(event, RunErrorEvent)), None)
                finish_operation(
                    span,
                    status="failed" if result.status == "completed" and terminal_error is not None else result.status,
                    run_id=result.run_id,
                    error_code=terminal_error.code if terminal_error is not None else None,
                )
                return
        except BaseException as exc:
            finish_operation(
                span,
                status="lost" if isinstance(exc, get_cancelled_exc_class()) else "failed",
                run_id=current.stream.run_id,
                error_code="subagent_execution_failed",
            )
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            with CancelScope(shield=True):
                if await self._finish_restart(current, active, expected_checkpoint):
                    return
                checkpoint: ObjectRef | None = None
                try:
                    state = await current.stream.export_state()
                    checkpoint = await self._publish_checkpoint_object(
                        head=current.head,
                        run_id=current.stream.run_id,
                        state=state,
                        deferred_requests=None,
                        display=active.display,
                        terminal=True,
                    )
                except Exception as checkpoint_error:
                    get_logger(__name__).error(
                        "Child recovery checkpoint failed: execution_id=%s exception_type=%s",
                        current.head.execution_id,
                        type(checkpoint_error).__name__,
                    )
                if isinstance(exc, get_cancelled_exc_class()):
                    await self._lose_after_acceptance(
                        current.head.execution_id,
                        expected_checkpoint=expected_checkpoint,
                        checkpoint=checkpoint,
                    )
                else:
                    await self._fail_after_acceptance(
                        current.head.execution_id,
                        exc,
                        expected_checkpoint=expected_checkpoint,
                        checkpoint=checkpoint,
                    )
        finally:
            with CancelScope(shield=True):
                async with self._lock:
                    self._active.pop(current.head.execution_id, None)
                    if self._restart is not None:
                        self._restart.finished(current.state.thread_id)
                    active.done.set()
                    self._signal_change_locked()
                # Persisted terminal status precedes cleanup; publish again only
                # after process-local activity is no longer observable as active.
                await self._publish_summary_by_execution(current.head.execution_id)

    async def _finish_restart(
        self,
        prepared: _PreparedSegment,
        active: _ActiveSegment,
        expected: ObjectRef | None,
    ) -> bool:
        restart_coordinator = self._restart
        state = restart_coordinator.saved_state(prepared.state.thread_id) if restart_coordinator is not None else None
        if state is None or not active.cleanup_succeeded:
            return False
        assert restart_coordinator is not None
        checkpoint = await self._publish_checkpoint_object(
            head=prepared.head,
            run_id=prepared.stream.run_id,
            state=state,
            deferred_requests=None,
            display=active.display,
            terminal=True,
        )
        await self._store.child_executions.finish(
            execution_id=prepared.head.execution_id,
            status="cancelled",
            expected_checkpoint=expected,
            checkpoint=checkpoint,
            failure=SafeFailure(code="graceful_restart", message="Paused for a graceful restart."),
        )
        parent_composition = await self._store.objects.publish_model(
            object_kind=ObjectKind.run_composition,
            value=prepared.scope.composition,
        )
        restart_coordinator.saved(
            RestartItem(
                thread_id=prepared.state.thread_id,
                root_thread_id=prepared.scope.root_thread_id,
                parent_thread_id=prepared.scope.thread_id,
                execution_id=prepared.head.execution_id,
                run_id=prepared.stream.run_id,
                composition=prepared.head.run_composition,
                checkpoint=checkpoint,
                parent_composition=parent_composition.ref,
                parent_run_id=prepared.scope.run_id,
                identity=RestartPrincipal(
                    issuer=prepared.identity.issuer,
                    subject=prepared.identity.subject,
                    claims=dict(prepared.identity.claims),
                ),
                parent_identity=RestartPrincipal(
                    issuer=prepared.scope.instance.identity.issuer,
                    subject=prepared.scope.instance.identity.subject,
                    claims=dict(prepared.scope.instance.identity.claims),
                ),
                usage_limits=None if prepared.usage_limits is None else asdict(prepared.usage_limits),
            )
        )
        return True

    async def _consume_run(
        self,
        prepared: _PreparedSegment,
        active: _ActiveSegment,
    ) -> tuple[HarnessRunResult[Any], CompactChildDisplay, tuple[AguiEvent, ...]]:
        observer = HarnessAguiObserver()
        compactor = _DisplayCompactor(prepared.display)
        result: HarnessRunResult[Any] | None = None
        terminal_events: tuple[AguiEvent, ...] = ()
        run_error: BaseException | None = None
        try:
            instance = AgentInstanceContext(
                identity=prepared.identity,
                agent_instance_id=prepared.agent_instance_id,
                parent_agent_instance_id=prepared.scope.agent_instance_id,
                delegation_id=prepared.head.execution_id,
                actor="a13n-harness-ui.subagent",
                host_refs={"thread_id": prepared.state.thread_id},
            )
            async with self.bind_parent_run(
                thread_id=prepared.stream.thread_id,
                run_id=prepared.stream.run_id,
                instance=instance,
                composition=prepared.composition,
            ):
                async with prepared.stream as stream:
                    async for item in stream:
                        record_skill_event(item)
                        await self._store.usage.observe(thread_id=prepared.state.thread_id, item=item)
                        events = observer.observe(item)
                        compactor.observe(events)
                        async with self._lock:
                            active.display = compactor.snapshot()
                        if isinstance(item, HarnessRunResultEvent):
                            # Completion remains fenced by cleanup and checkpoint
                            # publication in _finish_result; token delivery is not
                            # evidence of a saved child outcome.
                            result = item.result
                            terminal_events = events
                        else:
                            await self._publish_live(prepared, events)
        except BaseException as exc:
            run_error = exc

        finalization_error: BaseException | None = None
        with CancelScope(shield=True):
            try:
                finalized = await prepared.environment.finalize(
                    timeout_seconds=self._cleanup_timeout_seconds,
                )
                active.cleanup_succeeded = not finalized.cleanup_errors and all(
                    publication.status != "failed" for publication in finalized.state_publications
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
        if result.failure is not None and prepared.stream.diagnostic_error is not None:
            feedback = await to_thread.run_sync(
                partial(
                    exception_feedback,
                    prepared.stream.diagnostic_error,
                    thread_id=prepared.stream.thread_id,
                    run_id=prepared.stream.run_id,
                    phase="child_execution",
                )
            )
            result = result.replace(
                failure=result.failure.model_copy(update={"message": f"{result.failure.message}\n{feedback}"})
            )
        return result, compactor.snapshot(), terminal_events

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
                checkpoint = await self._publish_checkpoint_object(
                    head=head,
                    run_id=result.run_id,
                    state=state,
                    deferred_requests=None,
                    display=terminal_display,
                    terminal=True,
                )
                await self._store.child_executions.finish(
                    execution_id=head.execution_id,
                    status="succeeded",
                    expected_checkpoint=expected,
                    checkpoint=checkpoint,
                    child_run_id=result.run_id,
                )
            except BaseException as exc:
                failure = await self._fail_terminal_persistence(
                    head.execution_id,
                    exc,
                    expected_checkpoint=expected,
                )
                return (_terminal_persistence_error(head, result.run_id, failure),)
            return terminal_events

        if result.status == "failed":
            failure = result.failure or SafeFailure(code="subagent_failed", message="The child execution failed.")
            terminal_display = _with_failure(display, failure)
            await self._finish_non_success(
                head=head,
                result=result,
                display=terminal_display,
                status="failed",
                failure=failure,
                expected=expected,
            )
            return terminal_events

        if result.status == "cancelled":
            terminal_display = _with_completion(display, output=None)
            await self._finish_non_success(
                head=head,
                result=result,
                display=terminal_display,
                status="cancelled",
                failure=None,
                expected=expected,
            )
            return terminal_events

        raise RunCoordinationError(
            "A deferred child result escaped denial continuation.",
            code="subagent_deferred_unresolved",
        )

    async def _finish_non_success(
        self,
        *,
        head: ChildExecutionHead,
        result: HarnessRunResult[Any],
        display: CompactChildDisplay,
        status: str,
        failure: SafeFailure | None,
        expected: ObjectRef | None,
    ) -> None:
        checkpoint: ObjectRef | None = None
        state = result.state
        if state is not None:
            try:
                checkpoint = await self._publish_checkpoint_object(
                    head=head,
                    run_id=result.run_id,
                    state=state,
                    deferred_requests=None,
                    display=display,
                    terminal=True,
                )
            except Exception:
                checkpoint = None
        await self._store.child_executions.finish(
            execution_id=head.execution_id,
            status=status,  # type: ignore[arg-type]
            expected_checkpoint=expected,
            checkpoint=checkpoint,
            child_run_id=result.run_id,
            failure=failure,
        )

    async def _publish_checkpoint(
        self,
        *,
        head: ChildExecutionHead,
        run_id: str,
        state: HarnessState,
        deferred_requests: DeferredToolRequests | None,
        display: CompactChildDisplay,
        terminal: bool,
        expected: ObjectRef | None,
    ) -> ObjectRef:
        reference = await self._publish_checkpoint_object(
            head=head,
            run_id=run_id,
            state=state,
            deferred_requests=deferred_requests,
            display=display,
            terminal=terminal,
        )
        await self._store.child_executions.select_checkpoint(
            execution_id=head.execution_id,
            expected=expected,
            checkpoint=reference,
            child_run_id=run_id,
        )
        async with self._lock:
            self._signal_change_locked()
        return reference

    async def _publish_checkpoint_object(
        self,
        *,
        head: ChildExecutionHead,
        run_id: str,
        state: HarnessState,
        deferred_requests: DeferredToolRequests | None,
        display: CompactChildDisplay,
        terminal: bool,
    ) -> ObjectRef:
        value = StoredChildCheckpoint(
            harness_release=harness_version,
            execution_id=head.execution_id,
            child_thread_id=head.child_thread_id,
            child_run_id=run_id,
            segment_index=head.segment_index,
            run_composition=head.run_composition,
            harness_state=state,
            deferred_requests=deferred_requests,
            display=display,
            terminal=terminal,
            created_at=datetime.now(UTC),
        )
        return (
            await self._store.objects.publish_model(
                object_kind=ObjectKind.child_checkpoint,
                value=value,
            )
        ).ref

    async def _fail_terminal_persistence(
        self,
        execution_id: str,
        exc: BaseException,
        *,
        expected_checkpoint: ObjectRef | None,
    ) -> SafeFailure:
        failure = _safe_failure(exc, fallback_code="subagent_checkpoint_failed")
        try:
            await self._store.child_executions.finish(
                execution_id=execution_id,
                status="failed",
                expected_checkpoint=expected_checkpoint,
                checkpoint=None,
                failure=failure,
            )
        except StoreError:
            pass
        return failure

    async def _fail_after_acceptance(
        self,
        execution_id: str,
        exc: BaseException,
        *,
        expected_checkpoint: ObjectRef | None,
        checkpoint: ObjectRef | None = None,
    ) -> None:
        failure = _safe_failure(exc, fallback_code="subagent_execution_failed")
        try:
            await self._store.child_executions.finish(
                execution_id=execution_id,
                status="failed",
                expected_checkpoint=expected_checkpoint,
                checkpoint=checkpoint,
                failure=failure,
            )
        except StoreError:
            return
        await self._publish_summary_by_execution(execution_id)

    async def _lose_after_acceptance(
        self,
        execution_id: str,
        *,
        expected_checkpoint: ObjectRef | None,
        checkpoint: ObjectRef | None = None,
    ) -> None:
        try:
            await self._store.child_executions.finish(
                execution_id=execution_id,
                status="lost",
                expected_checkpoint=expected_checkpoint,
                checkpoint=checkpoint,
            )
        except StoreError:
            return
        await self._publish_summary_by_execution(execution_id)

    async def _publish_summary_by_execution(self, execution_id: str) -> None:
        head = await self._store.child_executions.get(execution_id)
        if head is not None:
            await self._publish_summary(head)

    async def _publish_summary(self, head: ChildExecutionHead) -> None:
        if self._summary_hub is None:
            return
        child = await self._store.threads.get(head.child_thread_id)
        if child is None:
            return
        try:
            await self._summary_hub.publish(
                kind="child_execution",
                root_thread_id=await self._root_thread_id(child),
                thread_id=head.child_thread_id,
                execution_id=head.execution_id,
            )
        except Exception:
            return

    async def _publish_live(
        self,
        prepared: _PreparedSegment,
        events: Sequence[AguiEvent],
    ) -> None:
        if self._live_hub is None or not events:
            return
        try:
            await self._live_hub.publish(
                run_kind="child",
                root_thread_id=prepared.scope.root_thread_id,
                parent_thread_id=prepared.head.parent_thread_id,
                thread_id=prepared.stream.thread_id,
                run_id=prepared.stream.run_id,
                execution_id=prepared.head.execution_id,
                events=events,
            )
        except Exception:
            return

    async def _require_parent(self, context: SubagentOperatorContext) -> ParentRunScope:
        host_thread_id = context.host_refs.get("thread_id")
        if host_thread_id != context.parent_thread_id:
            raise RunCoordinationError(
                "The subagent parent correlation is invalid.",
                code="subagent_parent_scope_invalid",
            )
        key = (
            context.parent_thread_id,
            context.parent_run_id,
            context.parent_agent_instance_id,
        )
        async with self._lock:
            self._require_started_locked()
            scope = self._parents.get(key)
        if scope is None:
            raise RunCoordinationError(
                "The subagent request is outside the active parent Run.",
                code="subagent_parent_scope_invalid",
            )
        return scope

    async def _require_execution(
        self,
        scope: ParentRunScope,
        execution_id: str,
    ) -> ChildExecutionHead:
        return await self._require_execution_for_parent(scope.thread_id, execution_id)

    async def _require_execution_for_parent(
        self,
        parent_thread_id: str,
        execution_id: str,
    ) -> ChildExecutionHead:
        head = await self._store.child_executions.get(execution_id)
        if head is None or head.parent_thread_id != parent_thread_id:
            raise _SubagentRequestError(
                "The child execution is unavailable in this parent scope.",
                code="subagent_execution_unavailable",
            )
        return head

    async def _require_child_thread(self, head: ChildExecutionHead) -> Thread:
        thread = await self._store.threads.get(head.child_thread_id)
        if thread is None or thread.parent_thread_id != head.parent_thread_id:
            raise RunCoordinationError(
                "The retained child Thread is unavailable.",
                code="subagent_execution_unavailable",
            )
        return thread

    async def _read_checkpoint(self, head: ChildExecutionHead) -> StoredChildCheckpoint:
        reference = head.selected_checkpoint
        if reference is None:
            raise _SubagentRequestError(
                "The child execution has no selected checkpoint.",
                code="subagent_checkpoint_missing",
            )
        checkpoint = await self._store.objects.read_model(reference, StoredChildCheckpoint)
        if (
            checkpoint.execution_id != head.execution_id
            or checkpoint.child_thread_id != head.child_thread_id
            or checkpoint.child_run_id != head.child_run_id
            or checkpoint.segment_index != head.segment_index
            or checkpoint.run_composition != head.run_composition
        ):
            raise RunCoordinationError(
                "The selected child checkpoint is incompatible with its execution head.",
                code="subagent_checkpoint_incompatible",
            )
        return checkpoint

    async def _root_thread_id(self, thread: Thread) -> str:
        current = thread
        seen: set[str] = set()
        while current.parent_thread_id is not None:
            if current.thread_id in seen:
                raise RunCoordinationError(
                    "The Thread parent lineage contains a cycle.",
                    code="subagent_parent_scope_invalid",
                )
            seen.add(current.thread_id)
            parent = await self._store.threads.get(current.parent_thread_id)
            if parent is None:
                raise RunCoordinationError(
                    "The Thread parent lineage is incomplete.",
                    code="subagent_parent_scope_invalid",
                )
            current = parent
        return current.thread_id

    async def _execution_identity(self, head: ChildExecutionHead) -> tuple[str, str]:
        key = (head.child_thread_id, head.run_composition)
        if (identity := self._execution_identities.get(key)) is not None:
            self._execution_identities.move_to_end(key)
            return identity
        first = await self._store.child_executions.first_for_child(head.child_thread_id)
        if first is None or first.parent_thread_id != head.parent_thread_id:
            raise RunCoordinationError(
                "The child execution lineage is incomplete.",
                code="subagent_execution_unavailable",
            )
        initial = await self._store.objects.read_model(
            first.run_composition,
            ResolvedRunComposition,
        )
        current = (
            initial
            if head.run_composition == first.run_composition
            else await self._store.objects.read_model(
                head.run_composition,
                ResolvedRunComposition,
            )
        )
        if initial.thread_id != head.child_thread_id or current.thread_id != head.child_thread_id:
            raise RunCoordinationError(
                "The child composition belongs to another Thread.",
                code="subagent_execution_unavailable",
            )
        identity = (initial.root.roster_name, _definition_id(current.root))
        self._execution_identities[key] = identity
        if len(self._execution_identities) > 256:
            self._execution_identities.popitem(last=False)
        return identity

    async def _execution_projection(
        self,
        head: ChildExecutionHead,
        *,
        root_thread_id: str | None = None,
        include_activity: bool = False,
    ) -> ChildExecutionView:
        subagent_name, child_definition_id = await self._execution_identity(head)
        activity = None
        if include_activity:
            view = await self._execution_view(head)
            activity = _surface_activity(view.activity or SubagentActivitySnapshot(sequence=0))
        if root_thread_id is None:
            root_thread_id = await self._root_thread_id(await self._require_child_thread(head))
        async with self._lock:
            active = self._active.get(head.execution_id)
        locally_active = head.status == "running" and active is not None
        return ChildExecutionView(
            execution_id=head.execution_id,
            root_thread_id=root_thread_id,
            parent_thread_id=head.parent_thread_id,
            child_thread_id=head.child_thread_id,
            child_run_id=head.child_run_id,
            segment_index=head.segment_index,
            composition_id=head.run_composition.logical_digest,
            subagent_name=subagent_name,
            child_definition_id=child_definition_id,
            persisted_status=head.status,
            local_status="active" if locally_active else "unavailable",
            resumed_from=head.resumed_from,
            failure=None if head.failure is None else _surface_failure(head.failure),
            resumable=head.resumable,
            activity=activity,
            available_actions=("wait", "steer", "cancel") if locally_active else (),
            created_at=head.created_at,
            updated_at=head.updated_at,
            completed_at=head.completed_at,
        )

    async def _execution_view(self, head: ChildExecutionHead) -> SubagentExecutionView:
        subagent_name, child_definition_id = await self._execution_identity(head)
        async with self._lock:
            active = self._active.get(head.execution_id)
            display = active.display if active is not None else None
        if display is None:
            display = (
                (await self._read_checkpoint(head)).display
                if head.selected_checkpoint is not None
                else CompactChildDisplay()
            )
        return SubagentExecutionView(
            execution_id=head.execution_id,
            subagent_name=subagent_name,
            child_definition_id=child_definition_id,
            status=head.status,
            resumed_from=head.resumed_from,
            failure=None if head.failure is None else head.failure.model_dump(mode="json"),
            resumable=head.resumable,
            thread_id=head.child_thread_id,
            child_run_id=head.child_run_id,
            segment_index=head.segment_index,
            input=None,
            activity=_activity_snapshot(display),
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
        if head.status != "running":
            return None
        async with self._lock:
            return self._active.get(head.execution_id)

    def _require_started_locked(self) -> None:
        if not self._accepting or self._task_group is None:
            raise RunCoordinationError(
                "The subagent operator is not accepting work.",
                code="subagent_operator_unavailable",
            )

    def _signal_change_locked(self) -> None:
        self._changed.set()
        self._changed = Event()


class _DisplayCompactor:
    """Retain only bounded closed AG-UI activity."""

    def __init__(self, initial: CompactChildDisplay) -> None:
        self._activities = list(initial.activities)
        self._final_answer = initial.final_answer
        self._text: dict[str, str] = {}
        self._reasoning: dict[str, str] = {}
        self._tool_names: dict[str, str] = {}
        self._tool_arguments: dict[str, str] = {}
        self._tool_results: dict[str, str] = {}
        self._tool_ended: set[str] = set()

    def observe(self, events: Sequence[AguiEvent]) -> None:
        for event in events:
            extra = event.model_extra or {}
            metadata = ContentMetadata.from_native(extra.get("metadata"))
            if not metadata.display or extra.get("role") == "user":
                continue
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
        return CompactChildDisplay(
            activities=tuple(self._activities[-_MAX_DISPLAY_ACTIVITIES:]),
            final_answer=self._final_answer,
        )

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


def _require_edge(parent: ResolvedAgentNode, name: str) -> ResolvedSubagent:
    for edge in parent.children:
        if edge.name == name:
            return edge
    raise RunCoordinationError(
        "The selected child is absent from the parent composition.",
        code="subagent_plan_invalid",
    )


def _initial_child_configuration(
    parent: ResolvedRunComposition,
    edge: ResolvedSubagent,
) -> ThreadConfiguration:
    source = (
        AgentResourceSource(id=edge.source_id)
        if edge.source_kind == "agent"
        else MarkdownSubagentSource(id=edge.source_id)
    )
    return ThreadConfiguration(
        version=1,
        project_id=parent.project_id,
        local_roots=parent.project_roots,
        agent_source=source,
        environment_profile_id=parent.environment_profile.profile_id,
        environment_bindings=tuple(item.selection for item in parent.environment_bindings),
        default_environment=parent.default_environment,
        harness_plugin_ids=tuple(item.plugin_id for item in edge.definition.harness_plugins),
        environment_run_extension_ids=tuple(item.extension_id for item in parent.environment_run_extensions),
        mcp_server_ids=tuple(item.server_id for item in edge.definition.mcp_servers),
    )


def _selection(thread_id: str, configuration: ThreadConfiguration) -> ThreadCompositionSelection:
    source = configuration.agent_source
    return ThreadCompositionSelection(
        thread_id=thread_id,
        version=configuration.version,
        project_id=configuration.project_id,
        local_roots=configuration.local_roots,
        agent_source_kind=source.kind,
        agent_source_id=source.id,
        default_model_id=configuration.default_model_id,
        environment_profile_id=configuration.environment_profile_id,
        environment_bindings=configuration.environment_bindings,
        default_environment=configuration.default_environment,
        harness_plugin_ids=configuration.harness_plugin_ids,
        environment_run_extension_ids=configuration.environment_run_extension_ids,
        mcp_server_ids=configuration.mcp_server_ids,
    )


def _definition_id(node: ResolvedAgentNode) -> str:
    return f"a13n-harness-ui:{node.source_kind}:{node.source_id}"


def _public_id(kind: str) -> str:
    return f"{kind}-{uuid4().hex[:20]}"


def _async_view(
    head: ChildExecutionHead,
    *,
    subagent_name: str,
    child_definition_id: str,
) -> AsyncExecutionView:
    return AsyncExecutionView(
        execution_id=head.execution_id,
        subagent_name=subagent_name,
        child_definition_id=child_definition_id,
        status=head.status,
        resumed_from=head.resumed_from,
        failure=None if head.failure is None else head.failure.model_dump(mode="json"),
        resumable=head.resumable,
        thread_id=head.child_thread_id,
        child_run_id=head.child_run_id,
        segment_index=head.segment_index,
    )


def _intersect_usage_limits(*values: UsageLimits | None) -> UsageLimits | None:
    present = tuple(value for value in values if value is not None)
    if not present:
        return None
    fields: dict[str, int | Decimal | bool | None] = {}
    for name in _USAGE_LIMIT_FIELDS:
        ceilings = [getattr(value, name) for value in present if getattr(value, name) is not None]
        fields[name] = min(ceilings) if ceilings else None
    fields["count_tokens_before_request"] = any(value.count_tokens_before_request for value in present)
    return UsageLimits(**fields)  # type: ignore[arg-type]


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
                {
                    "status": "unavailable",
                    "reason": "Deferred interaction is unavailable to async child runs.",
                }
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
    final_answer = display.final_answer
    if isinstance(output, str) and output:
        final_answer = output
        if not (activities and activities[-1].kind == "text" and activities[-1].text == output):
            activities.append(CompactChildActivity(kind="text", text=output[:_MAX_ACTIVITY_TEXT]))
    activities.append(CompactChildActivity(kind="completion"))
    return CompactChildDisplay(
        activities=tuple(activities[-_MAX_DISPLAY_ACTIVITIES:]),
        final_answer=final_answer,
    )


def _with_failure(display: CompactChildDisplay, failure: SafeFailure) -> CompactChildDisplay:
    # Keep the original SafeFailure for settlement; only the compact display
    # uses the existing explicitly marked failure preview.
    activities = [*display.activities, CompactChildActivity(kind="failure", text=_surface_failure(failure).message)]
    return CompactChildDisplay(
        activities=tuple(activities[-_MAX_DISPLAY_ACTIVITIES:]),
        final_answer=display.final_answer,
    )


def _activity_snapshot(display: CompactChildDisplay) -> SubagentActivitySnapshot | None:
    if not display.activities:
        return None
    previews = [activity.text for activity in display.activities if activity.kind in {"text", "thinking", "failure"}]
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
        dropped_tool_calls=max(
            0,
            len([item for item in display.activities if item.kind == "tool"]) - len(tools),
        ),
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
    return _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f'{match.group(1)}{match.group(2)}"[REDACTED]"',
        without_bearer,
    )


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


def _encode_child_cursor(value: _ChildCursor) -> str:
    return base64.urlsafe_b64encode(value.model_dump_json().encode("utf-8")).decode("ascii").rstrip("=")


def _decode_child_cursor(value: str) -> _ChildCursor:
    if not value or len(value) > 4096:
        raise RunCoordinationError("Child cursor is invalid.", code="child_cursor_invalid")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = base64.b64decode(padded, altchars=b"-_", validate=True)
        return _ChildCursor.model_validate_json(payload, strict=True)
    except (ValueError, ValidationError) as exc:
        raise RunCoordinationError("Child cursor is invalid.", code="child_cursor_invalid") from exc


def _surface_activity(value: SubagentActivitySnapshot) -> ChildActivityView:
    return ChildActivityView(
        sequence=value.sequence,
        output_preview=value.output_preview,
        output_truncated=value.output_truncated,
        active_tool_calls=tuple(_surface_tool_call(item) for item in value.active_tool_calls),
        recent_tool_calls=tuple(_surface_tool_call(item) for item in value.recent_tool_calls),
        dropped_tool_calls=value.dropped_tool_calls,
    )


def _surface_tool_call(value: SubagentToolCallSnapshot) -> ChildToolCallView:
    return ChildToolCallView(
        tool_call_id=value.tool_call_id,
        tool_name=value.tool_name,
        status=value.status,
        arguments=value.arguments,
        result=value.result,
    )


def _surface_failure(value: SafeFailure) -> FailureView:
    message = value.message
    if len(message) > _MAX_FAILURE_MESSAGE:
        message = message[: _MAX_FAILURE_MESSAGE - 23] + "\n...[message truncated]"
    return FailureView(
        code=value.code[:_MAX_FAILURE_CODE],
        message=message,
        details=_bounded_failure_details(value.details),
        retry_hint=value.retry_hint,
    )


def _bounded_failure_details(value: object) -> dict[str, JsonValue] | None:
    try:
        details = _JSON_ADAPTER.validate_python(value)
        if not isinstance(details, dict):
            return None
        encoded = json.dumps(details, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, ValidationError):
        return None
    return details if len(encoded) <= _MAX_FAILURE_DETAILS_BYTES else None


def _wait_timeout(value: float | None) -> float:
    if value is None:
        return _DEFAULT_WAIT_SECONDS
    if not math.isfinite(value) or value < 0:
        raise _SubagentRequestError(
            "Child wait timeout must be a finite non-negative number.",
            code="child_wait_invalid",
        )
    return min(value, _MAX_WAIT_SECONDS)


def _safe_failure(exc: BaseException, *, fallback_code: str) -> SafeFailure:
    code = exc.code if isinstance(exc, HarnessUiError) else fallback_code
    message = _redact_text(str(exc).strip()) or "The asynchronous child execution failed."
    return SafeFailure(code=code[:128], message=message[:4096])


__all__ = ["HarnessUiSubagentOperator", "ParentRunScope"]
