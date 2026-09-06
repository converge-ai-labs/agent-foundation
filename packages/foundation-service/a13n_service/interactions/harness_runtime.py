"""Exact Foundation construction and invocation of one logical Harness Run."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol

from a13n_environment_provider import Environment
from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentInstanceContext,
    DeferredToolResume,
    EnvironmentEntry,
    EnvironmentMount,
    ExecutableAgent,
    HarnessBuilder,
    HarnessEvent,
    HarnessObservationContext,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessRunStream,
    HarnessState,
    RunBindings,
    RunInputFactory,
    RunInputValue,
    RunModelResolver,
)
from a13n_harness.errors import RunError
from a13n_harness.model_context import ModelContextMiddleware
from a13n_harness.pricing import get_current_pricing_catalog
from anyio import to_thread
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import RunUsage, UsageLimits

if TYPE_CHECKING:
    from a13n_service.connectivity.execution import ExternalToolRuntime

from .attempts import AttemptPreparationAccepted
from .environment_observation import EnvironmentHookObservation, observe_environment_entry
from .harness_control import (
    HarnessContextBinding,
    HarnessHookBoundary,
    HarnessRunIdentity,
    RunControlPort,
    compose_run_control,
)
from .state import RunStateEnvelope

_DEFERRED_REQUESTS_ADAPTER = TypeAdapter(DeferredToolRequests)
logger = logging.getLogger("a13n_service.interactions.harness_runtime")


@dataclass(frozen=True, slots=True)
class ImmediateHarnessInput:
    """An accepted input that is already representable as native Harness input."""

    value: RunInputValue | None = None


@dataclass(frozen=True, slots=True)
class MaterializedHarnessInput:
    """An accepted input that must be materialized after Environment entry."""

    factory: RunInputFactory

    def __post_init__(self) -> None:
        if not callable(self.factory):
            raise TypeError("materialized Harness input factory must be callable")


type HarnessInput = ImmediateHarnessInput | MaterializedHarnessInput


@dataclass(frozen=True, slots=True)
class NoHarnessEnvironment:
    """Select Harness's empty Environment runtime."""


@dataclass(frozen=True, slots=True)
class SingleHarnessEnvironment:
    """Mount one fresh Environment as the default workspace."""

    entry: EnvironmentEntry

    def __post_init__(self) -> None:
        _require_environment_entry(self.entry)


@dataclass(frozen=True, slots=True)
class MountedHarnessEnvironments:
    """Mount a fresh named Environment set with an optional accepted default."""

    entries: Mapping[str, EnvironmentEntry]
    default_environment: str | None = None

    def __post_init__(self) -> None:
        entries = dict(self.entries)
        if not entries:
            raise ValueError("mounted Harness environments must not be empty")
        if any(not isinstance(name, str) or not name.strip() for name in entries):
            raise ValueError("mounted Harness environment names must be non-blank strings")
        for entry in entries.values():
            _require_environment_entry(entry)
        default = self.default_environment
        if default is not None and default not in entries:
            raise ValueError("default Harness environment must name a supplied mount")
        object.__setattr__(self, "entries", MappingProxyType(entries))


type FoundationHarnessEnvironment = NoHarnessEnvironment | SingleHarnessEnvironment | MountedHarnessEnvironments


@dataclass(frozen=True, slots=True)
class HarnessCollaborators:
    """Fresh typed collaborators supplied to one logical Harness Run."""

    instance: AgentInstanceContext
    model_resolver: RunModelResolver | None = None
    capabilities: tuple[AbstractCapability[AgentContext], ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    model_context: ModelContextMiddleware | None = None
    observation: HarnessObservationContext | None = None
    external_tools_prepared: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.instance, AgentInstanceContext):
            raise TypeError("Foundation Harness instance must be an AgentInstanceContext")
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "metadata", MappingProxyType(deepcopy(dict(self.metadata))))

    def create_bindings(self) -> RunBindings:
        """Create the fresh logical-Run binding value at invocation time."""

        return RunBindings(
            instance=self.instance,
            model_resolver=self.model_resolver,
            capabilities=self.capabilities,
            metadata=self.metadata,
            model_context=self.model_context,
            observation=self.observation,
        )


