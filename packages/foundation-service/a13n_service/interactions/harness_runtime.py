"""Exact Foundation construction and invocation of one logical Harness Run."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

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
    HarnessObservationContext,
    HarnessRunResult,
    HarnessRunStream,
    RunBindings,
    RunInputFactory,
    RunInputValue,
    RunModelResolver,
)
from a13n_harness.errors import RunError
from a13n_harness.model_context import ModelContextMiddleware
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import RunUsage, UsageLimits

from .attempts import AttemptMutationReceipt, AttemptPreparationAccepted
from .harness_control import RunControlCoordinator, compose_run_control
from .harness_stream import (
    HarnessResultCoordinator,
    HarnessStreamConsumer,
    HarnessTerminalConsumption,
)
from .objects import StoredRunState
from .state import RunStateEnvelope

_DEFERRED_REQUESTS_ADAPTER = TypeAdapter(DeferredToolRequests)


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


type FoundationHarnessInput = ImmediateHarnessInput | MaterializedHarnessInput


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
class FoundationHarnessCollaborators:
    """Fresh typed collaborators supplied to one logical Harness Run."""

    instance: AgentInstanceContext
    model_resolver: RunModelResolver | None = None
    capabilities: tuple[AbstractCapability[AgentContext], ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    model_context: ModelContextMiddleware | None = None
    observation: HarnessObservationContext | None = None
    toolset_instructions: bool | None = None

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
            toolset_instructions=self.toolset_instructions,
            capabilities=self.capabilities,
            metadata=self.metadata,
            model_context=self.model_context,
            observation=self.observation,
        )


@dataclass(frozen=True, slots=True)
class FoundationHarnessInvocation[OutputT]:
    """Complete non-authoritative inputs for one entered Harness stream."""

    definition: AgentDefinition[OutputT]
    input: FoundationHarnessInput
    collaborators: FoundationHarnessCollaborators
    environment: FoundationHarnessEnvironment = field(default_factory=NoHarnessEnvironment)
    deferred_resume: DeferredToolResume | None = None
    usage: RunUsage | None = None
    usage_limits: UsageLimits | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.definition, AgentDefinition):
            raise TypeError("Foundation Harness definition must be an AgentDefinition")
        if not isinstance(self.input, ImmediateHarnessInput | MaterializedHarnessInput):
            raise TypeError("Foundation Harness input source is invalid")
        if not isinstance(self.collaborators, FoundationHarnessCollaborators):
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


@dataclass(frozen=True, slots=True)
class FoundationHarnessYielded[OutputT]:
    """A planned local cancellation followed by its durable yielded transition."""

    result: HarnessRunResult[OutputT]
    mutation: AttemptMutationReceipt


type FoundationHarnessExecution[OutputT] = HarnessTerminalConsumption[OutputT] | FoundationHarnessYielded[OutputT]


class FoundationHarnessRuntimeCoordinator(
    RunControlCoordinator,
    HarnessResultCoordinator,
    Protocol,
):
    """Attempt coordinator surface required by the outer Harness runtime."""

    @property
    def current_state(self) -> StoredRunState: ...

    async def attach_stream[OutputT](
        self,
        stream: HarnessRunStream[OutputT],
        preparation: AttemptPreparationAccepted,
    ) -> None: ...

    async def complete_handoff(self) -> AttemptMutationReceipt: ...


class FoundationHarnessRuntime:
    """Build, enter, consume, and finalize one Foundation-owned Harness Run."""

    def __init__(self, builder: HarnessBuilder) -> None:
        if not isinstance(builder, HarnessBuilder):
            raise TypeError("Foundation Harness runtime requires a HarnessBuilder")
        self._builder = builder

    async def execute[OutputT](
        self,
        invocation: FoundationHarnessInvocation[OutputT],
        *,
        coordinator: FoundationHarnessRuntimeCoordinator,
        preparation: AttemptPreparationAccepted,
        consumer: HarnessStreamConsumer,
    ) -> FoundationHarnessExecution[OutputT]:
        state = coordinator.current_state.envelope
        input_source, deferred_resume = _select_attempt_input(invocation, state)
        definition = compose_run_control(invocation.definition, coordinator)
        executable = self._builder.build(definition)
        bindings = invocation.collaborators.create_bindings()
        stream = _create_stream(
            executable,
            input_source=input_source,
            bindings=bindings,
            environment=invocation.environment,
            previous_state=state,
            deferred_resume=deferred_resume,
            usage=invocation.usage,
            usage_limits=invocation.usage_limits,
        )
        async with stream as entered:
            await coordinator.attach_stream(entered, preparation)
            consumption = await consumer.consume(entered, coordinator)
        if isinstance(consumption, HarnessTerminalConsumption):
            return consumption
        return FoundationHarnessYielded(
            result=consumption.result,
            mutation=await coordinator.complete_handoff(),
        )


def _select_attempt_input[OutputT](
    invocation: FoundationHarnessInvocation[OutputT],
    state: RunStateEnvelope,
) -> tuple[FoundationHarnessInput, DeferredToolResume | None]:
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
    input_source: FoundationHarnessInput,
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


def _require_environment_entry(entry: object) -> None:
    if not isinstance(entry, Environment | EnvironmentMount):
        raise TypeError("Harness environment entry must be an Environment or EnvironmentMount")


__all__ = [
    "FoundationHarnessCollaborators",
    "FoundationHarnessEnvironment",
    "FoundationHarnessExecution",
    "FoundationHarnessInput",
    "FoundationHarnessInvocation",
    "FoundationHarnessRuntime",
    "FoundationHarnessRuntimeCoordinator",
    "FoundationHarnessYielded",
    "ImmediateHarnessInput",
    "MaterializedHarnessInput",
    "MountedHarnessEnvironments",
    "NoHarnessEnvironment",
    "SingleHarnessEnvironment",
]
