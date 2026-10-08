from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest
from a13n_harness import (
    AbstractHarnessPlugin,
    AgentIdentityRef,
    AgentInstanceContext,
    DefinitionError,
    HarnessBuilder,
    HarnessInstrumentation,
    HarnessObservationContext,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessTraceContent,
    ModelRecoveryPolicy,
    ModelResolutionError,
    RunBindings,
    RunCleanupError,
)
from a13n_harness.observation import redact_json
from a13n_harness.plugins import (
    PluginRunExchange,
    PluginRunNext,
)
from a13n_harness.pricing import (
    AbstractModelCostCapability,
    ModelCostInput,
    ModelCostQuote,
)
from a13n_harness.usage import RunUsageSummary
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, Instrumentation
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.instrumented import InstrumentationSettings, InstrumentedModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings

pytestmark = pytest.mark.anyio


def _providers() -> tuple[TracerProvider, InMemorySpanExporter, MeterProvider, InMemoryMetricReader]:
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    return tracer_provider, span_exporter, meter_provider, metric_reader


def _model(current_span_ids: list[int] | None = None) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        if current_span_ids is not None:
            current_span_ids.append(trace.get_current_span().get_span_context().span_id)
        yield "ok"

    return FunctionModel(stream_function=stream)


def _build(
    instrumentation: HarnessInstrumentation | None,
    *,
    model: Any | None = None,
):
    return HarnessBuilder(instrumentation=instrumentation).build(
        AgentSpec(name="observed-agent"),
        output_type=str,
        model=model or _model(),
    )


def _instrumentation_capabilities(executable: Any) -> list[Instrumentation]:
    leaves: list[AbstractCapability[Any]] = []
    executable._agent.root_capability.apply(leaves.append)
    return [capability for capability in leaves if isinstance(capability, Instrumentation)]


def _metric_map(reader: InMemoryMetricReader) -> dict[str, Any]:
    data = reader.get_metrics_data()
    return {
        metric.name: metric
        for resource_metrics in data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


def test_instrumentation_configuration_rejects_invalid_ownership_and_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(DefinitionError, match="at least one") as missing:
        HarnessInstrumentation()
    assert missing.value.code == "instrumentation_provider_missing"

    _, _, meter_provider, _ = _providers()
    metrics_only = HarnessInstrumentation(meter_provider=meter_provider)
    assert metrics_only.trace_content is HarnessTraceContent.STANDARD

    with pytest.raises(DefinitionError, match="trace_content") as content:
        HarnessInstrumentation(
            meter_provider=meter_provider,
            trace_content="standard",  # type: ignore[arg-type]
        )
    assert content.value.code == "instrumentation_policy_invalid"

    with pytest.raises(DefinitionError, match="HarnessInstrumentation") as invalid:
        HarnessBuilder(instrumentation=object())  # type: ignore[arg-type]
    assert invalid.value.code == "instrumentation_invalid"

    assert _build(None) is not None
    with pytest.raises(DefinitionError) as selection_invalid:
        HarnessBuilder(instrumentation="invalid")  # type: ignore[arg-type]
    assert selection_invalid.value.code == "instrumentation_invalid"


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("A13N_HARNESS_TRACE_LEVEL", "standard"),
        ("A13N_HARNESS_TRACE_CONTENT", "invalid"),
        ("A13N_HARNESS_METRICS", "invalid"),
    ),
)
def test_environment_rejects_unsupported_observation_policy(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(DefinitionError) as invalid:
        HarnessBuilder()

    assert invalid.value.code == "instrumentation_environment_invalid"
    assert invalid.value.details == {"field": name}


def test_environment_defaults_to_trace_off_with_dormant_standard_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("A13N_HARNESS_TRACE_LEVEL", raising=False)
    monkeypatch.delenv("A13N_HARNESS_TRACE_CONTENT", raising=False)
    monkeypatch.delenv("A13N_HARNESS_METRICS", raising=False)

    assert HarnessInstrumentation.from_environment() is None

    _, _, meter_provider, _ = _providers()
    monkeypatch.setenv("A13N_HARNESS_METRICS", "standard")
    monkeypatch.setattr(
        trace,
        "get_tracer_provider",
        lambda: pytest.fail("trace provider must not be selected while tracing is off"),
    )
    configuration = HarnessInstrumentation.from_environment(meter_provider=meter_provider)
    assert configuration is not None
    assert configuration.tracer_provider is None
    assert configuration.meter_provider is meter_provider
    assert configuration.trace_content is HarnessTraceContent.STANDARD


def test_builder_environment_mode_uses_otel_global_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracer_provider, _, meter_provider, _ = _providers()
    monkeypatch.setenv("A13N_HARNESS_TRACE_LEVEL", "verbose")
    monkeypatch.delenv("A13N_HARNESS_TRACE_CONTENT", raising=False)
    monkeypatch.setenv("A13N_HARNESS_METRICS", "standard")
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: tracer_provider)
    monkeypatch.setattr(metrics, "get_meter_provider", lambda: meter_provider)

    executable = HarnessBuilder().build(
        AgentSpec(name="environment-agent"),
        output_type=str,
        model=_model(),
    )

    configuration = executable._observation.configuration
    assert configuration is not None
    assert configuration.tracer_provider is tracer_provider
    assert configuration.meter_provider is meter_provider
    assert configuration.trace_content is HarnessTraceContent.STANDARD


def test_signal_matrix_uses_explicit_noop_providers_and_suppresses_ambient_instrumentation() -> None:
    tracer_provider, _, meter_provider, _ = _providers()

    disabled = _build(None)
    trace_only = _build(HarnessInstrumentation(tracer_provider=tracer_provider))
    metrics_only = _build(HarnessInstrumentation(meter_provider=meter_provider))
    both = _build(HarnessInstrumentation(tracer_provider=tracer_provider, meter_provider=meter_provider))

    assert disabled._agent.instrument is False
    assert trace_only._agent.instrument is False
    assert metrics_only._agent.instrument is False
    assert both._agent.instrument is False
    assert _instrumentation_capabilities(disabled) == []

    trace_capability = _instrumentation_capabilities(trace_only)
    metrics_capability = _instrumentation_capabilities(metrics_only)
    both_capability = _instrumentation_capabilities(both)
    assert len(trace_capability) == len(metrics_capability) == len(both_capability) == 1
    assert type(trace_capability[0].settings.meter).__name__ == "NoOpMeter"
    assert type(metrics_capability[0].settings.tracer).__name__ == "NoOpTracer"
    assert type(both_capability[0].settings.tracer).__name__ != "NoOpTracer"
    assert type(both_capability[0].settings.meter).__name__ != "NoOpMeter"