@dataclass(frozen=True, slots=True)
class HarnessInvocation[OutputT]:
    """Complete non-authoritative inputs for one entered Harness stream."""

    definition: AgentDefinition[OutputT]
    input: HarnessInput
    collaborators: HarnessCollaborators
    environment: FoundationHarnessEnvironment = field(default_factory=NoHarnessEnvironment)
    deferred_resume: DeferredToolResume | None = None
    usage: RunUsage | None = None
    usage_limits: UsageLimits | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.definition, AgentDefinition):
            raise TypeError("Foundation Harness definition must be an AgentDefinition")
        if not isinstance(self.input, ImmediateHarnessInput | MaterializedHarnessInput):
            raise TypeError("Foundation Harness input source is invalid")
        if not isinstance(self.collaborators, HarnessCollaborators):
            raise TypeError("Foundation Harness collaborators are invalid")
        if not isinstance(
            self.environment,
            NoHarnessEnvironment | SingleHarnessEnvironment | MountedHarnessEnvironments,
        ):
            raise TypeError("Foundation Harness environment selection is invalid")
        if self.deferred_resume is not None and not isinstance(self.deferred_resume, DeferredToolResume):
            raise TypeError("Foundation deferred resume must be a DeferredToolResume or None")
        if self.usage is not None and not isinstance(self.usage, RunUsage):
            raise TypeError("Foundation Harness usage must be a RunUsage or None")
        if self.usage_limits is not None and not isinstance(self.usage_limits, UsageLimits):
            raise TypeError("Foundation Harness usage limits must be UsageLimits or None")


class HarnessEventProjector(Protocol):
    """Project one canonical public Harness stream item into live presentation."""

    def project(self, event: HarnessEvent | HarnessRunResultEvent[Any]) -> None: ...

    def project_environment(self, observation: EnvironmentHookObservation) -> None: ...

    async def close(self) -> None: ...


