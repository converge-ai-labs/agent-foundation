"""Public executable entry points over the shared Run lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, overload

from pydantic import TypeAdapter
from pydantic_ai import Agent
from pydantic_ai.realtime import AudioRetention, RealtimeModel, RealtimeModelSettings
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import RunUsage, UsageLimits

from a13n_harness._run_stream import HarnessRunStream as HarnessRunStream
from a13n_harness._run_stream import _usage_limits_from_spec
from a13n_harness.context import AgentContext, RunBindings, SubagentCollection
from a13n_harness.environment.sources import EnvironmentEntry
from a13n_harness.errors import RunError
from a13n_harness.events import HarnessRunResultEvent
from a13n_harness.input import RunInputFactory, RunInputValue
from a13n_harness.live import HarnessLiveStream
from a13n_harness.models.binding import RunModelResolver
from a13n_harness.observation import _ObservationRuntime
from a13n_harness.plugins import AbstractHarnessPlugin
from a13n_harness.pricing import AbstractModelCostCapability
from a13n_harness.recovery import ToolRecoveryMode
from a13n_harness.result import HarnessRunResult
from a13n_harness.state import HarnessState
from a13n_harness.tools.deferred import DeferredToolResume

if TYPE_CHECKING:
    from a13n_harness.builder import AgentDefinition


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
        async with HarnessRunStream(
            executable=self,
            input=input,
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
        return HarnessRunStream(
            executable=self,
            input=input,
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

    def live(
        self,
        *,
        model: RealtimeModel,
        model_settings: RealtimeModelSettings | None = None,
        environment: EnvironmentEntry | None = None,
        environments: Mapping[str, EnvironmentEntry] | None = None,
        default_environment: str | None = None,
        bindings: RunBindings | None = None,
        previous_state: HarnessState | None = None,
        audio_retention: AudioRetention = "transcript_only",
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessLiveStream:
        """Create one duplex Live Run; realtime output is speech/text, not OutputT."""
        return HarnessLiveStream(
            executable=self,
            model=model,
            model_settings=model_settings,
            environment=environment,
            environments=environments,
            default_environment=default_environment,
            bindings=bindings,
            previous_state=previous_state,
            audio_retention=audio_retention,
            usage=usage,
            usage_limits=usage_limits,
        )