async def test_shared_provider_exports_one_span_hierarchy_to_multiple_backends() -> None:
    first_exporter = InMemorySpanExporter()
    second_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(first_exporter))
    tracer_provider.add_span_processor(SimpleSpanProcessor(second_exporter))
    executable = _build(HarnessInstrumentation(tracer_provider=tracer_provider))

    result = await executable.run("hello", bindings=RunBindings.embedded())
    assert result.status == "completed"

    def hierarchy(exporter: InMemorySpanExporter) -> set[tuple[int, int, int | None, str]]:
        return {
            (
                span.context.trace_id,
                span.context.span_id,
                span.parent.span_id if span.parent is not None else None,
                span.name,
            )
            for span in exporter.get_finished_spans()
        }

    first_hierarchy = hierarchy(first_exporter)
    second_hierarchy = hierarchy(second_exporter)
    assert first_hierarchy == second_hierarchy
    assert len(first_hierarchy) == len(first_exporter.get_finished_spans())
    assert sum(span.name == "harness.run" for span in first_exporter.get_finished_spans()) == 1


def test_trace_content_maps_to_pydantic_instrumentation_settings() -> None:
    tracer_provider, _, _, _ = _providers()
    none = _build(
        HarnessInstrumentation(
            tracer_provider=tracer_provider,
            trace_content=HarnessTraceContent.NONE,
        )
    )
    standard = _build(
        HarnessInstrumentation(
            tracer_provider=tracer_provider,
            trace_content=HarnessTraceContent.STANDARD,
        )
    )
    full = _build(
        HarnessInstrumentation(
            tracer_provider=tracer_provider,
            trace_content=HarnessTraceContent.FULL,
        )
    )

    none_settings = _instrumentation_capabilities(none)[0].settings
    standard_settings = _instrumentation_capabilities(standard)[0].settings
    full_settings = _instrumentation_capabilities(full)[0].settings
    assert none_settings.version == standard_settings.version == full_settings.version == 5
    assert none_settings.include_content is False
    assert none_settings.include_binary_content is False
    assert none_settings.include_model_request_parameters is False
    assert standard_settings.include_content is True
    assert standard_settings.include_binary_content is False
    assert standard_settings.include_model_request_parameters is False
    assert full_settings.include_content is True
    assert full_settings.include_binary_content is True
    assert full_settings.include_model_request_parameters is True


class _DynamicInstrumentationCapability(AbstractCapability[Any]):
    def __init__(self, instrumentation: Instrumentation) -> None:
        self._instrumentation = instrumentation

    async def for_run(self, ctx: Any) -> AbstractCapability[Any]:
        del ctx
        return self._instrumentation


def test_pydantic_instrumentation_and_instrumented_models_are_rejected() -> None:
    tracer_provider, _, _, _ = _providers()
    pydantic_instrumentation = Instrumentation(settings=InstrumentationSettings(tracer_provider=tracer_provider))
    with pytest.raises(DefinitionError, match="reserved") as definition_conflict:
        HarnessBuilder().build(
            AgentSpec(name="conflict"),
            output_type=str,
            model=_model(),
            capabilities=[pydantic_instrumentation],
        )
    assert definition_conflict.value.code == "instrumentation_owner_conflict"

    executable = _build(None)
    with pytest.raises(DefinitionError, match="Agent instrumentation") as run_conflict:
        executable.stream(
            "hello",
            bindings=RunBindings.embedded(capabilities=[pydantic_instrumentation]),
        )
    assert run_conflict.value.code == "instrumentation_owner_conflict"

    wrapped = InstrumentedModel(
        _model(),
        InstrumentationSettings(tracer_provider=tracer_provider),
    )
    with pytest.raises(DefinitionError, match="InstrumentedModel") as model_conflict:
        _build(None, model=wrapped)
    assert model_conflict.value.code == "instrumentation_owner_conflict"


async def test_run_resolved_instrumentation_is_rejected_before_it_emits_spans() -> None:
    tracer_provider, exporter, _, _ = _providers()
    dynamic = _DynamicInstrumentationCapability(
        Instrumentation(settings=InstrumentationSettings(tracer_provider=tracer_provider))
    )
    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(name="dynamic-conflict"),
        output_type=str,
        model=_model(),
        capabilities=[dynamic],
    )

    with pytest.raises(DefinitionError) as conflict:
        await executable.run("hello", bindings=RunBindings.embedded())

    assert conflict.value.code == "instrumentation_owner_conflict"
    assert exporter.get_finished_spans() == ()


async def test_runtime_model_resolver_rejects_instrumented_models() -> None:
    tracer_provider, _, _, _ = _providers()
    wrapped = InstrumentedModel(
        _model(),
        InstrumentationSettings(tracer_provider=tracer_provider),
    )

    async def resolve_model(context: Any, model_id: str):
        del context, model_id
        return wrapped

    executable = HarnessBuilder().build(
        AgentSpec(name="resolver-agent", model="logical-model"),
        output_type=str,
    )
    with pytest.raises(ModelResolutionError, match="InstrumentedModel") as conflict:
        await executable.run(
            "hello",
            bindings=RunBindings.embedded(model_resolver=resolve_model),
        )
    assert conflict.value.code == "instrumentation_owner_conflict"


async def test_stream_construction_is_observation_inert() -> None:
    tracer_provider, exporter, _, _ = _providers()
    executable = _build(HarnessInstrumentation(tracer_provider=tracer_provider))

    stream = executable.stream("hello", bindings=RunBindings.embedded())
    assert exporter.get_finished_spans() == ()

    async with stream:
        # Entry prepares the Run, but does not start model/tool execution.
        assert [span.name for span in exporter.get_finished_spans()] == ["harness.prepare"]

    run_span = next(span for span in exporter.get_finished_spans() if span.name == "harness.run")
    assert run_span.attributes["a13n.run.outcome"] == "cancelled"


async def test_enabled_trace_uses_current_host_parent_and_detaches_at_public_boundaries() -> None:
    tracer_provider, exporter, _, _ = _providers()
    model_span_ids: list[int] = []
    executable = _build(
        HarnessInstrumentation(tracer_provider=tracer_provider),
        model=_model(model_span_ids),
    )
    host_tracer = tracer_provider.get_tracer("test-host")

    with host_tracer.start_as_current_span("host.root") as host_span:
        async with executable.stream("hello", bindings=RunBindings.embedded()) as stream:
            assert trace.get_current_span() is host_span
            async for item in stream:
                assert trace.get_current_span() is host_span
                assert item.run_id == stream.run_id

    spans = exporter.get_finished_spans()
    run_span = next(span for span in spans if span.name == "harness.run")
    attempt_span = next(span for span in spans if span.attributes.get("gen_ai.operation.name") == "invoke_agent")
    request_span = next(span for span in spans if span.attributes.get("gen_ai.operation.name") == "chat")
    assert run_span.parent is not None
    assert run_span.parent.span_id == host_span.get_span_context().span_id
    assert attempt_span.parent is not None and attempt_span.parent.span_id == run_span.context.span_id
    assert request_span.parent is not None and request_span.parent.span_id == attempt_span.context.span_id
    assert model_span_ids == [request_span.context.span_id]
    assert run_span.attributes["a13n.run.outcome"] == "completed"
    assert run_span.status.status_code is StatusCode.UNSET