class HarnessDriver:
    """Sole owner, public-API caller, and consumer of one entered Harness stream."""

    def __init__(
        self,
        builder: HarnessBuilder,
        *,
        control: RunControlPort,
        projector: HarnessEventProjector,
        external_tools: ExternalToolRuntime | None = None,
    ) -> None:
        if not isinstance(builder, HarnessBuilder):
            raise TypeError("Harness driver requires a HarnessBuilder")
        self._external_tools = external_tools
        self._builder = builder
        self._control = control
        self._projector = projector
        self._driver_token = object()
        self._run_token: object | None = None
        self._stream: HarnessRunStream[Any] | None = None
        self._model_attempt_token: object | None = None
        self._model_attempt_run_id: str | None = None
        self._boundary: _DriverHookBoundary | None = None
        self._used = False

    async def run[OutputT](
        self,
        invocation: HarnessInvocation[OutputT],
        *,
        preparation: AttemptPreparationAccepted,
    ) -> HarnessRunResult[OutputT]:
        if self._used:
            raise RunError(
                "Harness driver cannot be reused.",
                code="foundation_driver_reused",
            )
        self._used = True
        try:
            async with AsyncExitStack() as stack:
                if self._external_tools is not None:
                    capabilities = await stack.enter_async_context(
                        self._external_tools.capabilities(lambda: self._control.current_context)
                    )
                    invocation = replace(
                        invocation,
                        collaborators=replace(
                            invocation.collaborators,
                            capabilities=(*invocation.collaborators.capabilities, *capabilities),
                        ),
                    )
                else:
                    config = self._control.current_state.envelope.effective_agent_config
                    if (
                        config.connector_tools or config.mcp_tools
                    ) and not invocation.collaborators.external_tools_prepared:
                        raise RunError("External tool runtime is unavailable.", code="external_tools_unavailable")
                return await self._run(invocation, preparation=preparation)
        finally:
            await self._close_live_projection()

    async def _run[OutputT](
        self, invocation: HarnessInvocation[OutputT], *, preparation: AttemptPreparationAccepted
    ) -> HarnessRunResult[OutputT]:
        state = self._control.current_state.envelope
        input_source, deferred_resume = _select_attempt_input(invocation, state)
        definition = compose_run_control(invocation.definition, self._control, self)
        pricing_catalog = await to_thread.run_sync(get_current_pricing_catalog)
        executable = self._builder.build(definition, pricing_catalog=pricing_catalog)
        bindings = invocation.collaborators.create_bindings()
        stream = _create_stream(
            executable,
            input_source=input_source,
            bindings=bindings,
            environment=_observe_environment(invocation.environment, self._projector),
            previous_state=state,
            deferred_resume=deferred_resume,
            usage=invocation.usage,
            usage_limits=invocation.usage_limits,
        )
        async with stream as entered:
            self._attach(entered, invocation.collaborators.instance, state.thread_id)
            try:
                await self._control.enter_harness(
                    HarnessRunIdentity(thread_id=entered.thread_id, run_id=entered.run_id),
                    preparation,
                )
                await self._control.after_stream_entry()
                return await self._consume(entered)
            finally:
                self._detach()

    def bind_model_attempt(self, ctx: RunContext[AgentContext]) -> HarnessContextBinding:
        self._require_context(ctx)
        run_token = self._require_run_token()
        model_attempt_token = object()
        self._model_attempt_token = model_attempt_token
        self._model_attempt_run_id = ctx.run_id
        return HarnessContextBinding(
            self._driver_token,
            run_token,
            model_attempt_token,
        )

    @asynccontextmanager
    async def hook_boundary(
        self,
        ctx: RunContext[AgentContext],
        binding: HarnessContextBinding | None,
    ) -> AsyncIterator[HarnessHookBoundary]:
        self._require_context(ctx)
        if binding is None:
            raise RunError(
                "Foundation run-control hook is missing its ModelAttempt binding.",
                code="foundation_control_identity_mismatch",
            )
        self.validate_binding(binding)
        if self._model_attempt_run_id != ctx.run_id:
            raise RunError(
                "Foundation run control received a binding from another ModelAttempt.",
                code="foundation_control_identity_mismatch",
            )
        if self._boundary is not None:
            raise RunError(
                "Foundation run-control hook boundaries cannot overlap.",
                code="foundation_control_reentrant",
            )
        boundary = _DriverHookBoundary(self, ctx)
        self._boundary = boundary
        try:
            yield boundary
        finally:
            boundary.close()
            self._boundary = None

    def validate_binding(self, binding: HarnessContextBinding) -> None:
        if (
            binding._driver_token is not self._driver_token
            or binding._run_token is not self._require_run_token()
            or binding._model_attempt_token is not self._model_attempt_token
        ):
            raise RunError(
                "Foundation run control received an incompatible Harness binding.",
                code="foundation_control_identity_mismatch",
            )

    def validate_boundary(self, boundary: HarnessHookBoundary) -> None:
        if boundary is not self._boundary:
            raise RunError(
                "Foundation run control received an inactive Harness hook boundary.",
                code="foundation_control_identity_mismatch",
            )

    async def steer(self, input: RunInputValue) -> str:
        return await self._require_stream().steer(input)

    async def cancel(self) -> None:
        stream = self._stream
        if stream is not None:
            stream.cancel()

    async def export_state(self) -> HarnessState:
        return await self._require_stream().export_state()

    async def _consume[OutputT](
        self,
        stream: HarnessRunStream[OutputT],
    ) -> HarnessRunResult[OutputT]:
        terminal: HarnessRunResult[OutputT] | None = None
        async for item in stream:
            if isinstance(item, HarnessRunResultEvent):
                if terminal is not None:
                    raise RunError(
                        "Harness stream emitted more than one terminal result.",
                        code="foundation_stream_terminal_duplicate",
                    )
                if item.thread_id != stream.thread_id or item.run_id != stream.run_id:
                    raise RunError(
                        "Harness terminal result does not match the entered stream.",
                        code="foundation_control_identity_mismatch",
                    )
                terminal = item.result
                if self._control.terminal_observation_allowed:
                    self._project_live(item)
                continue
            if terminal is not None:
                raise RunError(
                    "Harness stream emitted an observation after its terminal result.",
                    code="foundation_stream_event_after_terminal",
                )
            self._project_live(item)
        if terminal is None:
            raise RunError(
                "Harness stream ended without a terminal result.",
                code="foundation_stream_terminal_missing",
            )
        return terminal

    def _project_live(self, item: HarnessEvent | HarnessRunResultEvent[Any]) -> None:
        try:
            self._projector.project(item)
        except Exception:
            logger.exception(
                "Harness live observation projection failed",
                extra={
                    "event": "harness_live_projection_failed",
                    "harness_run_id": item.run_id,
                    "harness_sequence": item.sequence,
                },
            )

    async def _close_live_projection(self) -> None:
        try:
            await self._projector.close()
        except Exception:
            logger.exception(
                "Harness live observation projector close failed",
                extra={"event": "harness_live_projection_close_failed"},
            )

    def _attach[OutputT](
        self,
        stream: HarnessRunStream[OutputT],
        instance: AgentInstanceContext,
        thread_id: str,
    ) -> None:
        context = stream.context
        if stream.thread_id != thread_id or context.instance is not instance:
            raise RunError(
                "Harness Run identity does not match Foundation preparation.",
                code="foundation_control_identity_mismatch",
            )
        self._stream = stream
        self._run_token = object()

    def _detach(self) -> None:
        if self._boundary is not None:
            self._boundary.close()
        self._boundary = None
        self._stream = None
        self._model_attempt_token = None
        self._model_attempt_run_id = None
        self._run_token = None

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        if ctx.deps is not self._require_stream().context:
            raise RunError(
                "Foundation run control received an incompatible Harness context.",
                code="foundation_control_identity_mismatch",
            )

    def _require_run_token(self) -> object:
        if self._run_token is None:
            raise RunError(
                "The Harness driver is not attached to an active Run.",
                code="run_not_active",
            )
        return self._run_token

    def _require_stream(self) -> HarnessRunStream[Any]:
        if self._stream is None:
            raise RunError(
                "The Harness driver is not attached to an active Run.",
                code="run_not_active",
            )
        return self._stream


