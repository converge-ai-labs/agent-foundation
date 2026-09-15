from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any, Literal

from a13n_environment import (
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    Environment,
)
from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    AgentSpec,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessInstrumentation,
    HarnessObservationContext,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessState,
    HarnessTraceContent,
    RunBindings,
    SubagentDefinition,
)
from a13n_harness.capabilities import (
    CompactionCapability,
    CompactionPolicy,
    HandoffCapability,
    SubagentCapability,
)
from a13n_harness.environment import (
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
)
from a13n_harness.environment.advanced import EmptyEnvironmentRuntime
from a13n_harness.pricing import (
    AbstractModelCostCapability,
    ModelCostInput,
    ModelCostQuote,
)
from a13n_harness.tools import (
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolInvocationContext,
)
from a13n_harness.toolsets import AgentMediaUnderstandingProvider
from opentelemetry import trace
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage, UsageLimits

Scenario = Literal["summary", "compaction", "view", "subagent"]

_SCENARIOS: tuple[Scenario, ...] = ("summary", "compaction", "view", "subagent")
_PROJECT_ID = "agent-foundation-local"
_IMAGE_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    trace_id: str
    result: HarnessRunResult[str]
    context_events: tuple[str, ...]


class _ObservationPresentationProcessor(SpanProcessor):
    """Map bounded Harness fields to Langfuse and Logfire display attributes."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._trace_fields: dict[int, dict[str, Any]] = {}
        self._trace_ids_by_name: dict[str, str] = {}

    def on_start(self, span: Span, parent_context: object | None = None) -> None:
        del parent_context
        attributes = span.attributes or {}
        operation = attributes.get("gen_ai.operation.name")
        if span.name == "harness.run" or operation == "invoke_agent":
            span.set_attribute("langfuse.observation.type", "agent")
        elif operation == "execute_tool":
            span.set_attribute("langfuse.observation.type", "tool")

        trace_id = span.get_span_context().trace_id
        if span.name == "harness.run" and span.parent is None:
            fields: dict[str, Any] = {}
            name = attributes.get("a13n.observation.name")
            if isinstance(name, str):
                fields["langfuse.trace.name"] = name
                span.set_attribute("logfire.msg", name)
                with self._lock:
                    self._trace_ids_by_name[name] = f"{trace_id:032x}"
            session_id = attributes.get("a13n.observation.session.id")
            if isinstance(session_id, str):
                fields["langfuse.session.id"] = session_id
            user_id = attributes.get("a13n.user.id")
            if isinstance(user_id, str):
                fields["langfuse.user.id"] = user_id
            labels = attributes.get("a13n.observation.labels")
            if isinstance(labels, (list, tuple)) and all(isinstance(label, str) for label in labels):
                normalized_labels = tuple(label for label in labels if isinstance(label, str))
                fields["langfuse.trace.tags"] = normalized_labels
                fields["logfire.tags"] = normalized_labels
            for key, value in attributes.items():
                prefix = "a13n.observation.metadata."
                if key.startswith(prefix) and isinstance(value, (str, bool, int, float)):
                    fields[f"langfuse.trace.metadata.{key.removeprefix(prefix)}"] = json.dumps(
                        value,
                        ensure_ascii=False,
                        allow_nan=False,
                    )
            fields["langfuse.version"] = "observation-demo-v4"
            fields["langfuse.release"] = "local-validation-2026-08"
            fields["langfuse.environment"] = "local"
            with self._lock:
                self._trace_fields[trace_id] = fields

        with self._lock:
            fields = dict(self._trace_fields.get(trace_id, {}))
        for key, value in fields.items():
            span.set_attribute(key, value)

        if span.name == "harness.operation":
            kind = attributes.get("a13n.operation.kind")
            if isinstance(kind, str):
                span.set_attribute("logfire.msg", f"harness {kind}")

    def on_end(self, span: ReadableSpan) -> None:
        if span.parent is None and span.context is not None:
            with self._lock:
                self._trace_fields.pop(span.context.trace_id, None)

    def trace_id(self, name: str) -> str:
        with self._lock:
            try:
                return self._trace_ids_by_name[name]
            except KeyError:
                raise RuntimeError(f"No root Harness trace was observed for {name!r}") from None

    def shutdown(self) -> None:
        return None


class _DemoCostCapability(AbstractModelCostCapability):
    @property
    def revision(self) -> str:
        return "observation-demo-2026-08"

    def quote(self, value: ModelCostInput) -> ModelCostQuote:
        del value
        return ModelCostQuote(
            cost_usd=Decimal("0.00125"),
            source="custom",
            pricing_revision=self.revision,
            rule_id="synthetic-fixed-cost",
        )


class _UsageFunctionModel(FunctionModel):
    """Deterministic streaming model with synthetic provider usage counters."""

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[object] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        async with super().request_stream(
            messages,
            model_settings,
            model_request_parameters,
            run_context,
        ) as stream:
            stream.usage.input_tokens = 120
            stream.usage.cache_write_tokens = 12
            stream.usage.cache_read_tokens = 24
            stream.usage.output_tokens = 8
            stream.usage.details = {"reasoning_tokens": 3}
            yield stream


class _AllowInvocations:
    async def __call__(
        self,
        invocation: ToolInvocationContext,
        metadata: HarnessToolMetadata,
        *,
        context: AgentContext,
    ) -> InvocationPolicyDecision:
        del invocation, metadata, context
        return InvocationPolicyDecision.allow()


def _tool_returns(messages: Sequence[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _summary_model() -> _UsageFunctionModel:
    call_count = 0

    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal call_count
        del messages
        call_count += 1
        if call_count == 1:
            if "summarize" not in {tool.name for tool in info.function_tools}:
                raise RuntimeError("The summarize tool is not available")
            yield {
                0: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps(
                        {
                            "content": (
                                "The synthetic analysis is complete. Preserve deterministic Observation "
                                "identity, usage, cost, and continuation evidence."
                            ),
                            "files_to_inspect": ["dev/observation-demo/agent.py"],
                        }
                    ),
                    tool_call_id="summary-demo-1",
                )
            }
            return
        yield "Explicit summarize restored the continuation and completed the task."

    return _UsageFunctionModel(stream_function=stream, model_name="summary-demo-model")


def _compaction_model() -> _UsageFunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        settings = info.model_settings or {}
        if settings.get("tool_choice") == "none":
            yield (
                "The earlier synthetic discussion established the Observation demo. "
                "Continue with the retained user intent and bounded telemetry policy."
            )
            return
        yield "Automatic compaction replaced the long history and completed the task."

    return _UsageFunctionModel(stream_function=stream, model_name="compaction-demo-model")


def _view_model() -> _UsageFunctionModel:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = _tool_returns(messages)
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps(
                        {
                            "file_path": "/workspace/observation.png",
                            "instructions": "Transcribe the visible synthetic label and note uncertainty.",
                        }
                    ),
                    tool_call_id="view-observation-image-1",
                )
            }
            return
        yield f"View completed through the media-understanding Agent: {returns[-1].content}"

    return _UsageFunctionModel(stream_function=stream, model_name="view-demo-model")


def _subagent_parent_model() -> _UsageFunctionModel:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = _tool_returns(messages)
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps(
                        {
                            "subagent": "reviewer",
                            "prompt": "Review the bounded Observation identity and cost evidence.",
                        }
                    ),
                    tool_call_id="delegate-reviewer-1",
                )
            }
            return
        yield "Inline reviewer completed and the parent accepted its bounded report."

    return _UsageFunctionModel(stream_function=stream, model_name="subagent-parent-demo-model")


def _subagent_child_model() -> _UsageFunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "Reviewer confirmed parent-child lineage and one nested logical run."

    return _UsageFunctionModel(stream_function=stream, model_name="subagent-child-demo-model")


def _compaction_state() -> HarnessState:
    return HarnessState.new(
        message_history=(
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content=(
                            "Prepare a synthetic Observation demonstration with enough provider-reported "
                            "usage to trigger automatic compaction."
                        )
                    )
                ]
            ),
            ModelResponse(
                parts=[TextPart(content="The initial synthetic analysis produced a deliberately long history.")],
                usage=RequestUsage(input_tokens=1_100, output_tokens=100),
                metadata={"demo": "preloaded-history"},
            ),
        )
    )


def _instrumentation(tracer_provider: TracerProvider) -> HarnessInstrumentation:
    return HarnessInstrumentation(
        tracer_provider=tracer_provider,
        trace_content=HarnessTraceContent(os.environ.get("A13N_HARNESS_TRACE_CONTENT", "standard")),
    )


def _main_identity(scenario: Scenario) -> AgentIdentityRef:
    return AgentIdentityRef(
        issuer="https://identity.observation.local",
        subject=f"observation-demo:{scenario}",
        agent_id=f"observation-{scenario}-agent",
        user_id="observation-user-42",
        evaluation_cohort="must-not-be-projected",
    )


def _local_environment(root: Path) -> Environment:
    return DirectLocalEnvironmentProvider().create_environment(
        environment_id="observation-demo-local",
        configuration=DirectLocalProviderConfiguration(root=DirectLocalRootConfiguration(path=root)),
        state=None,
        runtime=None,
    )


def _media_provider() -> AgentMediaUnderstandingProvider:
    def understand(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        assert info.model_settings is not None
        return ModelResponse(
            parts=[TextPart("Detected text: OBSERVATION DEMO. Unclear or omitted details: none.")],
            model_name="observation-vision-model",
            provider_name="function",
            usage=RequestUsage(
                input_tokens=64,
                cache_read_tokens=16,
                output_tokens=18,
                input_audio_tokens=2,
                output_audio_tokens=3,
                details={"reasoning_tokens": 4},
            ),
        )

    return AgentMediaUnderstandingProvider(
        models={"image": FunctionModel(function=understand, model_name="observation-vision-model")}
    )


def _build_standard_scenario(
    scenario: Literal["summary", "compaction", "view"],
    instrumentation: HarnessInstrumentation,
):
    cost = _DemoCostCapability()
    if scenario == "summary":
        return HarnessBuilder(instrumentation=instrumentation).build(
            AgentSpec(
                name="summary-observation-demo",
                instructions="Use summarize exactly once, then complete from restored context.",
            ),
            output_type=str,
            model=_summary_model(),
            capabilities=(HandoffCapability(), cost),
        )
    if scenario == "compaction":
        return HarnessBuilder(instrumentation=instrumentation).build(
            AgentSpec(
                name="compaction-observation-demo",
                instructions="Demonstrate provider-usage-triggered automatic context compaction.",
            ),
            output_type=str,
            model=_compaction_model(),
            capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=1_000)), cost),
        )
    return HarnessBuilder(instrumentation=instrumentation).build(
        AgentSpec(
            name="view-observation-demo",
            instructions="Use view once to transcribe the synthetic image through the media Agent.",
        ),
        output_type=str,
        model=_view_model(),
        capabilities=(
            DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
            cost,
        ),
    )


_SUBAGENT_PARENT_INSTANCE_ID = "observation-subagent-parent-instance"


def _build_subagent_scenario(instrumentation: HarnessInstrumentation):
    child = AgentDefinition(
        agent=AgentSpec(name="reviewer-observation-demo"),
        output_type=str,
        definition_id="observation-reviewer-v1",
        model=_subagent_child_model(),
    )
    parent = AgentDefinition(
        agent=AgentSpec(name="subagent-observation-demo"),
        output_type=str,
        definition_id="observation-parent-v1",
        model=_subagent_parent_model(),
        capabilities=(
            SubagentCapability(),
            _DemoCostCapability(),
        ),
        subagents=(
            SubagentDefinition(
                name="reviewer",
                description="Review one bounded Observation report.",
                agent=child,
                usage_limits=UsageLimits(request_limit=5, total_tokens_limit=80_000),
            ),
        ),
    )
    return HarnessBuilder(instrumentation=instrumentation).build(parent)


async def _subagent_bindings() -> RunBindings:
    return RunBindings(
        instance=AgentInstanceContext(
            identity=_main_identity("subagent"),
            agent_instance_id=_SUBAGENT_PARENT_INSTANCE_ID,
            actor="observation-demo-host",
        ),
        environment=EmptyEnvironmentRuntime(),
        capabilities=(InvocationPolicyCapability(evaluator=_AllowInvocations(), max_dispatch_retries=0),),
    )


def _prompt(scenario: Scenario) -> str:
    return {
        "summary": "Prepare the explicit summarize Observation demonstration.",
        "compaction": "Continue the automatic compaction Observation demonstration.",
        "view": "Read the synthetic image and report its visible text.",
        "subagent": "Delegate one bounded Observation review to the reviewer.",
    }[scenario]


def _observation_context(scenario: Scenario) -> HarnessObservationContext:
    return HarnessObservationContext(
        name=f"observation-{scenario}",
        session_id="observation-demo-2026-08",
        labels=("a13n-harness", "observation-demo", f"scenario:{scenario}"),
        metadata={
            "scenario": scenario,
            "evaluation": "synthetic-pass",
            "synthetic": True,
            "demo_revision": 4,
            "string_boolean": "true",
            "string_number": "3",
        },
    )


def _configure_internal_agent_observation(tracer_provider: TracerProvider) -> None:
    Agent.instrument_all(
        InstrumentationSettings(
            tracer_provider=tracer_provider,
            meter_provider=NoOpMeterProvider(),
            include_content=os.environ.get("A13N_HARNESS_TRACE_CONTENT", "standard") != "none",
            include_binary_content=os.environ.get("A13N_HARNESS_TRACE_CONTENT", "standard") == "full",
            include_model_request_parameters=os.environ.get("A13N_HARNESS_TRACE_CONTENT", "standard") == "full",
            version=5,
        )
    )


async def _run_scenario(
    scenario: Scenario,
    tracer_provider: TracerProvider,
    presentation: _ObservationPresentationProcessor,
) -> ScenarioResult:
    _configure_internal_agent_observation(tracer_provider)
    instrumentation = _instrumentation(tracer_provider)
    previous_state = _compaction_state() if scenario == "compaction" else None
    prompt = _prompt(scenario)
    observation = _observation_context(scenario)
    context_events: list[str] = []
    terminal: HarnessRunResult[str] | None = None
    workspace: TemporaryDirectory[str] | None = None
    environments: dict[str, Environment] | None = None

    if scenario == "subagent":
        executable = _build_subagent_scenario(instrumentation)
        bindings = await _subagent_bindings()
        bindings = replace(bindings, observation=observation)
    elif scenario == "view":
        workspace = TemporaryDirectory(prefix="observation-demo-")
        root = Path(workspace.name)
        (root / "observation.png").write_bytes(_IMAGE_BYTES)
        executable = _build_standard_scenario("view", instrumentation)
        environments = {"local": _local_environment(root)}
        bindings = RunBindings(
            instance=AgentInstanceContext(
                identity=_main_identity("view"),
                agent_instance_id="observation-view-instance",
                actor="observation-demo-host",
            ),
            capabilities=(InvocationPolicyCapability(evaluator=_AllowInvocations(), max_dispatch_retries=0),),
            file_media_understanding=_media_provider(),
            observation=observation,
        )
    else:
        executable = _build_standard_scenario(scenario, instrumentation)
        bindings = RunBindings(
            instance=AgentInstanceContext(
                identity=_main_identity(scenario),
                agent_instance_id=f"observation-{scenario}-instance",
                actor="observation-demo-host",
            ),
            environment=EmptyEnvironmentRuntime(),
            observation=observation,
        )

    try:
        async with executable.stream(
            prompt,
            bindings=bindings,
            previous_state=previous_state,
            environments=environments,
        ) as run:
            async for item in run:
                if isinstance(item, HarnessEvent):
                    event = item.event
                    if isinstance(event, HarnessExtensionEvent) and event.kind == "context":
                        payload = event.payload
                        if isinstance(payload, dict):
                            event_type = payload.get("type")
                            if isinstance(event_type, str):
                                context_events.append(event_type)
                elif isinstance(item, HarnessRunResultEvent):
                    terminal = item.result
    finally:
        if workspace is not None:
            workspace.cleanup()

    if terminal is None:
        raise RuntimeError(f"{scenario} did not emit a terminal result")
    terminal.output_or_raise()
    return ScenarioResult(
        trace_id=presentation.trace_id(observation.name or "harness.run"),
        result=terminal,
        context_events=tuple(context_events),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run complete Agent Harness traces against local Langfuse.")
    parser.add_argument(
        "scenario",
        choices=(*_SCENARIOS, "all"),
        nargs="?",
        default="all",
    )
    args = parser.parse_args()
    scenarios: tuple[Scenario, ...] = _SCENARIOS if args.scenario == "all" else (args.scenario,)

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        raise RuntimeError("The executable Host did not install an OpenTelemetry SDK TracerProvider")
    presentation = _ObservationPresentationProcessor()
    provider.add_span_processor(presentation)

    base_url = os.environ.get("LANGFUSE_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
    for scenario in scenarios:
        observed = await _run_scenario(scenario, provider, presentation)
        print(f"scenario={scenario}")
        print(f"trace_id={observed.trace_id}")
        print(f"output={observed.result.output_or_raise()}")
        print(f"context_events={','.join(observed.context_events)}")
        print(f"usage_records={len(observed.result.usage_records)}")
        endpoint = os.environ.get(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        )
        if "/api/public/otel" in endpoint:
            print(f"trace_url={base_url}/project/{_PROJECT_ID}/traces/{observed.trace_id}")
        print()

    if not provider.force_flush(timeout_millis=10_000):
        raise RuntimeError("OpenTelemetry traces did not flush within 10 seconds")


if __name__ == "__main__":
    asyncio.run(main())