def test_observation_context_validates_and_freezes_bounded_values() -> None:
    metadata: dict[str, str | bool | int | float] = {"scenario": "summary", "synthetic": True}
    context = HarnessObservationContext(
        name="observation-summary",
        session_id="observation-summary-session",
        labels=("a13n-harness", "profile:summary"),
        metadata=metadata,
    )
    metadata["scenario"] = "changed"

    assert context.metadata == {"scenario": "summary", "synthetic": True}
    with pytest.raises(TypeError):
        context.metadata["scenario"] = "changed"  # type: ignore[index]

    invalid_values = (
        {"labels": "profile:summary"},
        {"labels": {"profile:summary"}},
        {"labels": ("duplicate", "duplicate")},
        {"labels": ("x" * 65,)},
        {"metadata": [("scenario", "summary")]},
        {"metadata": {"Invalid Key": "value"}},
        {"metadata": {"nested": {"value": "not-supported"}}},
        {"metadata": {"not_finite": float("inf")}},
    )
    for value in invalid_values:
        with pytest.raises(DefinitionError) as invalid:
            HarnessObservationContext(**value)  # type: ignore[arg-type]
        assert invalid.value.code == "observation_context_invalid"


async def test_observation_context_enriches_native_descendants_without_a_provider_processor() -> None:
    tracer_provider, exporter, _, _ = _providers()
    executable = _build(
        HarnessInstrumentation(
            tracer_provider=tracer_provider,
        )
    )
    observation = HarnessObservationContext(
        name="observation-summary",
        session_id="observation-summary-session",
        labels=("a13n-harness", "profile:summary"),
        metadata={"scenario": "summary", "synthetic": True, "sequence": 4},
    )

    result = await executable.run(
        "hello",
        bindings=RunBindings.embedded(observation=observation),
    )
    assert result.status == "completed"

    spans = exporter.get_finished_spans()
    run_span = next(span for span in spans if span.name == "harness.run")
    assert run_span.parent is None
    assert run_span.attributes["a13n.observation.name"] == "observation-summary"
    assert run_span.attributes["a13n.observation.session.id"] == "observation-summary-session"
    assert run_span.attributes["a13n.observation.labels"] == ("a13n-harness", "profile:summary")
    assert run_span.attributes["a13n.observation.metadata.scenario"] == "summary"
    assert run_span.attributes["a13n.observation.metadata.synthetic"] is True
    assert run_span.attributes["a13n.observation.metadata.sequence"] == 4
    for span in spans:
        assert span.attributes["langfuse.trace.name"] == "observation-summary"
        assert span.attributes["langfuse.session.id"] == "observation-summary-session"
        assert span.attributes["langfuse.trace.tags"] == observation.labels
        assert span.attributes["langfuse.observation.metadata.scenario"] == "summary"
        assert span.attributes["langfuse.observation.metadata.sequence"] == "4"
        assert span.attributes["langfuse.observation.metadata.thread_id"] == result.state.thread_id
        assert span.attributes["a13n.run.id"] == result.run_id
        assert "langfuse.trace.public" not in span.attributes


async def test_identity_and_lineage_use_only_the_bounded_attribute_registry() -> None:
    tracer_provider, exporter, _, _ = _providers()
    instance = AgentInstanceContext(
        identity=AgentIdentityRef(
            issuer="https://identity.example",
            subject="workload-subject",
            agent_id="research-agent",
            user_id="user-42",
            tenant_id="must-not-be-projected",
        ),
        agent_instance_id="agent-instance-42",
        parent_agent_instance_id="agent-instance-parent",
        delegation_id="delegation-42",
        actor="host.scheduler",
        host_refs={"request_id": "must-not-be-projected"},
    )
    bindings = RunBindings(instance=instance)
    executable = _build(
        HarnessInstrumentation(
            tracer_provider=tracer_provider,
        )
    )

    result = await executable.run("hello", bindings=bindings)
    assert result.status == "completed"

    spans = exporter.get_finished_spans()
    run_span = next(span for span in spans if span.name == "harness.run")
    attempt_span = next(span for span in spans if span.attributes.get("gen_ai.operation.name") == "invoke_agent")
    identity_attributes = {
        "a13n.agent.identity.issuer": "https://identity.example",
        "a13n.agent.identity.subject": "workload-subject",
        "a13n.agent.id": "research-agent",
        "a13n.user.id": "user-42",
        "a13n.agent.instance.id": "agent-instance-42",
        "a13n.agent.parent_instance.id": "agent-instance-parent",
        "a13n.delegation.id": "delegation-42",
        "a13n.actor": "host.scheduler",
    }
    assert {key: run_span.attributes[key] for key in identity_attributes} == identity_attributes
    assert {key: attempt_span.attributes[key] for key in identity_attributes} == identity_attributes
    assert "tenant_id" not in str(run_span.attributes)
    assert "must-not-be-projected" not in str(run_span.attributes)
    assert "tenant_id" not in str(attempt_span.attributes)
    assert "must-not-be-projected" not in str(attempt_span.attributes)


async def test_unsafe_or_oversized_identity_values_are_omitted_without_truncation() -> None:
    tracer_provider, exporter, _, _ = _providers()
    oversized = "identity-value-" + ("x" * 1024)
    bindings = RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(
                issuer="https://identity.example",
                subject="bounded-subject",
                agent_id=oversized,
                user_id="bounded-user",
            ),
            agent_instance_id="bounded-instance",
            parent_agent_instance_id="bounded-parent",
            delegation_id="bounded-delegation",
            actor="\ud800",
        )
    )
    executable = _build(HarnessInstrumentation(tracer_provider=tracer_provider))

    result = await executable.run("hello", bindings=bindings)
    assert result.status == "completed"

    run_span = next(span for span in exporter.get_finished_spans() if span.name == "harness.run")
    assert run_span.attributes["a13n.agent.identity.issuer"] == "https://identity.example"
    assert run_span.attributes["a13n.agent.identity.subject"] == "bounded-subject"
    assert run_span.attributes["a13n.user.id"] == "bounded-user"
    assert run_span.attributes["a13n.agent.instance.id"] == "bounded-instance"
    assert run_span.attributes["a13n.agent.parent_instance.id"] == "bounded-parent"
    assert run_span.attributes["a13n.delegation.id"] == "bounded-delegation"
    assert "a13n.agent.id" not in run_span.attributes
    assert "a13n.actor" not in run_span.attributes
    assert oversized not in str(run_span.attributes)