class _DriverHookBoundary:
    """Ephemeral adaptation of one currently awaited Harness hook context."""

    def __init__(self, driver: HarnessDriver, ctx: RunContext[AgentContext]) -> None:
        self._driver = driver
        self._ctx: RunContext[AgentContext] | None = ctx

    async def enqueue(
        self,
        input: RunInputValue,
        *,
        priority: Literal["asap"],
    ) -> str:
        ctx = self._require_context()
        enqueue_id = (
            ctx.enqueue(input, priority=priority) if isinstance(input, str) else ctx.enqueue(*input, priority=priority)
        )
        if enqueue_id is None:
            raise RunError(
                "A Thread inbox entry produced no Harness input.",
                code="foundation_inbox_input_invalid",
            )
        return enqueue_id

    async def export_state(
        self,
        complete_messages: Sequence[ModelMessage],
    ) -> HarnessState:
        return await self._require_context().deps.export_state(complete_messages)

    def close(self) -> None:
        self._ctx = None

    def _require_context(self) -> RunContext[AgentContext]:
        self._driver.validate_boundary(self)
        if self._ctx is None:
            raise RunError(
                "Harness hook boundary is no longer active.",
                code="foundation_control_identity_mismatch",
            )
        return self._ctx


def _select_attempt_input[OutputT](
    invocation: HarnessInvocation[OutputT],
    state: RunStateEnvelope,
) -> tuple[HarnessInput, DeferredToolResume | None]:
    if state.input_disposition == "applied":
        if state.host.deferred is not None:
            raise RunError(
                "Applied Foundation state still contains a deferred continuation.",
                code="foundation_run_state_invalid",
            )
        return ImmediateHarnessInput(), None

    deferred = state.host.deferred
    resume = invocation.deferred_resume
    if deferred is None:
        if resume is not None:
            raise RunError(
                "Foundation supplied deferred results without pending state.",
                code="foundation_deferred_resume_invalid",
            )
    else:
        if resume is None:
            raise RunError(
                "Foundation pending state requires complete deferred results.",
                code="foundation_deferred_resume_required",
            )
        try:
            expected = _DEFERRED_REQUESTS_ADAPTER.validate_python(deferred.requests)
        except ValidationError as error:
            raise RunError(
                "Foundation state contains invalid deferred requests.",
                code="foundation_run_state_invalid",
            ) from error
        if expected != resume.requests:
            raise RunError(
                "Foundation deferred results do not match the committed pending requests.",
                code="foundation_deferred_resume_mismatch",
            )
    return invocation.input, resume


