"""Executable Agents and the canonical Harness run stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from traceback import walk_tb
from typing import TYPE_CHECKING, Any, cast, overload
from uuid import uuid4

from a13n_logging import get_logger
from anyio import CancelScope
from opentelemetry.trace import StatusCode
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_ai import Agent
from pydantic_ai.agent import AgentRunEvents
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import AgentRunError, ModelHTTPError, RunCancelled, UsageLimitExceeded, UserError
from pydantic_ai.messages import (
    AgentStreamEvent,
    EnqueuedMessagesEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
)
from pydantic_ai.run import AgentRunResult, AgentRunResultEvent
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import RunUsage, UsageLimits

from a13n_harness._capability_contract import _validate_capability_source
from a13n_harness._output_contract import _business_output_contains_deferred_value
from a13n_harness.capabilities.context import (
    COMPACTION_CAPABILITY_ID,
    HANDOFF_CAPABILITY_ID,
)
from a13n_harness.capabilities.steering import (
    SteeringBridge,
)
from a13n_harness.content import (
    content_items,
    native_content,
    normalize_request_history,
    replace_request_parts,
    request_input_content,
    request_parts,
)
from a13n_harness.context import (
    AgentContext,
    RunBindings,
    SubagentCollection,
    _CapabilityProvenance,
)
from a13n_harness.environment.providers import BoundEnvironment, EnvironmentRuntime
from a13n_harness.environment.sources import EnvironmentEntry, normalize_environment_inputs
from a13n_harness.errors import (
    HarnessError,
    PluginError,
    RetryHint,
    RunCleanupError,
    RunError,
    StateError,
)
from a13n_harness.events import (
    AgentStreamEventProtocol,
    EnvironmentChangedPayload,
    HarnessEvent,
    HarnessEventEmitter,
    HarnessExtensionEvent,
    HarnessRunResultEvent,
    HarnessStreamEvent,
    InputSource,
    ModelRetryScheduledPayload,
    _ChildEventForwarder,
    _RunEventEmitter,
    input_events,
)
from a13n_harness.input import (
    RunInputFactory,
    RunInputValue,
    RunPreparationContext,
    SemanticRunInput,
    normalize_input,
)
from a13n_harness.models.binding import RunModelResolver
from a13n_harness.observation import (
    _LogicalRunObservation,
    _ObservationRuntime,
    observe_operation,
    observe_output,
    observe_phase,
    record_span_metadata,
)
from a13n_harness.plugins import (
    AbstractHarnessPlugin,
    BoundPluginContext,
    PluginRunExchange,
    PluginRunItem,
    PluginRunNext,
    PluginRunResponse,
    bind_run_plugins,
)
from a13n_harness.pricing import AbstractModelCostCapability
from a13n_harness.providers.environment.models import EnvironmentChange, EnvironmentError
from a13n_harness.recovery import (
    InterruptedResponseTracker,
    ToolRecoveryMode,
    is_recoverable_model_failure,
    normalize_interrupted_history,
    prepare_tool_recovery,
)
from a13n_harness.result import HarnessRunResult, SafeFailure
from a13n_harness.spec import AgentSpec as HarnessAgentSpec
from a13n_harness.spec import _default_usage_limits
from a13n_harness.state import AgentContextState, HarnessState
from a13n_harness.tools.deferred import (
    DeferredInputState,
    DeferredToolResume,
    preflight_deferred_resume,
)
from a13n_harness.usage import RunUsageLedger, RunUsageSummary, UsageRecord, UsageSnapshot

if TYPE_CHECKING:
    from a13n_harness.builder import AgentDefinition


_EXTENSION_EVENT_ADAPTER = TypeAdapter(HarnessExtensionEvent)


def _report_model_failure(
    error: BaseException, *, thread_id: str, run_id: str, retrying: bool = False
) -> dict[str, JsonValue]:
    """Keep actionable structure without logging provider bodies or exception payloads."""
    details: dict[str, JsonValue] = {"exception_type": type(error).__name__}
    if isinstance(error, ModelHTTPError):
        details["status_code"] = error.status_code
    locations: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        locations.append(type(current).__name__)
        for frame, line in list(walk_tb(current.__traceback__))[-32:]:
            locations.append(f"  {frame.f_code.co_filename}:{line} in {frame.f_code.co_name}")
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    logger = get_logger(__name__)
    log = logger.debug if retrying else logger.warning
    log(
        "Model execution interrupted: thread_id=%s run_id=%s details=%s\n%s",
        thread_id,
        run_id,
        details,
        "\n".join(locations),
    )
    return details


@dataclass(frozen=True, slots=True)
class _ResponsePumpTerminal:
    error: BaseException | None = None


@dataclass(slots=True)
class _EnvironmentChangeDrain:
    requested: asyncio.Event = field(default_factory=asyncio.Event)
    drained: asyncio.Event = field(default_factory=asyncio.Event)
    progress: asyncio.Event = field(default_factory=asyncio.Event)
    cursor: int | None = None
    terminal_sequence: int | None = None


def _consume_finished_task(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


async def _stop_environment_event_task(task: asyncio.Task[None]) -> None:
    if not task.done():
        task.cancel()
    try:
        done, pending = await asyncio.wait((task,), timeout=5.0)
    except BaseException:
        if not task.done():
            task.add_done_callback(_consume_finished_task)
        raise
    if pending:
        task.add_done_callback(_consume_finished_task)
        raise RunError(
            "Environment change event adapter did not stop before cleanup deadline.",
            code="event_adapter_cleanup_timeout",
        )
    if task in done and not task.cancelled():
        task.result()


async def _emit_environment_change_events(
    context: AgentContext,
    drain: _EnvironmentChangeDrain,
) -> None:
    """Adapt the run-local Environment journal through the logical terminal fence."""
    environment = context.environment
    cursor = 0
    drain.cursor = cursor
    while True:
        terminal_sequence = drain.terminal_sequence
        if terminal_sequence is not None and cursor >= terminal_sequence:
            drain.drained.set()
            drain.progress.set()
            return

        read_task = asyncio.create_task(environment._read_changes(after_sequence=cursor, wait=True))
        terminal_task = asyncio.create_task(drain.requested.wait())
        try:
            done, _ = await asyncio.wait({read_task, terminal_task}, return_when=asyncio.FIRST_COMPLETED)
            if terminal_task in done:
                if not read_task.done():
                    read_task.cancel()
                    await asyncio.gather(read_task, return_exceptions=True)
                changes = await environment._read_changes(after_sequence=cursor, wait=False)
            else:
                changes = read_task.result()
        except EnvironmentError as exc:
            if exc.code == "environment_closed":
                terminal_sequence = drain.terminal_sequence
                if terminal_sequence is not None and cursor >= terminal_sequence:
                    drain.drained.set()
                    drain.progress.set()
                return
            raise
        finally:
            for task in (read_task, terminal_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(read_task, terminal_task, return_exceptions=True)

        for change in changes:
            try:
                await context.events.emit(_environment_change_event(change))
            except RunError as exc:
                if exc.code in {"event_emitter_closed", "event_consumer_stopped"}:
                    return
                raise
            cursor = change.sequence
            progress = drain.progress
            drain.cursor = cursor
            drain.progress = asyncio.Event()
            progress.set()


def _environment_change_event(change: EnvironmentChange) -> HarnessExtensionEvent:
    payload = EnvironmentChangedPayload(
        sequence=change.sequence,
        kind=change.kind,
        name=change.name,
        previous_default=change.previous_default,
        current_default=change.current_default,
    )
    return HarnessExtensionEvent(kind="context", payload=payload.model_dump(mode="json"))


def _normalize_toolset_instructions(agent: AgentSpec) -> bool:
    if isinstance(agent, HarnessAgentSpec):
        return agent.toolset_instructions
    return True


def _usage_limits_from_spec(agent: AgentSpec) -> UsageLimits:
    if isinstance(agent, HarnessAgentSpec):
        return deepcopy(agent.usage_limits)
    return _default_usage_limits()


def _reconcile_system_prompt(
    messages: Sequence[ModelMessage],
    system_prompt: Sequence[str],
) -> tuple[ModelMessage, ...]:
    if not messages or (isinstance(messages[-1], ModelResponse) and messages[-1].state == "suspended"):
        return tuple(messages)

    reconciled = list(messages)
    first_request = True
    for index, message in enumerate(reconciled):
        if not isinstance(message, ModelRequest):
            continue
        parts = [
            (part, annotations)
            for part, annotations in request_parts(message)
            if not isinstance(part, SystemPromptPart)
        ]
        if first_request:
            parts = [*[(SystemPromptPart(content=block), None) for block in system_prompt], *parts]
            first_request = False
        if tuple(part for part, _ in parts) != tuple(message.parts):
            reconciled[index] = replace_request_parts(message, parts)
    return tuple(reconciled)


class ExecutableAgent[OutputT]:
    """Reusable code-built Agent that creates one fresh context per invocation."""

    def __init__(
        self,
        *,
        definition: AgentDefinition[OutputT],
        agent: Agent[AgentContext, OutputT | DeferredToolRequests],
        system_prompt: tuple[str, ...],
        output_adapter: TypeAdapter[Any],
        plugins: tuple[AbstractHarnessPlugin, ...],
        subagents: SubagentCollection,
        definition_reserved_capability_ids: frozenset[str],
        model_inference: RunModelResolver,
        observation: _ObservationRuntime,
        model_cost: AbstractModelCostCapability,
    ) -> None:
        self.definition = definition
        self.subagents = subagents
        self._agent = agent
        self._model_cost = model_cost
        self._system_prompt = system_prompt
        self._output_adapter = output_adapter
        self._plugins = plugins
        self._definition_reserved_capability_ids = definition_reserved_capability_ids
        self._model_inference = model_inference
        self._observation = observation

    def definition_usage_limits(self) -> UsageLimits:
        """Return a detached definition baseline, independent of per-Run overrides."""
        return _usage_limits_from_spec(self.definition.agent)

    @overload
    async def run(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        environment: EnvironmentEntry,
        environments: None = None,
        default_environment: None = None,
        bindings: RunBindings | None = None,
        previous_state: HarnessState | None = None,
        resume_usage: bool = False,
        tool_recovery: ToolRecoveryMode = "declared",
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunResult[OutputT]: ...

    @overload
    async def run(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        environment: None = None,
        environments: Mapping[str, EnvironmentEntry],
        default_environment: str | None = None,
        bindings: RunBindings | None = None,
        previous_state: HarnessState | None = None,
        resume_usage: bool = False,
        tool_recovery: ToolRecoveryMode = "declared",
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunResult[OutputT]: ...

    @overload
    async def run(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        environment: None = None,
        environments: None = None,
        default_environment: None = None,
        bindings: RunBindings | None = None,
        previous_state: HarnessState | None = None,
        resume_usage: bool = False,
        tool_recovery: ToolRecoveryMode = "declared",
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunResult[OutputT]: ...

    async def run(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        environment: EnvironmentEntry | None = None,
        environments: Mapping[str, EnvironmentEntry] | None = None,
        default_environment: str | None = None,
        bindings: RunBindings | None = None,
        previous_state: HarnessState | None = None,
        resume_usage: bool = False,
        tool_recovery: ToolRecoveryMode = "declared",
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunResult[OutputT]:
        """Consume the canonical stream and return its sole terminal result."""
        async with self._stream(
            input,
            input_factory=input_factory,
            environment=environment,
            environments=environments,
            default_environment=default_environment,
            bindings=bindings,
            previous_state=previous_state,
            resume_usage=resume_usage,
            tool_recovery=tool_recovery,
            deferred_resume=deferred_resume,
            usage=usage,
            usage_limits=usage_limits,
        ) as stream:
            async for item in stream:
                if isinstance(item, HarnessRunResultEvent):
                    return item.result
        raise RunError("The run ended without a terminal result.", code="run_result_missing")

    @overload
    def stream(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        environment: EnvironmentEntry,
        environments: None = None,
        default_environment: None = None,
        bindings: RunBindings | None = None,
        previous_state: HarnessState | None = None,
        resume_usage: bool = False,
        tool_recovery: ToolRecoveryMode = "declared",
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunStream[OutputT]: ...

    @overload
    def stream(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        environment: None = None,
        environments: Mapping[str, EnvironmentEntry],
        default_environment: str | None = None,
        bindings: RunBindings | None = None,
        previous_state: HarnessState | None = None,
        resume_usage: bool = False,
        tool_recovery: ToolRecoveryMode = "declared",
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunStream[OutputT]: ...

    @overload
    def stream(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        environment: None = None,
        environments: None = None,
        default_environment: None = None,
        bindings: RunBindings | None = None,
        previous_state: HarnessState | None = None,
        resume_usage: bool = False,
        tool_recovery: ToolRecoveryMode = "declared",
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunStream[OutputT]: ...

    def stream(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        environment: EnvironmentEntry | None = None,
        environments: Mapping[str, EnvironmentEntry] | None = None,
        default_environment: str | None = None,
        bindings: RunBindings | None = None,
        previous_state: HarnessState | None = None,
        resume_usage: bool = False,
        tool_recovery: ToolRecoveryMode = "declared",
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunStream[OutputT]:
        """Create a lazy, single-entry canonical Harness stream."""
        return self._stream(
            input,
            input_factory=input_factory,
            environment=environment,
            environments=environments,
            default_environment=default_environment,
            bindings=bindings,
            previous_state=previous_state,
            resume_usage=resume_usage,
            tool_recovery=tool_recovery,
            deferred_resume=deferred_resume,
            usage=usage,
            usage_limits=usage_limits,
        )

    def _stream(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        environment: EnvironmentEntry | None = None,
        environments: Mapping[str, EnvironmentEntry] | None = None,
        default_environment: str | None = None,
        bindings: RunBindings | None = None,
        previous_state: HarnessState | None = None,
        resume_usage: bool = False,
        tool_recovery: ToolRecoveryMode = "declared",
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunStream[OutputT]:
        if tool_recovery not in {"declared", "never", "always"}:
            raise ValueError("tool_recovery must be 'declared', 'never', or 'always'")
        if input is not None and input_factory is not None:
            raise RunError(
                "input and input_factory are mutually exclusive.",
                code="input_source_conflict",
            )
        if bindings is not None and not isinstance(bindings, RunBindings):
            raise RunError("bindings must be a RunBindings value.", code="run_bindings_invalid")
        resolved_bindings = bindings if bindings is not None else RunBindings.embedded()
        environment_binding = normalize_environment_inputs(
            environment=environment,
            environments=environments,
            default_environment=default_environment,
            advanced_binding=resolved_bindings.environment,
        )
        run_reserved_ids = _validate_capability_source(resolved_bindings.capabilities, source="run")
        normalized_resume = (
            preflight_deferred_resume(deferred_resume, previous_state=previous_state)
            if deferred_resume is not None
            else None
        )
        effective_usage_limits = deepcopy(usage_limits) if usage_limits is not None else self.definition_usage_limits()
        return HarnessRunStream(
            executable=self,
            input=input,
            input_factory=input_factory,
            bindings=resolved_bindings,
            environment_binding=environment_binding,
            previous_state=previous_state,
            resume_usage=resume_usage,
            tool_recovery=tool_recovery,
            deferred_resume=normalized_resume,
            run_reserved_capability_ids=run_reserved_ids,
            usage=usage,
            usage_limits=effective_usage_limits,
        )


class HarnessRunStream[OutputT](AsyncIterator[HarnessStreamEvent[OutputT]]):
    """Own one logical Run: preparation, plugin streaming, native attempts, and teardown."""

    def __init__(
        self,
        *,
        executable: ExecutableAgent[OutputT],
        input: RunInputValue | None,
        input_factory: RunInputFactory | None,
        bindings: RunBindings,
        environment_binding: EnvironmentRuntime,
        previous_state: HarnessState | None,
        resume_usage: bool,
        tool_recovery: ToolRecoveryMode,
        deferred_resume: DeferredToolResume | None,
        run_reserved_capability_ids: frozenset[str],
        usage: RunUsage | None,
        usage_limits: UsageLimits | None,
    ) -> None:
        self._executable = executable
        self._input = input
        self._input_factory = input_factory
        self._bindings = bindings
        self._environment_runtime = environment_binding
        self._previous_state = (
            previous_state.model_copy(deep=True) if previous_state is not None else HarnessState.new()
        )
        self.thread_id = self._previous_state.thread_id
        self._usage_snapshot = UsageSnapshot.from_state(self._previous_state) if resume_usage else None
        if resume_usage and self._usage_snapshot is None:
            raise StateError("Accounting resume requires usage state.", code="usage_state_missing")
        self.run_id = f"run-{uuid4().hex}"
        self._tool_recovery = (
            prepare_tool_recovery(
                self._previous_state.message_history,
                tool_recovery,
                deferred_resume,
            )
            if deferred_resume is None or deferred_resume.recovery
            else None
        )
        self._deferred_input = DeferredInputState(deferred_resume)
        self._deferred_resume = (
            deferred_resume if deferred_resume is not None and not deferred_resume.recovery else None
        )
        self._run_reserved_capability_ids = run_reserved_capability_ids
        self._usage = usage if usage is not None else RunUsage()
        self._usage_limits = usage_limits

        # Environment and plugin scopes belong to the logical Run, not a native attempt.
        self._environment_ready: asyncio.Future[BoundEnvironment] | None = None
        self._environment_close_requested = asyncio.Event()
        self._environment_lifecycle_task: asyncio.Task[None] | None = None
        self._environment_ready_delivered = False
        self._context: AgentContext | None = None
        self._response: PluginRunResponse[OutputT] | None = None
        self._responses: dict[int, tuple[int, PluginRunResponse[OutputT]]] = {}
        self._closed_response_ids: set[int] = set()
        self._run_attachments_closed = False

        # Native attempts update the complete live history while sharing usage and cancellation.
        self._attempt_events: AgentRunEvents[OutputT | DeferredToolRequests] | None = None
        self._latest_messages: tuple[ModelMessage, ...] = self._previous_state.message_history
        self._new_message_index = len(self._latest_messages)
        self._cancel_event = asyncio.Event()

        self._emitter = _RunEventEmitter(self.thread_id, self.run_id)
        self._environment_change_drain = _EnvironmentChangeDrain()
        self._environment_event_task: asyncio.Task[None] | None = None
        self._response_pump_task: asyncio.Task[None] | None = None
        self._response_queue: asyncio.Queue[PluginRunItem[OutputT] | _ResponsePumpTerminal] = asyncio.Queue(maxsize=1)
        self._response_next_task: asyncio.Task[PluginRunItem[OutputT] | _ResponsePumpTerminal] | None = None
        self._logical_events_started = False
        self._source_sequence = 0
        self._public_sequence = 0
        self._last_public_child_sequence_by_run: dict[str, int] = {}

        # Validation, shutdown checkpointing, and successful delivery are distinct boundaries.
        self._validated_outcome: HarnessRunResult[OutputT] | None = None
        self._pending_result: HarnessRunResult[OutputT] | None = None
        self._terminal_close_task: asyncio.Task[None] | None = None
        self._delivered_result: HarnessRunResult[OutputT] | None = None
        self._shutdown_state: HarnessState | None = None
        self._shutdown_state_error: BaseException | None = None
        self._source_cleanup_failures: list[BaseException] = []
        self._diagnostic_error: BaseException | None = None

        self._entered = False
        self._consumer_claimed = False
        self._reading_next = False
        self._closed = False
        self._observation: _LogicalRunObservation | None = None

    @property
    def context(self) -> AgentContext:
        """Return the fresh run context after stream entry."""
        if self._context is None:
            raise RunError("The stream is not entered.", code="run_not_active")
        return self._context

    @property
    def pending_deferred_input(self) -> DeferredToolResume | None:
        """Detached accepted facts not yet incorporated by this Run.

        Read alongside the current canonical checkpoint, never an older snapshot.
        Consumption is reconciled before history transformations and stays retired.
        The value remains available after stream shutdown for terminal publication.
        """
        pending = self._deferred_input.pending
        return (
            None
            if pending is None
            else DeferredToolResume(pending.requests, pending.results, recovery=pending.recovery)
        )

    @property
    def result(self) -> HarnessRunResult[OutputT] | None:
        """Return the terminal result only after its result event was delivered."""
        return self._delivered_result

    @property
    def outcome(self) -> HarnessRunResult[OutputT] | None:
        """Return the last validated candidate after shutdown, not a success receipt.

        Unlike result, this is also available when cleanup or consumer cancellation
        prevents terminal delivery. Hosts must preserve state and deferred requests
        together while keeping the original execution/cleanup failure authoritative.
        """
        return self._validated_outcome if self._closed else None

    @property
    def diagnostic_error(self) -> BaseException | None:
        """Return the terminal exception for private Host diagnostics, never serialization."""
        return self._diagnostic_error

    @property
    def usage(self) -> RunUsageSummary:
        """Return a detached run-local usage summary."""
        return self.context.usage_attribution.summary(tool_calls=self._usage.tool_calls)

    @property
    def usage_records(self) -> tuple[UsageRecord, ...]:
        return self.context.usage_records

    def _bind_parent_event_forwarder(self, parent: HarnessEventEmitter) -> _ChildEventForwarder:
        """Bind this exact stream as a validated child of one active parent emitter."""
        if not isinstance(parent, _RunEventEmitter):
            raise RunError("Inline child parent emitter is invalid.", code="child_event_invalid")
        return parent.bind_child(self._emitter)

    async def __aenter__(self) -> HarnessRunStream[OutputT]:
        if self._entered:
            raise RunError("HarnessRunStream cannot be entered more than once.", code="run_stream_reused")
        self._entered = True
        self._observation = self._executable._observation.start_run(
            thread_id=self.thread_id,
            run_id=self.run_id,
            instance=self._bindings.instance,
            observation_context=self._bindings.observation,
        )
        activation = (
            self._observation.activate() if self._observation is not None else _LogicalRunObservation.suppress()
        )
        try:
            with observe_phase("prepare") as phase:
                record_span_metadata(
                    phase,
                    {
                        "prepare.input_factory": self._input_factory is not None,
                        "prepare.deferred_resume": self._deferred_resume is not None,
                        "prepare.plugin_count": len(self._executable._plugins),
                    },
                )
                phase.set_attribute("a13n.phase.step", "environment")
                self._environment_ready = asyncio.get_running_loop().create_future()
                # This task outlives preparation and also owns Environment teardown.
                # Keep its ambient parent at Run scope, not an already-ended prepare span.
                environment_activation = (
                    self._observation.activate() if self._observation is not None else _LogicalRunObservation.suppress()
                )
                try:
                    self._environment_lifecycle_task = asyncio.create_task(self._run_environment_lifecycle())
                finally:
                    _LogicalRunObservation.deactivate(environment_activation)
                environment = await asyncio.shield(self._environment_ready)
                self._environment_ready_delivered = True
                phase.set_attribute("a13n.phase.step", "input")
                preparation = RunPreparationContext(
                    run_id=self.run_id,
                    instance=self._bindings.instance,
                    environment=environment,
                    metadata=self._bindings.metadata,
                )
                input_value = self._input
                if self._input_factory is not None:
                    try:
                        input_value = await self._input_factory(preparation)
                    except Exception as exc:
                        raise RunError("Run input factory failed.", code="input_factory_failed") from exc
                semantic_input = normalize_input(input_value)
                if self._observation is not None:
                    self._observation.record_input(
                        semantic_input.value,
                        kind="deferred_response" if self._deferred_resume is not None else "prompt",
                    )
                phase.set_attribute("a13n.phase.step", "context")
                context = await self._create_context(environment)
                self._context = context
                phase.set_attribute("a13n.phase.step", "plugins")
                run_plugins = await bind_run_plugins(self._executable._plugins, context)
                exchange = PluginRunExchange(
                    input=semantic_input,
                    context=context,
                    _state_exporter=self.export_state,
                )
                self._response = self._build_plugin_response(run_plugins, 0, exchange)
                record_span_metadata(
                    phase,
                    {
                        "phase.status": "completed",
                        "prepare.capability_count": len(self._executable.definition.capabilities),
                    },
                )
                observe_output(
                    phase,
                    {"environment_bound": True, "context_ready": True, "plugin_count": len(run_plugins)},
                    status="prepared",
                )
                return self
        except asyncio.CancelledError as exc:
            await self._close_resources(outcome=None, cancellation=exc)
            raise
        except BaseException as exc:
            await self._close_resources(outcome=None, failure=exc)
            raise
        finally:
            _LogicalRunObservation.deactivate(activation)

    async def _create_context(self, environment: BoundEnvironment) -> AgentContext:
        bindings = self._bindings
        agent_spec = self._executable.definition.agent
        plugin_context = BoundPluginContext()
        context_state = AgentContextState(self._previous_state.agent_context_state)
        usage_attribution = RunUsageLedger(
            run_id=self.run_id,
            instance=bindings.instance,
            events=self._emitter,
            reporter=bindings.usage_reporter,
            limits=self._usage_limits,
            baseline=self._usage,
            thread_id=self.thread_id,
            state=context_state,
            snapshot=self._usage_snapshot,
        )
        # Native tool admission must include restored calls, while the explicit
        # Host baseline remains disjoint from the Context-owned accounting scope.
        if self._usage_snapshot is not None:
            self._usage.tool_calls += self._usage_snapshot.tool_calls
        usage_attribution.cost_capability = bindings._inherited_model_cost or self._executable._model_cost
        await usage_attribution.save()
        return AgentContext(
            run_id=self.run_id,
            thread_id=self._previous_state.thread_id,
            instance=bindings.instance,
            configuration=bindings.configuration,
            state=context_state,
            environment=environment,
            model_resolver=bindings.model_resolver,
            model_characteristics=(
                agent_spec.model_characteristics if isinstance(agent_spec, HarnessAgentSpec) else None
            ),
            _model_inference=self._executable._model_inference,
            toolset_instructions=(
                bindings.toolset_instructions
                if bindings.toolset_instructions is not None
                else _normalize_toolset_instructions(agent_spec)
            ),
            _toolset_instructions_override=bindings.toolset_instructions,
            model_context=bindings.model_context,
            model_call_check=bindings.model_call_check,
            usage_reporter=bindings.usage_reporter,
            _inherited_model_cost=bindings._inherited_model_cost,
            plugins=plugin_context,
            subagents=self._executable.subagents,
            events=self._emitter,
            usage_attribution=usage_attribution,
            deferred_resume=self._deferred_resume,
            _deferred_input=self._deferred_input,
            deferred_tools_supported=bindings.deferred_tools_supported,
            _tool_recovery=self._tool_recovery,
            metadata=bindings.metadata,
            _steering=SteeringBridge(
                context_state,
                run_id=self.run_id,
                retain_inputs=bool(
                    {COMPACTION_CAPABILITY_ID, HANDOFF_CAPABILITY_ID}
                    & self._executable._definition_reserved_capability_ids
                ),
                events=self._emitter,
            ),
            web=bindings.web,
            document_converter=bindings.document_converter,
            file_media_understanding=bindings.file_media_understanding,
            skill_selection=bindings.skill_selection,
            task_state=bindings.task_state,
            working_state_observer=bindings.working_state_observer,
            client_toolsets=bindings.client_toolsets,
            tool_result_directory=bindings.tool_result_directory,
            _capability_provenance=_CapabilityProvenance(
                definition_ids=self._executable._definition_reserved_capability_ids,
                run_ids=self._run_reserved_capability_ids,
            ),
        )

    async def _run_environment_lifecycle(self) -> None:
        ready = self._environment_ready
        assert ready is not None
        try:
            async with self._environment_runtime.bind(
                thread_id=self._previous_state.thread_id,
                run_id=self.run_id,
                instance=self._bindings.instance,
                host_refs=self._bindings.instance.host_refs,
            ) as environment:
                await self._environment_runtime._activate()
                if not ready.done():
                    ready.set_result(environment)
                await self._environment_close_requested.wait()
        except asyncio.CancelledError:
            if not ready.done():
                ready.cancel()
            raise
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
                return
            raise

    async def _close_environment_lifecycle(self) -> None:
        task = self._environment_lifecycle_task
        self._environment_lifecycle_task = None
        if task is None:
            return

        abort_entry = not self._environment_ready_delivered
        if abort_entry:
            ready = self._environment_ready
            if ready is not None and not ready.done():
                ready.cancel()
            if not task.done():
                task.cancel()
        else:
            self._environment_close_requested.set()

        pending_cancellation: asyncio.CancelledError | None = None
        current_task = asyncio.current_task()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if current_task is not None and current_task.cancelling():
                    pending_cancellation = pending_cancellation or exc
                    if abort_entry and not task.done():
                        task.cancel()
                continue

        lifecycle_error: BaseException | None = None
        if task.cancelled():
            if not abort_entry:
                lifecycle_error = asyncio.CancelledError("Environment lifecycle was cancelled during cleanup.")
        else:
            lifecycle_error = task.exception()
        if pending_cancellation is not None:
            if lifecycle_error is not None:
                pending_cancellation.add_note(f"Environment lifecycle cleanup also failed: {lifecycle_error!r}")
            raise pending_cancellation
        if lifecycle_error is not None:
            raise lifecycle_error

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, traceback
        activation = (
            self._observation.activate() if self._observation is not None else _LogicalRunObservation.suppress()
        )
        try:
            if self._terminal_close_task is not None:
                await self._terminal_close_task
            elif not self._closed:
                cancellation = exc_value if isinstance(exc_value, asyncio.CancelledError) else None
                failure = exc_value if isinstance(exc_value, BaseException) else None
                await self._close_resources(
                    outcome=self._validated_outcome,
                    cancellation=cancellation,
                    failure=failure,
                )
        finally:
            _LogicalRunObservation.deactivate(activation)

    def __aiter__(self) -> HarnessRunStream[OutputT]:
        if not self._entered or self._closed:
            raise RunError("The stream is not active.", code="run_not_active")
        if self._consumer_claimed:
            raise RunError("HarnessRunStream has exactly one consumer.", code="run_stream_reused")
        self._consumer_claimed = True
        return self

    async def __anext__(self) -> HarnessStreamEvent[OutputT]:
        if not self._entered or self._delivered_result is not None or (self._closed and self._pending_result is None):
            raise StopAsyncIteration
        if self._reading_next:
            raise RunError(
                "Concurrent iteration of HarnessRunStream is not supported.",
                code="run_stream_concurrent_next",
            )
        self._reading_next = True
        activation = (
            self._observation.activate() if self._observation is not None else _LogicalRunObservation.suppress()
        )
        try:
            return await self._next_item()
        finally:
            _LogicalRunObservation.deactivate(activation)
            self._reading_next = False

    async def _next_item(self) -> HarnessStreamEvent[OutputT]:
        assert self._response is not None
        try:
            self._start_logical_event_mux()
            if self._pending_result is None:
                if self._response_next_task is None:
                    self._response_next_task = asyncio.create_task(self._response_queue.get())
                task = self._response_next_task
                wait_for: set[asyncio.Task[Any]] = {task}
                lifecycle_task = self._environment_lifecycle_task
                if lifecycle_task is not None:
                    wait_for.add(lifecycle_task)
                done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)
                if lifecycle_task is not None and lifecycle_task in done:
                    self._environment_lifecycle_task = None
                    if lifecycle_task.cancelled():
                        raise RunError(
                            "Environment lifecycle stopped before logical run cleanup.",
                            code="environment_lifecycle_stopped",
                        )
                    error = lifecycle_task.exception()
                    if error is not None:
                        raise error
                    raise RunError(
                        "Environment lifecycle stopped before logical run cleanup.",
                        code="environment_lifecycle_stopped",
                    )
                item = task.result()
                self._response_next_task = None
                if isinstance(item, _ResponsePumpTerminal):
                    if item.error is not None:
                        raise item.error
                    raise StopAsyncIteration
                if not isinstance(item, HarnessRunResult):
                    return self._normalize_public_event(item)

                result = self._validate_result_candidate(item)
                self._validated_outcome = result
                await self._stop_response_pump(cancel=False)
                self._pending_result = result
                self._terminal_close_task = asyncio.create_task(self._close_resources(outcome=result))

            close_task = self._terminal_close_task
            assert close_task is not None
            await close_task
            result = self._pending_result
            self._pending_result = None
            self._terminal_close_task = None
            self._delivered_result = result
            return HarnessRunResultEvent(
                thread_id=self.thread_id,
                run_id=self.run_id,
                sequence=self._next_public_sequence(),
                occurred_at=datetime.now(UTC),
                result=result,
            )
        except StopAsyncIteration as exc:
            error = PluginError(
                "Plugin middleware ended without a result candidate.",
                code="plugin_result_missing",
            )
            error.__cause__ = exc
            await self._raise_after_failure(error)
        except asyncio.CancelledError as exc:
            if self._terminal_close_task is not None:
                try:
                    await self._terminal_close_task
                except BaseException as cleanup:
                    exc.add_note(f"Harness cleanup also failed: {cleanup!r}")
                raise
            await self._close_resources(outcome=self._validated_outcome, cancellation=exc, failure=exc)
            raise
        except RunCleanupError as exc:
            if self._closed:
                raise
            await self._raise_after_failure(exc)
        except BaseException as exc:
            await self._raise_after_failure(exc)
        raise AssertionError("unreachable")

    def _start_logical_event_mux(self) -> None:
        if self._logical_events_started:
            return
        self._logical_events_started = True
        self._emitter.start_consuming()
        self._environment_change_drain.cursor = 0
        self._environment_event_task = asyncio.create_task(
            _emit_environment_change_events(self.context, self._environment_change_drain)
        )
        self._response_pump_task = asyncio.create_task(self._pump_response())

    def _install_terminal_fence(self) -> None:
        drain = self._environment_change_drain
        if drain.terminal_sequence is not None:
            return
        self._environment_runtime._begin_close()
        drain.terminal_sequence = self.context.environment._change_sequence
        drain.requested.set()

    async def _pump_response(self) -> None:
        assert self._response is not None
        response = self._response
        iteration_error: BaseException | None = None
        cancellation: asyncio.CancelledError | None = None
        terminal_result: HarnessRunResult[OutputT] | None = None
        try:
            async for item in response:
                if isinstance(item, HarnessRunResult):
                    if terminal_result is not None:
                        raise PluginError(
                            "Plugin middleware emitted more than one result candidate.",
                            code="plugin_result_multiple",
                        )
                    terminal_result = item
                    self._install_terminal_fence()
                    continue
                await self._response_queue.put(item)
        except asyncio.CancelledError as exc:
            cancellation = exc
            current_task = asyncio.current_task()
            if current_task is not None:
                while current_task.cancelling():
                    current_task.uncancel()
        except BaseException as exc:
            if terminal_result is not None:
                self._source_cleanup_failures.append(exc)
            else:
                iteration_error = exc
        try:
            await self._close_registered_responses()
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
        except BaseException as exc:
            self._source_cleanup_failures.append(exc)
        if cancellation is not None:
            raise cancellation
        if terminal_result is not None:
            await self._response_queue.put(terminal_result)
            return
        await self._response_queue.put(_ResponsePumpTerminal(error=iteration_error))

    async def _close_registered_responses(self) -> None:
        failures: list[BaseException] = []
        pending_cancellation: asyncio.CancelledError | None = None
        current_task = asyncio.current_task()

        def capture_cancellation(exc: asyncio.CancelledError | None = None) -> bool:
            nonlocal pending_cancellation
            if current_task is None or not current_task.cancelling():
                return False
            pending_cancellation = pending_cancellation or exc or asyncio.CancelledError()
            while current_task.cancelling():
                current_task.uncancel()
            return True

        responses = sorted(self._responses.values(), key=lambda item: item[0], reverse=True)
        for _, response in responses:
            response_id = id(response)
            if response_id in self._closed_response_ids:
                continue
            self._closed_response_ids.add(response_id)
            try:
                await response.aclose()
            except asyncio.CancelledError as exc:
                if not capture_cancellation(exc):
                    failures.append(exc)
            except BaseException as exc:
                failures.append(exc)
            finally:
                capture_cancellation()
        if pending_cancellation is not None:
            self._source_cleanup_failures.extend(failures)
            for failure in failures:
                pending_cancellation.add_note(f"Harness plugin response cleanup also failed: {failure!r}")
            raise pending_cancellation
        if len(failures) == 1:
            failure = failures[0]
            if isinstance(failure, asyncio.CancelledError):
                raise BaseExceptionGroup("Harness plugin response cleanup failed", failures)
            raise failure
        if failures:
            raise BaseExceptionGroup("Harness plugin response cleanup failed", failures)

    async def _stop_response_pump(self, *, cancel: bool = True) -> None:
        task = self._response_pump_task
        self._response_pump_task = None
        if task is None:
            return
        if cancel and not task.done():
            task.cancel()
        pending_cancellation: asyncio.CancelledError | None = None
        current_task = asyncio.current_task()
        # Do not translate AnyIO's repeated scope cancellation into repeated
        # Task.cancel() calls that pierce the producer's shielded cleanup.
        # A new explicit caller cancellation still reaches plugin cleanup.
        with CancelScope(shield=True):
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError as exc:
                    if current_task is not None and current_task.cancelling():
                        pending_cancellation = pending_cancellation or exc
                        if not task.done():
                            task.cancel()
                    continue
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self._source_cleanup_failures.append(error)
        if pending_cancellation is not None:
            raise pending_cancellation

    async def _cancel_response_next_task(self) -> None:
        task = self._response_next_task
        self._response_next_task = None
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _normalize_public_event(self, item: Any) -> HarnessEvent:
        if isinstance(item, HarnessExtensionEvent):
            item = HarnessEvent(
                thread_id=self.thread_id,
                run_id=self.run_id,
                sequence=0,
                occurred_at=datetime.now(UTC),
                event=item,
            )
        if not isinstance(item, HarnessEvent) or item.sequence < 0:
            raise PluginError("Plugin emitted an invalid stream item.", code="plugin_event_invalid")
        event = item.event
        if isinstance(event, HarnessExtensionEvent):
            try:
                event = _EXTENSION_EVENT_ADAPTER.validate_python(event.model_dump(), strict=True)
            except ValidationError as exc:
                raise PluginError(
                    "Plugin emitted an invalid Harness event.",
                    code="plugin_event_invalid",
                ) from exc
        elif (
            not isinstance(event, AgentStreamEventProtocol)
            or not isinstance(event.event_kind, str)
            or not event.event_kind.strip()
        ):
            raise PluginError(
                "Plugin emitted an invalid Harness event.",
                code="plugin_event_invalid",
            )
        provenance = self._emitter.take_child_provenance(item)
        if provenance is not None and (item.thread_id != provenance.thread_id or item.run_id != provenance.run_id):
            raise PluginError(
                "Plugin changed forwarded child event provenance.",
                code="plugin_event_run_mismatch",
            )
        if provenance is not None and item.sequence != provenance.sequence:
            raise PluginError(
                "Plugin changed forwarded child event sequence.",
                code="plugin_event_sequence_invalid",
            )
        if item.run_id != self.run_id:
            if provenance is None or not self._emitter.is_registered_child(item.run_id, item.thread_id):
                raise PluginError(
                    "Plugin emitted an event for an unregistered child run.",
                    code="plugin_event_run_mismatch",
                )
            previous = self._last_public_child_sequence_by_run.get(item.run_id)
            if previous is not None and item.sequence <= previous:
                raise PluginError(
                    "Plugin emitted an out-of-order child event.",
                    code="plugin_event_sequence_invalid",
                )
            self._last_public_child_sequence_by_run[item.run_id] = item.sequence
            return HarnessEvent(
                thread_id=item.thread_id,
                run_id=item.run_id,
                sequence=item.sequence,
                occurred_at=item.occurred_at,
                event=event,
            )
        return HarnessEvent(
            thread_id=self.thread_id,
            run_id=self.run_id,
            sequence=self._next_public_sequence(),
            occurred_at=datetime.now(UTC),
            event=event,
        )

    async def _raise_after_failure(self, failure: BaseException) -> None:
        """Close a failed stream and retain an already validated inner outcome."""
        outcome = self._validated_outcome
        try:
            await self._close_resources(outcome=outcome, failure=failure)
        except RunCleanupError as cleanup_error:
            if outcome is None:
                raise cleanup_error from failure
            raise RunCleanupError(
                "Harness run failed after producing an outcome and cleanup also failed.",
                outcome=outcome,
                causes=(failure, *cleanup_error.causes),
            ) from failure
        if outcome is not None:
            raise RunCleanupError(
                "Harness run failed after producing an outcome.",
                outcome=outcome,
                causes=(failure,),
            ) from failure
        raise failure

    def cancel(self) -> None:
        """Request native Pydantic AI cancellation, including before Agent start."""
        if self._delivered_result is not None or self._closed:
            return
        self._cancel_event.set()
        if self._attempt_events is not None:
            self._attempt_events.cancel()

    async def steer(self, input: RunInputValue, *, input_id: str | None = None) -> str:
        """Deliver one user steering value through native Pydantic enqueue.

        `input_id` is the host's identity for the value; it is recorded on the delivered request, where
        `steering_input_ids` reads it back from exported state.
        """
        if not self._entered or self._closed or self._context is None:
            raise RunError("The run is not active.", code="run_not_active")
        return await self._context._steering.steer(input, input_id=input_id)

    async def export_state(self) -> HarnessState:
        """Export active state or the detached checkpoint retained before shutdown."""
        if self._closed:
            if self._shutdown_state is not None:
                return self._shutdown_state
            raise StateError(
                "No complete shutdown checkpoint is available.", code="run_state_unavailable"
            ) from self._shutdown_state_error
        if not self._entered or self._context is None:
            raise StateError("The run is not active.", code="run_not_active")
        self._refresh_live_messages()
        return await self._context.export_state(self._latest_messages)

    def _build_plugin_response(
        self,
        plugins: tuple[AbstractHarnessPlugin, ...],
        index: int,
        exchange: PluginRunExchange,
    ) -> PluginRunResponse[OutputT]:
        if index == len(plugins):
            response = PluginRunResponse(self._stream_run(exchange))
        else:
            plugin = plugins[index]
            call_next = PluginRunNext[OutputT](
                lambda next_exchange: self._build_plugin_response(plugins, index + 1, next_exchange)
            )
            response = plugin.wrap_run(exchange, call_next)
            if not isinstance(response, PluginRunResponse):
                raise PluginError(
                    "Plugin wrap_run must return PluginRunResponse.",
                    code="plugin_response_invalid",
                    details={"plugin_id": plugin.plugin_id},
                )
        typed_response = cast(PluginRunResponse[OutputT], response)
        typed_response._bind_item_validator(self._validate_plugin_response_item)
        registered = self._register_response(typed_response, depth=index * 2 + 1)
        boundary = PluginRunResponse(self._response_boundary_items(registered, terminal=index == 0))
        boundary._bind_item_validator(self._validate_plugin_response_item)
        return self._register_response(boundary, depth=index * 2)

    def _validate_plugin_response_item(
        self,
        item: HarnessEvent | HarnessRunResult[OutputT],
    ) -> HarnessEvent | HarnessRunResult[OutputT]:
        if isinstance(item, HarnessRunResult):
            validated = self._validate_result_candidate(item)
            self._validated_outcome = validated
            return validated
        return item

    def _register_response(
        self,
        response: PluginRunResponse[OutputT],
        *,
        depth: int,
    ) -> PluginRunResponse[OutputT]:
        self._responses.setdefault(id(response), (depth, response))
        return response

    async def _response_boundary_items(
        self,
        response: PluginRunResponse[OutputT],
        *,
        terminal: bool,
    ) -> AsyncGenerator[HarnessEvent | HarnessRunResult[OutputT]]:
        async for item in response:
            if not isinstance(item, HarnessRunResult):
                yield item
                continue
            if terminal:
                self._install_terminal_fence()
                if item.status in {"failed", "cancelled"}:
                    await self.context.usage_attribution._flush_cleanup()
                else:
                    await self.context.usage_attribution._flush(reason="terminal")
                item = self._validate_result_candidate(
                    item.replace(
                        usage_records=self.context.usage_records,
                        usage=self.context.usage_attribution.summary(tool_calls=self._usage.tool_calls),
                    )
                )
            target_sequence = self.context.environment._change_sequence
            async for event in self._drain_emitter_through(target_sequence, terminal=terminal):
                yield event
            if terminal:
                self._emitter.close()
            yield item
            return

    async def _drain_emitter_through(
        self,
        target_sequence: int,
        *,
        terminal: bool,
    ) -> AsyncGenerator[HarnessEvent]:
        emitter_task: asyncio.Task[HarnessExtensionEvent | HarnessEvent] | None = None
        progress_task: asyncio.Task[bool] | None = None
        deadline_task = asyncio.create_task(asyncio.sleep(5.0)) if terminal else None
        try:
            while True:
                if emitter_task is not None and emitter_task.done():
                    event = emitter_task.result()
                    emitter_task = None
                    yield self._adapt_extension_event(event) if isinstance(event, HarnessExtensionEvent) else event
                    continue

                adapter_task = self._environment_event_task
                if adapter_task is not None and adapter_task.done():
                    self._environment_event_task = None
                    adapter_task.result()
                    adapter_task = None
                    if not terminal or not self._environment_change_drain.drained.is_set():
                        raise RunError(
                            "Environment change event adapter stopped before the terminal journal fence.",
                            code="event_adapter_stopped",
                        )

                progress = self._environment_change_drain.progress
                cursor = self._environment_change_drain.cursor
                adapter_complete = adapter_task is None or adapter_task.done()
                terminal_complete = not terminal or (
                    self._environment_change_drain.drained.is_set() and adapter_complete
                )
                if cursor is not None and cursor >= target_sequence and self._emitter.empty() and terminal_complete:
                    if emitter_task is not None and not emitter_task.done():
                        emitter_task.cancel()
                        await asyncio.gather(emitter_task, return_exceptions=True)
                    return

                if emitter_task is None:
                    emitter_task = asyncio.create_task(self._emitter.next())
                if progress_task is None:
                    progress_task = asyncio.create_task(progress.wait())
                wait_for: set[asyncio.Task[Any]] = {emitter_task, progress_task}
                if adapter_task is not None:
                    wait_for.add(adapter_task)
                if deadline_task is not None:
                    wait_for.add(deadline_task)
                done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)
                if deadline_task is not None and deadline_task in done:
                    raise RunError(
                        "Environment change event adapter did not drain before cleanup deadline.",
                        code="event_adapter_cleanup_timeout",
                    )
                if progress_task in done:
                    progress_task.result()
                    progress_task = None
        finally:
            tasks: list[asyncio.Task[Any]] = [
                task for task in (emitter_task, progress_task, deadline_task) if task is not None
            ]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _pump_attempts(
        self,
        source: AsyncGenerator[HarnessEvent | HarnessRunResult[OutputT]],
        queue: asyncio.Queue[PluginRunItem[OutputT] | _ResponsePumpTerminal],
    ) -> None:
        """Keep native event scopes and their cleanup in the same producer task."""
        error: BaseException | None = None
        cancellation: asyncio.CancelledError | None = None
        try:
            async for item in source:
                await queue.put(item)
        except asyncio.CancelledError as exc:
            cancellation = exc
        except BaseException as exc:
            error = exc
        try:
            await source.aclose()
        except BaseException as exc:
            error = exc if error is None else BaseExceptionGroup("Harness model source cleanup failed", [error, exc])
        if cancellation is not None:
            if error is not None:
                cancellation.add_note(f"Harness model source cleanup also failed: {error!r}")
            raise cancellation
        await queue.put(_ResponsePumpTerminal(error=error))

    async def _stream_run(
        self,
        exchange: PluginRunExchange,
    ) -> AsyncGenerator[HarnessEvent | HarnessRunResult[OutputT]]:
        """Merge native attempts and Run events into the innermost plugin response."""
        source_queue: asyncio.Queue[PluginRunItem[OutputT] | _ResponsePumpTerminal] = asyncio.Queue(maxsize=1)
        source_pump = asyncio.create_task(self._pump_attempts(self._run_attempts(exchange), source_queue))
        source_task: asyncio.Task[PluginRunItem[OutputT] | _ResponsePumpTerminal] | None = None
        emitter_task: asyncio.Task[HarnessExtensionEvent | HarnessEvent] | None = None
        pending_result: HarnessRunResult[OutputT] | None = None
        try:
            while True:
                if emitter_task is not None and emitter_task.done():
                    event = emitter_task.result()
                    emitter_task = None
                    yield self._adapt_extension_event(event) if isinstance(event, HarnessExtensionEvent) else event
                    continue
                if source_task is None:
                    source_task = asyncio.create_task(source_queue.get())
                if emitter_task is None:
                    emitter_task = asyncio.create_task(self._emitter.next())

                wait_for: set[asyncio.Task[Any]] = {source_task, emitter_task}
                adapter_task = self._environment_event_task
                if adapter_task is not None and not adapter_task.done():
                    wait_for.add(adapter_task)
                done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)

                if adapter_task is not None and adapter_task in done:
                    self._environment_event_task = None
                    adapter_task.result()
                    raise RunError(
                        "Environment change event adapter stopped before the logical terminal fence.",
                        code="event_adapter_stopped",
                    )
                if emitter_task in done:
                    event = emitter_task.result()
                    emitter_task = None
                    yield self._adapt_extension_event(event) if isinstance(event, HarnessExtensionEvent) else event
                    continue
                assert source_task in done
                item = source_task.result()
                source_task = None
                if not isinstance(item, _ResponsePumpTerminal):
                    if pending_result is not None:
                        raise RunError(
                            "Harness model source emitted an item after its result candidate.",
                            code="agent_result_not_terminal",
                        )
                    if isinstance(item, HarnessRunResult):
                        pending_result = item
                    else:
                        yield item
                    continue

                await source_pump
                if item.error is not None:
                    raise item.error
                if pending_result is None:
                    return
                if emitter_task is not None:
                    if emitter_task.done():
                        event = emitter_task.result()
                        emitter_task = None
                        yield (
                            self._adapt_extension_event(event) if isinstance(event, HarnessExtensionEvent) else event
                        )
                    else:
                        emitter_task.cancel()
                        await asyncio.gather(emitter_task, return_exceptions=True)
                        emitter_task = None
                target_sequence = self.context.environment._change_sequence
                async for event in self._drain_emitter_through(target_sequence, terminal=False):
                    yield event
                yield pending_result
                return
        finally:
            tasks: list[asyncio.Task[Any]] = [
                task for task in (source_task, emitter_task, source_pump) if task is not None
            ]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _normalize_interrupted_history(
        self, messages: Sequence[ModelMessage], *, response_tracker: InterruptedResponseTracker
    ) -> tuple[tuple[ModelMessage, ...], int]:
        self._deferred_input.reconcile(messages)
        remaining = self._deferred_input.pending
        return normalize_interrupted_history(
            messages,
            response_tracker=response_tracker,
            close_tool_calls=remaining is None,
        )

    async def _run_attempts(
        self,
        exchange: PluginRunExchange,
    ) -> AsyncGenerator[HarnessEvent | HarnessRunResult[OutputT]]:
        """Run native Agent attempts and recovery without rebuilding the Run's plugin chain."""
        if exchange.context is not self.context:
            raise PluginError(
                "Plugin middleware replaced the trusted run context.",
                code="plugin_context_replaced",
            )
        if not isinstance(exchange.input, SemanticRunInput):
            raise PluginError("Plugin middleware supplied an invalid input.", code="plugin_input_invalid")
        try:
            current_input = normalize_input(exchange.input.value)
        except HarnessError as exc:
            raise PluginError("Plugin middleware supplied an invalid input.", code="plugin_input_invalid") from exc

        await exchange.context._steering.prepare(
            current_input,
            restore_retained=self._deferred_resume is not None,
        )

        policy = self._executable.definition.model_recovery
        max_attempts = policy.max_attempts if policy.enabled else 1
        attempt_index = 0
        recovery = exchange.context._model_recovery
        current_history = (
            self._tool_recovery.messages if self._tool_recovery is not None else self._previous_state.message_history
        )
        deferred_results = (
            self._deferred_resume.results
            if self._deferred_resume is not None
            else self._tool_recovery.results
            if self._tool_recovery is not None
            else None
        )
        current_history = _reconcile_system_prompt(
            current_history,
            self._executable._system_prompt,
        )
        self._latest_messages = current_history

        while True:
            current_history = normalize_request_history(
                current_history,
                has_new_prompt=current_input.value is not None,
                has_deferred_results=deferred_results is not None and attempt_index == 0,
            )
            self._latest_messages = current_history
            await exchange.context._steering.resolve_delivered(current_history)
            retry_error: BaseException | None = None
            recovery.attempt_id = f"model-attempt-{uuid4().hex}"
            exchange.context._model_input.begin(
                recovery.attempt_id,
                tuple(content_items(current_input.value)) if current_input.value is not None else None,
                recovery=attempt_index > 0,
            )
            recovery.request_error = None
            response_tracker = InterruptedResponseTracker()
            attempt_token = self._observation.record_model_attempt() if self._observation is not None else None
            manager = self._executable._agent.run_stream_events(
                native_content(current_input.value) if current_input.value is not None else None,
                message_history=current_history,
                deferred_tool_results=(deferred_results if attempt_index == 0 else None),
                run_id=recovery.attempt_id,
                conversation_id=self.thread_id,
                deps=self.context,
                usage=self._usage,
                usage_limits=self._usage_limits,
                capabilities=self._bindings.capabilities,
            )
            try:
                async with manager as events:
                    self._attempt_events = events
                    if self._cancel_event.is_set():
                        events.cancel()
                    try:
                        async for event in events:
                            if isinstance(event, EnqueuedMessagesEvent):
                                await self.context._steering.mark_applied(event.enqueue_id)
                            response_tracker.observe(
                                cast(AgentStreamEvent, event),
                                response_history_count=len(events.all_messages()),
                            )
                            self._refresh_live_messages()
                            if isinstance(event, HarnessEvent):
                                yield event
                                continue
                            if isinstance(event, HarnessExtensionEvent):
                                yield self._adapt_extension_event(event)
                                continue
                            if isinstance(event, AgentRunResultEvent):
                                candidate = await self._native_result_candidate(event.result)
                                yield self._record_inner_candidate(candidate)
                                return
                            yield self._adapt_event(cast(AgentStreamEvent, event))
                            if isinstance(event, EnqueuedMessagesEvent):
                                for message in event.messages:
                                    if not isinstance(message, ModelRequest):
                                        continue
                                    notification = (message.metadata or {}).get("a13n.steering-source")
                                    source: InputSource = (
                                        notification
                                        if notification in {"async_subagent", "background_process"}
                                        else "steering"
                                    )
                                    for observed in input_events(
                                        request_input_content(message),
                                        source=source,
                                        input_id=event.enqueue_id,
                                    ):
                                        yield self._adapt_event(observed)
                    except RunCancelled as exc:
                        if exc.run_id is None:
                            # Native execution has not started; its empty history cannot
                            # replace the complete boundary already held by this Run.
                            raw_messages = self._latest_messages
                            raw_new_message_count = max(0, len(raw_messages) - self._new_message_index)
                        else:
                            raw_messages = exc.all_messages()
                            raw_new_message_count = len(exc.new_messages())
                        messages, _ = await self._normalize_interrupted_history(
                            raw_messages,
                            response_tracker=response_tracker,
                        )
                        self._attempt_events = None
                        self._latest_messages = messages
                        state = await exchange.context.export_state(messages)
                        yield self._record_inner_candidate(
                            HarnessRunResult(
                                thread_id=self.thread_id,
                                run_id=self.run_id,
                                status="cancelled",
                                output=None,
                                state=state,
                                usage=self.usage,
                                _messages=messages,
                                _new_message_index=max(0, len(messages) - raw_new_message_count),
                            )
                        )
                        return
                    except UsageLimitExceeded:
                        self._refresh_live_messages()
                        messages, _ = await self._normalize_interrupted_history(
                            self._latest_messages,
                            response_tracker=response_tracker,
                        )
                        self._attempt_events = None
                        self._latest_messages = messages
                        yield await self._failed_candidate(
                            code="usage_limit_exceeded",
                            message="Run usage limit exceeded.",
                        )
                        return
                    except Exception as error:
                        self._refresh_live_messages()
                        messages, _ = await self._normalize_interrupted_history(
                            self._latest_messages,
                            response_tracker=response_tracker,
                        )
                        self._attempt_events = None
                        self._latest_messages = messages
                        if self._cancel_event.is_set():
                            state = await exchange.context.export_state(messages)
                            yield self._record_inner_candidate(
                                HarnessRunResult(
                                    thread_id=self.thread_id,
                                    run_id=self.run_id,
                                    status="cancelled",
                                    output=None,
                                    state=state,
                                    usage=self.usage,
                                    _messages=messages,
                                    _new_message_index=min(self._new_message_index, len(messages)),
                                )
                            )
                            return

                        model_failure = recovery.request_error is error and not isinstance(error, HarnessError)
                        retryable = policy.enabled and model_failure and is_recoverable_model_failure(error)
                        if retryable:
                            recovery.consecutive_failures += 1
                        retrying = retryable and recovery.consecutive_failures < max_attempts
                        failure_details = (
                            _report_model_failure(
                                error, thread_id=self.thread_id, run_id=self.run_id, retrying=retrying
                            )
                            if model_failure or isinstance(error, AgentRunError)
                            else None
                        )
                        if retrying:
                            retry_error = error
                        elif model_failure or isinstance(error, AgentRunError):
                            self._diagnostic_error = error
                            exhausted = retryable
                            yield await self._failed_candidate(
                                code="model_recovery_exhausted" if exhausted else "agent_run_failed",
                                message=(
                                    f"Model execution could not recover after {max_attempts} consecutive failed attempts. "
                                    "Try continuing the conversation again."
                                    if exhausted
                                    else "Agent execution failed."
                                ),
                                details=failure_details,
                                retry_hint="new_run" if exhausted else "dependency_change",
                            )
                            return
                        else:
                            raise
            except BaseException as exc:
                # Pydantic attaches cancellation state only after its event context
                # drains the model task. Capture that committed boundary, not the
                # still-running response observed inside the context.
                cancelled = RunCancelled.from_cancellation(exc)
                if cancelled is not None and cancelled.run_id is not None:
                    self._latest_messages = tuple(cancelled.all_messages())
                else:
                    self._refresh_live_messages()
                self._latest_messages, _ = await self._normalize_interrupted_history(
                    self._latest_messages, response_tracker=response_tracker
                )
                raise
            finally:
                self._attempt_events = None
                recovery.attempt_id = None
                recovery.request_error = None
                if attempt_token is not None:
                    _LogicalRunObservation.reset_model_attempt(attempt_token)

            assert retry_error is not None
            retry_index = recovery.consecutive_failures
            delay = policy.delay(retry_index)
            yield self._adapt_extension_event(
                HarnessExtensionEvent(
                    kind="recovery",
                    payload=ModelRetryScheduledPayload(
                        attempt=retry_index + 1,
                        max_attempts=max_attempts,
                        delay_seconds=delay,
                    ).model_dump(mode="json"),
                )
            )
            if delay > 0:
                with observe_operation("recovery") as span:
                    record_span_metadata(
                        span,
                        {
                            "recovery.step": "backoff",
                            "recovery.next_attempt": retry_index + 1,
                            "recovery.max_attempts": max_attempts,
                            "recovery.delay_seconds": delay,
                        },
                    )
                    try:
                        await asyncio.wait_for(self._cancel_event.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    observe_output(span, {"cancel_requested": self._cancel_event.is_set()}, status="wait_finished")
            if self._cancel_event.is_set():
                state = await exchange.context.export_state(self._latest_messages) if self._latest_messages else None
                yield self._record_inner_candidate(
                    HarnessRunResult(
                        thread_id=self.thread_id,
                        run_id=self.run_id,
                        status="cancelled",
                        output=None,
                        state=state,
                        usage=self.usage,
                        _messages=self._latest_messages,
                        _new_message_index=min(self._new_message_index, len(self._latest_messages)),
                    )
                )
                return
            with observe_operation("recovery") as span:
                record_span_metadata(
                    span,
                    {
                        "recovery.step": "build_prompt",
                        "recovery.next_attempt": retry_index + 1,
                        "recovery.max_attempts": max_attempts,
                        "recovery.history_count": len(self._latest_messages),
                    },
                )
                retry_input = await policy.build_prompt(retry_error, retry_index, self._latest_messages)
                observe_output(span, {"retry_input_available": retry_input is not None}, status="prepared")
            current_input = normalize_input(retry_input)
            current_history = self._latest_messages
            attempt_index += 1

    async def _native_result_candidate(
        self, result: AgentRunResult[OutputT | DeferredToolRequests]
    ) -> HarnessRunResult[OutputT]:
        """Capture native completion while its attempt and live context are still open."""
        messages = tuple(result.all_messages())
        self._deferred_input.reconcile(messages)
        new_message_index = len(messages) - len(result.new_messages())
        self._latest_messages = messages
        state = await self.context.export_state(messages)
        if not isinstance(result.output, DeferredToolRequests):
            return HarnessRunResult(
                thread_id=self.thread_id,
                run_id=self.run_id,
                status="completed",
                output=result.output,
                state=state,
                usage=self.context.usage_attribution.summary(tool_calls=self._usage.tool_calls),
                _messages=messages,
                _new_message_index=new_message_index,
            )
        if not self.context.deferred_tools_supported:
            return HarnessRunResult(
                thread_id=self.thread_id,
                run_id=self.run_id,
                status="failed",
                output=None,
                failure=SafeFailure(
                    code="deferred_tools_unsupported",
                    message="This Host does not support deferred tool requests for this Run.",
                ),
                state=state,
                usage=self.context.usage_attribution.summary(tool_calls=self._usage.tool_calls),
                _messages=messages,
                _new_message_index=new_message_index,
            )
        deferred = result.output
        return HarnessRunResult(
            thread_id=self.thread_id,
            run_id=self.run_id,
            status="suspended",
            output=None,
            deferred=deferred,
            suspend_reason="deferred",
            state=state,
            usage=self.context.usage_attribution.summary(tool_calls=self._usage.tool_calls),
            _messages=messages,
            _new_message_index=new_message_index,
        )

    async def _failed_candidate(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, JsonValue] | None = None,
        retry_hint: RetryHint = "dependency_change",
    ) -> HarnessRunResult[OutputT]:
        state = await self.context.export_state(self._latest_messages)
        return self._record_inner_candidate(
            HarnessRunResult(
                thread_id=self.thread_id,
                run_id=self.run_id,
                status="failed",
                output=None,
                state=state,
                usage=self.context.usage_attribution.summary(tool_calls=self._usage.tool_calls),
                failure=SafeFailure.model_validate(
                    {
                        "code": code,
                        "message": message,
                        "details": details or {},
                        "retry_hint": retry_hint,
                    }
                ),
                _messages=self._latest_messages,
                _new_message_index=min(self._new_message_index, len(self._latest_messages)),
            )
        )

    def _record_inner_candidate(
        self,
        candidate: HarnessRunResult[OutputT],
    ) -> HarnessRunResult[OutputT]:
        attributed = candidate.replace(
            usage_records=self.context.usage_records,
            usage=self.context.usage_attribution.summary(tool_calls=self._usage.tool_calls),
        )
        validated = self._validate_result_candidate(attributed)
        self._validated_outcome = validated
        return validated

    def _validate_result_candidate(
        self,
        candidate: HarnessRunResult[OutputT],
    ) -> HarnessRunResult[OutputT]:
        if candidate.thread_id != self.thread_id:
            raise PluginError(
                "Plugin result thread_id does not match the active Thread.",
                code="plugin_result_thread_mismatch",
            )
        if candidate.run_id != self.run_id:
            raise PluginError(
                "Plugin result run_id does not match the active run.",
                code="plugin_result_run_mismatch",
            )
        try:
            if type(candidate) is HarnessRunResult:
                # Revalidate result fields without decoding immutable normalized history.
                validated = candidate.replace()
            else:
                # Subclasses may override the public views; normalize and check them.
                messages = candidate.all_messages()
                new_messages = candidate.new_messages()
                new_message_index = len(messages) - len(new_messages)
                if new_message_index < 0 or messages[new_message_index:] != new_messages:
                    raise ValueError("new messages are not a suffix of all messages")
                validated = HarnessRunResult(
                    thread_id=candidate.thread_id,
                    run_id=candidate.run_id,
                    status=candidate.status,
                    output=candidate.output,
                    state=candidate.state,
                    usage=candidate.usage,
                    usage_records=candidate.usage_records,
                    failure=candidate.failure,
                    suspend_reason=candidate.suspend_reason,
                    deferred=candidate.deferred,
                    _messages=messages,
                    _new_message_index=new_message_index,
                )
            if validated.status == "completed":
                output = validated.output
                if _business_output_contains_deferred_value(output):
                    raise ValueError("deferred output must suspend the run")
                self._executable._output_adapter.validate_python(output, strict=True)
            return validated
        except (TypeError, ValueError, ValidationError) as exc:
            raise PluginError(
                "Plugin emitted an invalid result candidate.",
                code="plugin_result_invalid",
            ) from exc

    def _adapt_event(self, event: AgentStreamEvent) -> HarnessEvent:
        envelope = HarnessEvent(
            thread_id=self.thread_id,
            run_id=self.run_id,
            sequence=self._source_sequence,
            occurred_at=datetime.now(UTC),
            event=event,
        )
        self._source_sequence += 1
        return envelope

    def _adapt_extension_event(self, event: HarnessExtensionEvent) -> HarnessEvent:
        envelope = self._emitter.envelope(event, sequence=self._source_sequence)
        self._source_sequence += 1
        return envelope

    def _next_public_sequence(self) -> int:
        sequence = self._public_sequence
        self._public_sequence += 1
        return sequence

    def _refresh_live_messages(self) -> None:
        events = self._attempt_events
        if events is None:
            return
        try:
            self._latest_messages = tuple(events.all_messages())
        except UserError:
            # Before Pydantic binds the run, imported history remains the latest complete boundary.
            return

    async def _close_resources(
        self,
        *,
        outcome: HarnessRunResult[Any] | None,
        cancellation: asyncio.CancelledError | None = None,
        failure: BaseException | None = None,
    ) -> None:
        if self._closed:
            if cancellation is not None:
                raise cancellation
            return
        if failure is not None and cancellation is None:
            self._diagnostic_error = failure

        with observe_phase("finalize") as phase:
            fence_failure: BaseException | None = None
            try:
                self._environment_runtime._begin_close()
            except BaseException as exc:
                fence_failure = exc

            current_task = asyncio.current_task()
            causes: list[BaseException] = [] if fence_failure is None else [fence_failure]

            def capture_pending_cancellation(exc: asyncio.CancelledError | None = None) -> bool:
                nonlocal cancellation
                if current_task is None or not current_task.cancelling():
                    return False
                if cancellation is None:
                    cancellation = exc or asyncio.CancelledError()
                while current_task.cancelling():
                    current_task.uncancel()
                return True

            capture_pending_cancellation(cancellation)

            async def finish_cleanup(awaitable: Awaitable[None]) -> None:
                try:
                    # Cleanup normally stays in the task that entered plugin and AnyIO scopes.
                    await awaitable
                except asyncio.CancelledError as exc:
                    if not capture_pending_cancellation(exc):
                        causes.append(exc)
                except BaseException as exc:
                    causes.append(exc)
                finally:
                    # Cleanup code may suppress or translate the injected CancelledError.
                    capture_pending_cancellation()

            # Repeated cancellation while draining the reader must not skip its producer.
            phase.set_attribute("a13n.phase.step", "responses")
            await finish_cleanup(self._cancel_response_next_task())
            await finish_cleanup(self._stop_response_pump())
            await finish_cleanup(self._close_registered_responses())
            phase.set_attribute("a13n.phase.step", "state_export")
            outcome = outcome or self._validated_outcome
            with CancelScope(shield=True):
                if outcome is not None:
                    # A validated middleware-owned result remains the state authority.
                    self._shutdown_state = outcome.state
                elif self._context is not None:
                    try:
                        self._shutdown_state = await self._context.export_state(self._latest_messages)
                    except BaseException as exc:
                        # The Host can diagnose export failure without losing the original
                        # execution error or preventing resource cleanup.
                        self._shutdown_state_error = exc
            phase.set_attribute("a13n.phase.step", "attachments")
            await finish_cleanup(self._close_run_attachments())
            causes.extend(self._source_cleanup_failures)
            self._source_cleanup_failures.clear()

            phase.set_attribute("a13n.phase.step", "environment")
            await finish_cleanup(self._close_environment_lifecycle())
            self._emitter.close()
            if self._environment_event_task is not None:
                task = self._environment_event_task
                self._environment_event_task = None
                await finish_cleanup(_stop_environment_event_task(task))
            self._closed = True

            phase_status = "failed" if causes or self._shutdown_state_error is not None else "completed"
            record_span_metadata(
                phase,
                {
                    "phase.status": phase_status,
                    "finalize.cleanup_error_count": len(causes),
                    "finalize.state_export_failed": self._shutdown_state_error is not None,
                },
            )
            observe_output(
                phase,
                {
                    "state_available": self._shutdown_state is not None,
                    "cleanup_error_count": len(causes),
                    "state_export_failed": self._shutdown_state_error is not None,
                    "cancelled": cancellation is not None,
                },
                status=phase_status,
            )
            if phase_status == "failed":
                phase.set_status(StatusCode.ERROR)

        self._finish_observation(
            outcome=outcome, cancellation=cancellation, failure=failure, cleanup_failed=bool(causes)
        )

        if cancellation is not None:
            for cause in causes:
                cancellation.add_note(f"Harness cleanup also failed: {cause!r}")
            raise cancellation
        if causes:
            raise RunCleanupError(
                "Harness run cleanup failed.",
                outcome=outcome,
                causes=tuple(causes),
            )

    def _finish_observation(
        self,
        *,
        outcome: HarnessRunResult[Any] | None,
        cancellation: asyncio.CancelledError | None,
        failure: BaseException | None,
        cleanup_failed: bool,
    ) -> None:
        observation = self._observation
        if observation is not None:
            if cancellation is not None:
                observed_outcome = "cancelled"
            elif cleanup_failed or failure is not None:
                observed_outcome = "failed"
            elif outcome is not None:
                observed_outcome = outcome.status
            else:
                observed_outcome = "cancelled"

            failure_code: str | None = None
            if cleanup_failed:
                failure_code = "run_cleanup_failed"
            elif observed_outcome == "failed" and outcome is not None and outcome.failure is not None:
                failure_code = outcome.failure.code
            elif observed_outcome == "failed" and isinstance(failure, HarnessError):
                failure_code = failure.code
            elif observed_outcome == "failed":
                failure_code = "run_unhandled"
            observation.record_output(outcome.output if outcome is not None else None, status=observed_outcome)
            observation.finish(
                outcome=observed_outcome,
                failure_code=failure_code,
                error=observed_outcome == "failed" or cleanup_failed,
            )

    async def _close_run_attachments(self) -> None:
        """Close collaborators claimed by finalized definition owners before Environment teardown."""
        if self._run_attachments_closed:
            return
        self._run_attachments_closed = True
        if self._context is not None:
            await self._context._close_run_cleanups()