class _ObservedFixedCostCapability(AbstractModelCostCapability):
    @property
    def revision(self) -> str:
        return "pricing-2026-08"

    def quote(self, value: ModelCostInput) -> ModelCostQuote:
        del value
        return ModelCostQuote(
            cost_usd=Decimal("0.125"),
            source="custom",
            pricing_revision=self.revision,
            rule_id="fixed-observation-test",
        )


class _ObservedUsageModel(TestModel):
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
            stream.usage.input_tokens = 100
            stream.usage.cache_write_tokens = 20
            stream.usage.cache_read_tokens = 30
            stream.usage.output_tokens = 40
            stream.usage.input_audio_tokens = 5
            stream.usage.cache_audio_read_tokens = 6
            stream.usage.output_audio_tokens = 7
            stream.usage.details = {
                "input_tokens": 100,
                "output_tokens": 40,
                "reasoning_tokens": 8,
            }
            yield stream


async def test_model_span_uses_native_usage_and_bounded_custom_cost_enrichment() -> None:
    tracer_provider, exporter, _, _ = _providers()

    executable = HarnessBuilder(
        instrumentation=HarnessInstrumentation(
            tracer_provider=tracer_provider,
        )
    ).build(
        AgentSpec(name="usage-observation-agent"),
        output_type=str,
        model=_ObservedUsageModel(custom_output_text="done", model_name="usage-observation-model"),
        capabilities=(_ObservedFixedCostCapability(),),
    )

    result = await executable.run("hello", bindings=RunBindings.embedded())
    assert result.status == "completed"

    request_span = next(
        span for span in exporter.get_finished_spans() if span.attributes.get("gen_ai.operation.name") == "chat"
    )
    assert request_span.attributes["gen_ai.usage.input_tokens"] == 100
    assert request_span.attributes["gen_ai.usage.output_tokens"] >= 40
    assert request_span.attributes["gen_ai.usage.cache_creation.input_tokens"] == 20
    assert request_span.attributes["gen_ai.usage.cache_read.input_tokens"] == 30
    assert request_span.attributes["gen_ai.usage.details.input_audio_tokens"] == 5
    assert request_span.attributes["gen_ai.usage.details.cache_audio_read_tokens"] == 6
    assert request_span.attributes["gen_ai.usage.details.output_audio_tokens"] == 7
    assert request_span.attributes["gen_ai.usage.details.reasoning_tokens"] == 8
    assert request_span.attributes["gen_ai.usage.cost"] == 0.125
    assert request_span.attributes["a13n.usage.cost.source"] == "custom"
    assert request_span.attributes["a13n.usage.pricing.status"] == "applied"
    assert request_span.attributes["a13n.usage.pricing.revision"] == "pricing-2026-08"
    assert request_span.attributes["a13n.usage.pricing.rule.id"] == "fixed-observation-test"
    assert "gen_ai.usage.details.input_tokens" not in request_span.attributes
    assert "gen_ai.usage.details.output_tokens" not in request_span.attributes
    assert "a13n.usage.input_tokens" not in request_span.attributes
    assert "a13n.usage.output_tokens" not in request_span.attributes
    attempt_span = next(
        span for span in exporter.get_finished_spans() if span.attributes.get("gen_ai.operation.name") == "invoke_agent"
    )
    assert "gen_ai.usage.cost" not in attempt_span.attributes
    assert not any(key.startswith("a13n.usage.") for key in attempt_span.attributes)


async def test_custom_pricing_does_not_leak_without_a_recording_pydantic_model_span() -> None:
    tracer_provider, exporter, meter_provider, _ = _providers()
    host_tracer = tracer_provider.get_tracer("test-host")

    for name, instrumentation in (
        ("disabled", None),
        ("metrics-only", HarnessInstrumentation(meter_provider=meter_provider)),
    ):
        executable = HarnessBuilder(instrumentation=instrumentation).build(
            AgentSpec(name=f"{name}-cost-observation-agent"),
            output_type=str,
            model=_ObservedUsageModel(custom_output_text="done", model_name=f"{name}-cost-model"),
            capabilities=(_ObservedFixedCostCapability(),),
        )
        with host_tracer.start_as_current_span(f"host.{name}"):
            result = await executable.run("hello", bindings=RunBindings.embedded())
        assert result.status == "completed"

    for span in exporter.get_finished_spans():
        assert "gen_ai.usage.cost" not in span.attributes
        assert not any(key.startswith("a13n.usage.") for key in span.attributes)


async def test_enabled_trace_uses_native_pydantic_spans_and_attempt_correlation() -> None:
    tracer_provider, exporter, _, _ = _providers()
    bindings = RunBindings.embedded()
    executable = _build(
        HarnessInstrumentation(
            tracer_provider=tracer_provider,
        )
    )

    result = await executable.run("hello", bindings=bindings)
    assert result.status == "completed"

    spans = exporter.get_finished_spans()
    run_span = next(span for span in spans if span.name == "harness.run")
    attempt_span = next(span for span in spans if span.attributes.get("gen_ai.operation.name") == "invoke_agent")
    request_span = next(span for span in spans if span.attributes.get("gen_ai.operation.name") == "chat")
    assert attempt_span.parent is not None and attempt_span.parent.span_id == run_span.context.span_id
    assert request_span.parent is not None and request_span.parent.span_id == attempt_span.context.span_id
    assert attempt_span.attributes["a13n.model_attempt.index"] == 0
    assert attempt_span.attributes["a13n.agent.instance.id"] == bindings.instance.agent_instance_id
    assert attempt_span.attributes["gen_ai.conversation.id"] == result.thread_id
    assert str(attempt_span.attributes["gen_ai.agent.call.id"]).startswith("model-attempt-")
    assert "hello" in str(attempt_span.attributes)


async def test_verbose_trace_omits_routine_state_and_plugin_operations() -> None:
    tracer_provider, exporter, _, _ = _providers()
    executable = _build(
        HarnessInstrumentation(
            tracer_provider=tracer_provider,
        )
    )

    result = await executable.run("hello", bindings=RunBindings.embedded())
    assert result.status == "completed"

    operations = [span for span in exporter.get_finished_spans() if span.name == "harness.operation"]
    assert operations == []