def _create_stream[OutputT](
    executable: ExecutableAgent[OutputT],
    *,
    input_source: HarnessInput,
    bindings: RunBindings,
    environment: FoundationHarnessEnvironment,
    previous_state: RunStateEnvelope,
    deferred_resume: DeferredToolResume | None,
    usage: RunUsage | None,
    usage_limits: UsageLimits | None,
) -> HarnessRunStream[OutputT]:
    if isinstance(input_source, MaterializedHarnessInput):
        input_value = None
        input_factory = input_source.factory
    else:
        input_value = input_source.value
        input_factory = None
    if isinstance(environment, SingleHarnessEnvironment):
        return executable.stream(
            input_value,
            input_factory=input_factory,
            environment=environment.entry,
            bindings=bindings,
            previous_state=previous_state.harness,
            deferred_resume=deferred_resume,
            usage=usage,
            usage_limits=usage_limits,
        )
    if isinstance(environment, MountedHarnessEnvironments):
        return executable.stream(
            input_value,
            input_factory=input_factory,
            environments=environment.entries,
            default_environment=environment.default_environment,
            bindings=bindings,
            previous_state=previous_state.harness,
            deferred_resume=deferred_resume,
            usage=usage,
            usage_limits=usage_limits,
        )
    return executable.stream(
        input_value,
        input_factory=input_factory,
        bindings=bindings,
        previous_state=previous_state.harness,
        deferred_resume=deferred_resume,
        usage=usage,
        usage_limits=usage_limits,
    )


def _observe_environment(
    environment: FoundationHarnessEnvironment,
    projector: HarnessEventProjector,
) -> FoundationHarnessEnvironment:
    if isinstance(environment, SingleHarnessEnvironment):
        return SingleHarnessEnvironment(observe_environment_entry(environment.entry, projector))
    if isinstance(environment, MountedHarnessEnvironments):
        return MountedHarnessEnvironments(
            entries={name: observe_environment_entry(entry, projector) for name, entry in environment.entries.items()},
            default_environment=environment.default_environment,
        )
    return environment


def _require_environment_entry(entry: object) -> None:
    if not isinstance(entry, Environment | EnvironmentMount):
        raise TypeError("Harness environment entry must be an Environment or EnvironmentMount")


__all__ = [
    "FoundationHarnessEnvironment",
    "HarnessCollaborators",
    "HarnessDriver",
    "HarnessEventProjector",
    "HarnessInput",
    "HarnessInvocation",
    "ImmediateHarnessInput",
    "MaterializedHarnessInput",
    "MountedHarnessEnvironments",
    "NoHarnessEnvironment",
    "SingleHarnessEnvironment",
]