async def test_metrics_only_records_exact_low_cardinality_harness_registry() -> None:
    tracer_provider, exporter, meter_provider, reader = _providers()
    model_span_ids: list[int] = []
    executable = _build(
        HarnessInstrumentation(meter_provider=meter_provider),
        model=_model(model_span_ids),
    )

    host_tracer = tracer_provider.get_tracer("test-host")
    with host_tracer.start_as_current_span("host.root") as host_span:
        result = await executable.run("hello", bindings=RunBindings.embedded())
    assert result.status == "completed"
    assert model_span_ids == [host_span.get_span_context().span_id]
    assert [span.name for span in exporter.get_finished_spans()] == ["host.root"]

    metrics = _metric_map(reader)
    assert {
        "a13n.harness.run.duration",
        "a13n.harness.run.active",
        "a13n.harness.run.model_attempts",
        "gen_ai.client.token.usage",
        "gen_ai.client.operation.time_to_first_chunk",
    } <= set(metrics)
    assert metrics["a13n.harness.run.duration"].unit == "s"
    assert metrics["a13n.harness.run.active"].unit == "{run}"
    assert metrics["a13n.harness.run.model_attempts"].unit == "{attempt}"
    assert "a13n.harness.operation.duration" not in metrics

    duration_point = metrics["a13n.harness.run.duration"].data.data_points[0]
    attempts_point = metrics["a13n.harness.run.model_attempts"].data.data_points[0]
    active_point = metrics["a13n.harness.run.active"].data.data_points[0]
    assert dict(duration_point.attributes) == {"a13n.run.outcome": "completed"}
    # Seconds get buckets sized for seconds, not the OpenTelemetry defaults sized for milliseconds.
    assert duration_point.explicit_bounds[:3] == (1, 2.5, 5)
    assert dict(attempts_point.attributes) == {"a13n.run.outcome": "completed"}
    assert attempts_point.sum == 1
    assert active_point.value == 0


async def test_recovery_attempts_share_one_run_and_record_attempt_count() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        if calls == 1:
            raise UnexpectedModelBehavior("Streamed response ended without content or tool calls")
        yield "recovered"

    tracer_provider, exporter, meter_provider, reader = _providers()
    executable = HarnessBuilder(
        instrumentation=HarnessInstrumentation(
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
        )
    ).build(
        AgentSpec(name="recovery-agent"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            backoff_initial_seconds=0.001,
            backoff_max_seconds=0.001,
        ),
    )

    result = await executable.run("hello", bindings=RunBindings.embedded())
    assert result.output == "recovered"
    metrics = _metric_map(reader)
    attempts = metrics["a13n.harness.run.model_attempts"].data.data_points[0]
    assert attempts.sum == 2
    recovery_points = [
        point
        for point in metrics["a13n.harness.operation.duration"].data.data_points
        if point.attributes["a13n.operation.kind"] == "recovery"
    ]
    assert recovery_points
    recovery_spans = [
        span
        for span in exporter.get_finished_spans()
        if span.name == "harness.operation" and span.attributes["a13n.operation.kind"] == "recovery"
    ]
    assert {span.attributes["a13n.recovery.step"] for span in recovery_spans} == {"backoff", "build_prompt"}
    for span in recovery_spans:
        assert span.attributes["a13n.recovery.next_attempt"] == 2
        assert span.attributes["a13n.operation.status"] == "completed"
        assert span.attributes["a13n.output.capture"] == "captured"


class _ShortCircuitPlugin(AbstractHarnessPlugin):
    @property
    def plugin_id(self) -> str:
        return "observation-short-circuit"

    async def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> HarnessRunResult:
        del call_next
        return HarnessRunResult(
            thread_id=exchange.context.thread_id,
            run_id=exchange.context.run_id,
            status="completed",
            output="cached",
            state=await exchange.export_current_state(),
            usage=RunUsageSummary(),
        )


async def test_plugin_short_circuit_records_zero_model_attempts() -> None:
    _, _, meter_provider, reader = _providers()
    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(meter_provider=meter_provider)).build(
        AgentSpec(name="short-circuit-agent"),
        output_type=str,
        model=_model(),
        plugins=[_ShortCircuitPlugin()],
    )

    result = await executable.run("hello", bindings=RunBindings.embedded())
    assert result.output == "cached"
    attempts = _metric_map(reader)["a13n.harness.run.model_attempts"].data.data_points[0]
    assert attempts.sum == 0


class _CleanupFailurePlugin(AbstractHarnessPlugin):
    @property
    def plugin_id(self) -> str:
        return "observation-cleanup-failure"

    async def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> HarnessRunResult:
        try:
            return await call_next(exchange)
        finally:
            raise RuntimeError("cleanup failed")


async def test_cleanup_failure_marks_the_logical_run_failed_without_raw_exception_fields() -> None:
    tracer_provider, exporter, _, _ = _providers()
    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(tracer_provider=tracer_provider)).build(
        AgentSpec(name="cleanup-agent"),
        output_type=str,
        model=_model(),
        plugins=[_CleanupFailurePlugin()],
    )

    with pytest.raises(RunCleanupError):
        await executable.run("hello", bindings=RunBindings.embedded())

    run_span = next(span for span in exporter.get_finished_spans() if span.name == "harness.run")
    assert run_span.attributes["a13n.run.outcome"] == "failed"
    assert run_span.attributes["a13n.run.failure.code"] == "run_cleanup_failed"
    assert run_span.status.status_code is StatusCode.ERROR
    assert run_span.events == ()
    assert "cleanup failed" not in str(run_span.attributes)


class _RaisingExporter(SpanExporter):
    def export(self, spans: Any) -> SpanExportResult:
        del spans
        raise RuntimeError("export failed")

    def shutdown(self) -> None:
        pass


async def test_harness_owned_span_export_failure_does_not_replace_the_run_outcome() -> None:
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(_RaisingExporter()))
    executable = _build(HarnessInstrumentation(tracer_provider=tracer_provider))

    result = await executable.run("hello", bindings=RunBindings.embedded())
    assert result.status == "completed"
    assert result.output == "ok"


async def test_external_task_cancellation_is_recorded_after_cleanup() -> None:
    started = asyncio.Event()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        started.set()
        await asyncio.Event().wait()
        yield "unreachable"

    tracer_provider, exporter, meter_provider, reader = _providers()
    executable = _build(
        HarnessInstrumentation(
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
        ),
        model=FunctionModel(stream_function=stream),
    )

    task = asyncio.create_task(executable.run("hello", bindings=RunBindings.embedded()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run_span = next(span for span in exporter.get_finished_spans() if span.name == "harness.run")
    assert run_span.attributes["a13n.run.outcome"] == "cancelled"
    assert run_span.status.status_code is StatusCode.UNSET
    active = _metric_map(reader)["a13n.harness.run.active"].data.data_points[0]
    assert active.value == 0


async def test_cancelled_run_is_not_reported_as_an_otel_error() -> None:
    tracer_provider, exporter, _, _ = _providers()
    executable = _build(HarnessInstrumentation(tracer_provider=tracer_provider))

    async with executable.stream("hello", bindings=RunBindings.embedded()) as stream:
        stream.cancel()
        items = [item async for item in stream]

    terminal = next(item for item in items if isinstance(item, HarnessRunResultEvent))
    assert terminal.result.status == "cancelled"
    run_span = next(span for span in exporter.get_finished_spans() if span.name == "harness.run")
    assert run_span.attributes["a13n.run.outcome"] == "cancelled"
    assert run_span.status.status_code is StatusCode.UNSET


async def test_enriched_tracer_preserves_otel_scope_semantics_and_child_identity() -> None:
    from opentelemetry.context import Context
    from opentelemetry.trace import Link, SpanKind

    provider, exporter, _, _ = _providers()
    tracer = HarnessInstrumentation(tracer_provider=provider).get_tracer("test-host")
    with tracer.start_as_current_span(
        "parent",
        attributes={
            "a13n.thread.id": "thread-parent",
            "a13n.observation.session.id": "session-parent",
            "a13n.observation.labels": ("parent",),
            "a13n.agent.instance.id": "agent-parent",
            "a13n.user.id": "user-parent",
            "a13n.observation.metadata.root_thread_id": "thread-parent",
            "a13n.observation.metadata.subagent_role": "parent-role",
            "langfuse.observation.type": "tool",
            "a13n.run.outcome": "failed",
        },
    ) as parent:
        # An inline child remains nested but has its own Thread, identity and type.
        with tracer.start_as_current_span(
            "harness.run",
            kind=SpanKind.INTERNAL,
            attributes={
                "a13n.thread.id": "thread-child",
                "a13n.run.id": "run-child",
                "a13n.agent.instance.id": "agent-child",
            },
        ) as child:

            @tracer.start_as_current_span("decorated")
            async def execute():
                await asyncio.sleep(0)
                assert trace.get_current_span().get_span_context().is_valid

            await execute()
            with tracer.start_as_current_span(
                "tool",
                attributes={"gen_ai.operation.name": "execute_tool", "langfuse.observation.type": "retriever"},
            ):
                pass
        with tracer.start_as_current_span("detached", context=Context(), links=[Link(parent.get_span_context())]):
            pass
        assert trace.get_current_span() is parent
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["harness.run"].parent.span_id == parent.get_span_context().span_id
    assert spans["harness.run"].attributes["langfuse.observation.type"] == "agent"
    for name in ("harness.run", "decorated", "tool"):
        attrs = spans[name].attributes
        assert attrs["langfuse.session.id"] == "thread-child"
        assert attrs["a13n.agent.instance.id"] == "agent-child"
        assert attrs["langfuse.observation.metadata.root_thread_id"] == "thread-parent"
        assert "langfuse.user.id" not in attrs
        assert "a13n.user.id" not in attrs
        assert "a13n.run.outcome" not in attrs
        assert "a13n.observation.metadata.subagent_role" not in attrs
    assert spans["tool"].attributes["langfuse.observation.type"] == "retriever"
    assert spans["decorated"].parent.span_id == child.get_span_context().span_id
    assert spans["detached"].parent is None
    assert spans["detached"].links[0].context == parent.get_span_context()
    assert not spans["detached"].attributes
    assert not trace.get_current_span().get_span_context().is_valid


async def test_enrichment_is_task_local_and_child_metadata_wins_at_the_bound() -> None:
    provider, exporter, _, _ = _providers()
    tracer = HarnessInstrumentation(tracer_provider=provider).get_tracer("test-host")

    async def branch(index):
        with tracer.start_as_current_span(
            f"parent-{index}",
            attributes={
                "a13n.observation.session.id": f"session-{index}",
                **{f"a13n.observation.metadata.key_{key}": key for key in range(16)},
            },
        ):
            await asyncio.sleep(0)
            with tracer.start_as_current_span(
                f"child-{index}",
                attributes={"a13n.observation.metadata.child": index},
            ):
                await asyncio.sleep(0)

    await asyncio.gather(branch(1), branch(2))
    for span in exporter.get_finished_spans():
        index = int(span.name[-1])
        assert span.attributes["langfuse.session.id"] == f"session-{index}"
        if span.name.startswith("child"):
            assert span.attributes["langfuse.observation.metadata.child"] == str(index)
            assert len([key for key in span.attributes if key.startswith("langfuse.observation.metadata.")]) == 16


async def test_sampled_out_and_metrics_only_do_not_construct_run_enrichment(monkeypatch) -> None:
    from a13n_harness import _trace
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF

    def unexpected(*args):
        pytest.fail("unrecorded run constructed metadata")

    monkeypatch.setattr(_trace, "_aliases", unexpected)
    provider = TracerProvider(sampler=ALWAYS_OFF)
    _, _, meter_provider, _ = _providers()
    for instrumentation in (
        HarnessInstrumentation(tracer_provider=provider),
        HarnessInstrumentation(meter_provider=meter_provider),
        None,
    ):
        assert (await _build(instrumentation).run("fictional")).status == "completed"


async def test_native_tool_spans_receive_enrichment() -> None:
    from pydantic_ai.capabilities import Capability
    from pydantic_ai.toolsets import FunctionToolset

    provider, exporter, _, _ = _providers()
    tools = FunctionToolset()

    @tools.tool_plain
    def answer(value: str) -> str:
        return value

    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(tracer_provider=provider)).build(
        AgentSpec(name="tools-agent"),
        model=TestModel(),
        capabilities=[Capability(toolsets=[tools])],
        output_type=str,
    )
    result = await executable.run("fictional")
    assert result.status == "completed"
    tools_spans = [
        span for span in exporter.get_finished_spans() if span.attributes.get("gen_ai.operation.name") == "execute_tool"
    ]
    assert tools_spans
    for span in tools_spans:
        assert span.attributes["langfuse.observation.type"] == "tool"
        assert span.attributes["langfuse.session.id"] == result.state.thread_id
        assert span.attributes["a13n.run.id"] == result.run_id
        assert "a13n.run.outcome" not in span.attributes


async def test_host_sampler_still_receives_existing_neutral_run_attributes() -> None:
    from opentelemetry.sdk.trace.sampling import Decision, ParentBased, Sampler, SamplingResult

    seen = []

    class NamedSampler(Sampler):
        def should_sample(
            self, parent_context, trace_id, name, kind=None, attributes=None, links=None, trace_state=None
        ):
            seen.append(dict(attributes or {}))
            keep = (attributes or {}).get("a13n.observation.name") == "keep"
            return SamplingResult(Decision.RECORD_AND_SAMPLE if keep else Decision.DROP, attributes=attributes)

        def get_description(self):
            return "named-run"

    provider = TracerProvider(sampler=ParentBased(NamedSampler()))
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    result = await _build(HarnessInstrumentation(tracer_provider=provider)).run(
        "fictional",
        bindings=RunBindings.embedded(observation=HarnessObservationContext(name="keep")),
    )
    assert result.status == "completed"
    assert seen[0]["a13n.observation.name"] == "keep"
    assert "a13n.agent.instance.id" in seen[0]
    assert len(exporter.get_finished_spans()) >= 3


async def test_selective_sampler_does_not_attribute_child_models_to_parent_run() -> None:
    from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult

    class SelectiveSampler(Sampler):
        def should_sample(
            self, parent_context, trace_id, name, kind=None, attributes=None, links=None, trace_state=None
        ):
            drop = name == "harness.run"
            return SamplingResult(Decision.DROP if drop else Decision.RECORD_AND_SAMPLE, attributes=attributes)

        def get_description(self):
            return "drop-logical-run"

    provider = TracerProvider(sampler=SelectiveSampler())
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = HarnessInstrumentation(tracer_provider=provider).get_tracer("test-host")
    with tracer.start_as_current_span(
        "parent",
        attributes={
            "a13n.thread.id": "thread-parent",
            "a13n.run.id": "run-parent",
            "a13n.agent.instance.id": "agent-parent",
            "a13n.user.id": "user-parent",
            "a13n.observation.session.id": "session-parent",
        },
    ):
        with tracer.start_as_current_span(
            "harness.run",
            attributes={
                "a13n.thread.id": "thread-child",
                "a13n.run.id": "run-child",
            },
        ) as child:
            assert not child.is_recording()
            with tracer.start_as_current_span("chat", attributes={"gen_ai.operation.name": "chat"}):
                pass
    model = next(span for span in exporter.get_finished_spans() if span.name == "chat")
    assert model.parent.span_id == child.get_span_context().span_id
    assert model.attributes["langfuse.observation.type"] == "generation"
    assert not any(
        key in model.attributes
        for key in (
            "a13n.thread.id",
            "a13n.run.id",
            "a13n.agent.instance.id",
            "a13n.user.id",
            "langfuse.session.id",
            "langfuse.user.id",
        )
    )


# Root and phase content only distinguish NONE from recorded content; FULL adds only binary and request detail.
@pytest.mark.parametrize("content", [HarnessTraceContent.NONE, HarnessTraceContent.STANDARD])
async def test_run_content_and_phases_follow_policy_and_native_ownership(content):
    import json

    provider, exporter, _, _ = _providers()
    executable = _build(HarnessInstrumentation(tracer_provider=provider, trace_content=content))
    result = await executable.run("only this turn")
    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.name == "harness.run")
    prepare = next(span for span in spans if span.name == "harness.prepare")
    finalize = next(span for span in spans if span.name == "harness.finalize")
    attempt = next(span for span in spans if span.attributes.get("gen_ai.operation.name") == "invoke_agent")
    assert prepare.parent.span_id == finalize.parent.span_id == attempt.parent.span_id == root.context.span_id
    assert root.start_time <= prepare.start_time < prepare.end_time <= attempt.start_time
    assert attempt.end_time <= finalize.start_time < finalize.end_time <= root.end_time
    assert root.attributes["a13n.output.kind"] == result.status
    if content is HarnessTraceContent.NONE:
        assert "a13n.input" not in root.attributes and "a13n.output" not in root.attributes
        assert "langfuse.trace.input" not in root.attributes
    else:
        assert json.loads(root.attributes["a13n.input"]) == "only this turn"
        assert json.loads(root.attributes["a13n.output"]) == result.output
        assert root.attributes["langfuse.observation.output"] == root.attributes["a13n.output"]
    for span in spans:
        if span is not root:
            assert "a13n.input" not in span.attributes
            assert "langfuse.trace.output" not in span.attributes
            if span.name not in {"harness.prepare", "harness.finalize"} or content is HarnessTraceContent.NONE:
                assert "a13n.output" not in span.attributes
    assert prepare.attributes["a13n.phase.status"] == "completed"
    assert finalize.attributes["langfuse.observation.metadata.finalize_cleanup_error_count"] == 0
    if content is not HarnessTraceContent.NONE:
        assert json.loads(prepare.attributes["a13n.output"]) == {
            "environment_bound": True,
            "context_ready": True,
            "plugin_count": 0,
        }
        assert json.loads(finalize.attributes["a13n.output"]) == {
            "state_available": True,
            "cleanup_error_count": 0,
            "state_export_failed": False,
            "cancelled": False,
        }


async def test_input_factory_and_plugin_result_are_root_content_authorities():
    import json

    provider, exporter, _, _ = _providers()
    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(tracer_provider=provider)).build(
        AgentSpec(), output_type=str, model=_model(), plugins=[_ShortCircuitPlugin()]
    )

    async def input_factory(context):
        return "prepared input"

    result = await executable.run(input_factory=input_factory)
    root = next(span for span in exporter.get_finished_spans() if span.name == "harness.run")
    assert result.output == json.loads(root.attributes["a13n.output"]) == "cached"
    assert json.loads(root.attributes["a13n.input"]) == "prepared input"
    assert not any(span.attributes.get("gen_ai.operation.name") for span in exporter.get_finished_spans())


async def test_content_projection_is_bounded_and_never_embeds_media_or_repr(monkeypatch):
    import json

    from pydantic_ai.messages import BinaryContent, ImageUrl

    provider, exporter, _, _ = _providers()
    instrumentation = HarnessInstrumentation(tracer_provider=provider, trace_content=HarnessTraceContent.FULL)
    with instrumentation.get_tracer("test").start_as_current_span("root") as span:
        instrumentation.record_input(
            span,
            [
                "hello",
                BinaryContent(data=b"private binary", media_type="image/png"),
                ImageUrl(url="https://private.invalid/secret"),
            ],
        )
        instrumentation.record_output(span, "界" * 100_000, status="completed")
    captured = exporter.get_finished_spans()[-1].attributes
    assert len(captured["a13n.output"].encode()) <= 8192
    assert captured["a13n.output.truncated"]
    assert captured["a13n.input.attachment_count"] == 2
    assert "private binary" not in captured["a13n.input"]
    assert "private.invalid" not in captured["a13n.input"]
    assert json.loads(captured["a13n.input"])[0] == "hello"

    def unexpected(*args):
        pytest.fail("disabled content must not be projected")

    monkeypatch.setattr("a13n_harness._trace_details._project", unexpected)
    disabled = HarnessInstrumentation(tracer_provider=provider, trace_content=HarnessTraceContent.NONE)
    with disabled.get_tracer("test").start_as_current_span("no-content") as span:
        disabled.record_input(span, object())
        disabled.record_output(span, object(), status="failed")


async def test_skill_summary_is_bounded_and_counts_reads_not_claimed_usage():
    from a13n_harness.observation import SkillObservation

    provider, exporter, _, _ = _providers()
    with provider.get_tracer("test").start_as_current_span("root") as span:
        skills = SkillObservation(span)
        skills.catalog([f"skill-{index}" for index in range(100)])
        for index in range(100):
            skills.access(f"skill-{index}")
        skills.access("skill-0")
    attributes = exporter.get_finished_spans()[-1].attributes
    assert attributes["a13n.skills.available_count"] == 100
    assert attributes["a13n.skills.available_omitted"] == 84
    assert len(attributes["a13n.skills.accessed"]) == 16
    assert attributes["a13n.skills.access_count"] == 101
    assert attributes["a13n.skills.accessed_truncated"]


@pytest.mark.parametrize("cancelled", [False, True])
async def test_phase_failures_preserve_exception_and_do_not_record_exception_content(cancelled):
    provider, exporter, _, _ = _providers()
    instrumentation = HarnessInstrumentation(tracer_provider=provider)
    error = asyncio.CancelledError("private failure") if cancelled else ValueError("private failure")
    with pytest.raises(type(error)) as caught:
        with instrumentation.phase("test", "prepare"):
            raise error
    assert caught.value is error
    span = exporter.get_finished_spans()[-1]
    assert (span.status.status_code is StatusCode.ERROR) == (not cancelled)
    assert "private failure" not in str(span.attributes)
    assert not span.events


@pytest.mark.parametrize("iterate_child", [False, True])
async def test_disabled_nested_run_masks_parent_phases_and_skill_summary(iterate_child):
    from a13n_harness.observation import observe_skill_access, observe_skill_catalog

    provider, exporter, _, _ = _providers()

    async def child_input(context):
        observe_skill_catalog(["child-only"])
        return "child input"

    async def child_model(messages, info):
        observe_skill_access("child-only", source_id="child", tool_id="view")
        yield "child result"

    child = HarnessBuilder(instrumentation=None).build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=child_model)
    )

    async def parent_model(messages, info):
        parent_span = trace.get_current_span()
        observe_skill_catalog(["parent-only"])
        async with child.stream(input_factory=child_input) as run:
            if iterate_child:
                async for _ in run:
                    pass
        assert trace.get_current_span() is parent_span
        observe_skill_access("parent-only", source_id="parent", tool_id="view")
        yield "parent result"

    parent = HarnessBuilder(instrumentation=HarnessInstrumentation(tracer_provider=provider)).build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=parent_model)
    )
    assert (await parent.run("parent input")).output == "parent result"
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans].count("harness.run") == 1
    assert [span.name for span in spans].count("harness.prepare") == 1
    assert [span.name for span in spans].count("harness.finalize") == 1
    root = next(span for span in spans if span.name == "harness.run")
    assert root.attributes["a13n.skills.available"] == ("parent-only",)
    assert root.attributes["a13n.skills.accessed"] == ("parent-only",)
    assert root.attributes["a13n.skills.access_count"] == 1


@pytest.mark.parametrize("failure", [ValueError, asyncio.CancelledError])
def test_operation_failure_keeps_safe_local_status_and_no_synthetic_output(failure):
    from a13n_harness.observation import _compile_observation, observe_operation

    provider, exporter, _, _ = _providers()
    runtime = _compile_observation(HarnessInstrumentation(tracer_provider=provider))
    observation = runtime.start_run(
        thread_id="thread-observed",
        run_id="run-observed",
        instance=RunBindings.embedded().instance,
        observation_context=None,
    )
    activation = observation.activate()
    try:
        with pytest.raises(failure):
            with observe_operation("compaction"):
                raise failure("private provider details")
    finally:
        observation.deactivate(activation)
        observation.finish(outcome="cancelled" if failure is asyncio.CancelledError else "failed")
    span = next(span for span in exporter.get_finished_spans() if span.name == "harness.operation")
    assert span.attributes["a13n.operation.status"] == ("cancelled" if failure is asyncio.CancelledError else "failed")
    assert span.status.status_code is (StatusCode.UNSET if failure is asyncio.CancelledError else StatusCode.ERROR)
    assert "a13n.output" not in span.attributes
    assert "private provider details" not in str(span.attributes)
    assert not span.events
    provider.shutdown()


def test_structural_metadata_is_bounded_local_and_best_effort():
    from a13n_harness.observation import record_span_metadata

    provider, exporter, _, _ = _providers()
    tracer = HarnessInstrumentation(tracer_provider=provider).get_tracer("test")
    with tracer.start_as_current_span("owner") as span:
        record_span_metadata(span, {"phase.label": "界" * 256, "phase.count": 3, "phase.invalid": float("nan")})
        with tracer.start_as_current_span("child"):
            pass
    child, owner = exporter.get_finished_spans()
    assert len(owner.attributes["a13n.phase.label"].encode("utf-8")) <= 256
    assert owner.attributes["langfuse.observation.metadata.phase_count"] == 3
    assert "a13n.phase.invalid" not in owner.attributes
    assert "a13n.phase.count" not in child.attributes
    provider.shutdown()


def test_redact_json_hides_authority_but_keeps_usage_counts():
    value = {
        "Authorization": "Bearer abc",
        "headers": {"x-api-key": "sk-live", "accept": "application/json"},
        "usage": {"input_tokens": 3, "output_tokens": 5},
        "messages": ["use Bearer xyz.123 now", {"password": "hunter2"}],
    }

    assert redact_json(value) == {
        "Authorization": "[REDACTED]",
        "headers": {"x-api-key": "[REDACTED]", "accept": "application/json"},
        "usage": {"input_tokens": 3, "output_tokens": 5},
        "messages": ["use Bearer [REDACTED] now", {"password": "[REDACTED]"}],
    }
    assert value["messages"][1] == {"password": "hunter2"}
