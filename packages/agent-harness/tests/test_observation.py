from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from a13n_harness import (
    AbstractHarnessPlugin,
    DefinitionError,
    HarnessBuilder,
    HarnessInstrumentation,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessTraceContent,
    HarnessTraceLevel,
    ModelRecoveryPolicy,
    ModelResolutionError,
    PluginRunExchange,
    PluginRunNext,
    PluginRunResponse,
    RunBindings,
    RunCleanupError,
)
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, Instrumentation
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.instrumented import InstrumentationSettings, InstrumentedModel
from pydantic_ai.usage import RunUsage

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
    with pytest.raises(DefinitionError, match="Non-none") as content:
        HarnessInstrumentation(
            meter_provider=meter_provider,
            trace_content=HarnessTraceContent.STANDARD,
        )
    assert content.value.code == "instrumentation_policy_invalid"

    with pytest.raises(DefinitionError, match="HarnessInstrumentation") as invalid:
        HarnessBuilder(instrumentation=object())  # type: ignore[arg-type]
    assert invalid.value.code == "instrumentation_invalid"

    monkeypatch.setenv("A13N_HARNESS_TRACE_LEVEL", "invalid")
    monkeypatch.setenv("A13N_HARNESS_TRACE_CONTENT", "invalid")
    monkeypatch.setenv("A13N_HARNESS_METRICS", "invalid")
    assert _build(None) is not None
    with pytest.raises(DefinitionError) as environment_invalid:
        HarnessBuilder()
    assert environment_invalid.value.code == "instrumentation_environment_invalid"
    with pytest.raises(DefinitionError) as selection_invalid:
        HarnessBuilder(instrumentation="invalid")  # type: ignore[arg-type]
    assert selection_invalid.value.code == "instrumentation_invalid"


def test_builder_environment_mode_uses_otel_global_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracer_provider, _, meter_provider, _ = _providers()
    monkeypatch.setenv("A13N_HARNESS_TRACE_LEVEL", "verbose")
    monkeypatch.setenv("A13N_HARNESS_TRACE_CONTENT", "none")
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
    assert configuration.trace_level is HarnessTraceLevel.VERBOSE
    assert configuration.trace_content is HarnessTraceContent.NONE


def test_signal_matrix_uses_explicit_noop_providers_and_suppresses_ambient_instrumentation() -> None:
    tracer_provider, _, meter_provider, _ = _providers()

    disabled = _build(None)
    summary = _build(HarnessInstrumentation(tracer_provider=tracer_provider))
    metrics_only = _build(HarnessInstrumentation(meter_provider=meter_provider))
    summary_with_metrics = _build(
        HarnessInstrumentation(tracer_provider=tracer_provider, meter_provider=meter_provider)
    )
    standard_trace_only = _build(
        HarnessInstrumentation(
            tracer_provider=tracer_provider,
            trace_level=HarnessTraceLevel.STANDARD,
        )
    )

    assert disabled._agent.instrument is False
    assert summary._agent.instrument is False
    assert _instrumentation_capabilities(disabled) == []
    assert _instrumentation_capabilities(summary) == []

    metrics_capability = _instrumentation_capabilities(metrics_only)
    summary_metrics_capability = _instrumentation_capabilities(summary_with_metrics)
    standard_capability = _instrumentation_capabilities(standard_trace_only)
    assert len(metrics_capability) == len(summary_metrics_capability) == len(standard_capability) == 1
    assert type(metrics_capability[0].settings.tracer).__name__ == "NoOpTracer"
    assert type(summary_metrics_capability[0].settings.tracer).__name__ == "NoOpTracer"
    assert type(standard_capability[0].settings.meter).__name__ == "NoOpMeter"


def test_trace_content_maps_to_pydantic_instrumentation_settings() -> None:
    tracer_provider, _, _, _ = _providers()
    standard = _build(
        HarnessInstrumentation(
            tracer_provider=tracer_provider,
            trace_level=HarnessTraceLevel.STANDARD,
            trace_content=HarnessTraceContent.STANDARD,
        )
    )
    full = _build(
        HarnessInstrumentation(
            tracer_provider=tracer_provider,
            trace_level=HarnessTraceLevel.VERBOSE,
            trace_content=HarnessTraceContent.FULL,
        )
    )

    standard_settings = _instrumentation_capabilities(standard)[0].settings
    full_settings = _instrumentation_capabilities(full)[0].settings
    assert standard_settings.version == full_settings.version == 5
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
    with pytest.raises(DefinitionError, match="Instrumentation") as run_conflict:
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
        assert exporter.get_finished_spans() == ()

    run_span = next(span for span in exporter.get_finished_spans() if span.name == "harness.run")
    assert run_span.attributes["a13n.run.outcome"] == "cancelled"


async def test_summary_trace_uses_current_host_parent_and_detaches_at_public_boundaries() -> None:
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
    assert {span.name for span in spans} == {"host.root", "harness.run"}
    run_span = next(span for span in spans if span.name == "harness.run")
    assert run_span.parent is not None
    assert run_span.parent.span_id == host_span.get_span_context().span_id
    assert model_span_ids == [run_span.context.span_id]
    assert run_span.attributes["a13n.run.outcome"] == "completed"
    assert run_span.status.status_code is StatusCode.UNSET


async def test_standard_trace_uses_native_pydantic_spans_and_attempt_correlation() -> None:
    tracer_provider, exporter, _, _ = _providers()
    bindings = RunBindings.embedded()
    executable = _build(
        HarnessInstrumentation(
            tracer_provider=tracer_provider,
            trace_level=HarnessTraceLevel.STANDARD,
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
    assert "hello" not in str(attempt_span.attributes)


async def test_verbose_trace_adds_bounded_harness_operations() -> None:
    tracer_provider, exporter, _, _ = _providers()
    executable = _build(
        HarnessInstrumentation(
            tracer_provider=tracer_provider,
            trace_level=HarnessTraceLevel.VERBOSE,
        )
    )

    result = await executable.run("hello", bindings=RunBindings.embedded())
    assert result.status == "completed"

    operations = [span for span in exporter.get_finished_spans() if span.name == "harness.operation"]
    kinds = {span.attributes["a13n.operation.kind"] for span in operations}
    assert {"context", "state", "plugin_validation"} <= kinds
    assert all(
        set(span.attributes) <= {"a13n.operation.kind", "a13n.capability.id", "a13n.operation.id"}
        for span in operations
    )


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
        "a13n.harness.operation.duration",
        "gen_ai.client.token.usage",
        "gen_ai.client.operation.time_to_first_chunk",
    } <= set(metrics)
    assert metrics["a13n.harness.run.duration"].unit == "s"
    assert metrics["a13n.harness.run.active"].unit == "{run}"
    assert metrics["a13n.harness.run.model_attempts"].unit == "{attempt}"
    assert metrics["a13n.harness.operation.duration"].unit == "s"

    duration_point = metrics["a13n.harness.run.duration"].data.data_points[0]
    attempts_point = metrics["a13n.harness.run.model_attempts"].data.data_points[0]
    active_point = metrics["a13n.harness.run.active"].data.data_points[0]
    assert dict(duration_point.attributes) == {"a13n.run.outcome": "completed"}
    assert dict(attempts_point.attributes) == {"a13n.run.outcome": "completed"}
    assert attempts_point.sum == 1
    assert active_point.value == 0
    operation_points = metrics["a13n.harness.operation.duration"].data.data_points
    assert all(set(point.attributes) == {"a13n.operation.kind"} for point in operation_points)


async def test_recovery_attempts_share_one_run_and_record_attempt_count() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        if calls == 1:
            raise UnexpectedModelBehavior("interrupted")
        yield "recovered"

    _, _, meter_provider, reader = _providers()
    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(meter_provider=meter_provider)).build(
        AgentSpec(name="recovery-agent"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
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


class _ShortCircuitPlugin(AbstractHarnessPlugin):
    @property
    def plugin_id(self) -> str:
        return "observation-short-circuit"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        del call_next

        async def iterate():
            yield HarnessRunResult(
                thread_id=exchange.context.thread_id,
                run_id=exchange.context.run_id,
                status="completed",
                output="cached",
                state=await exchange.export_current_state(),
                usage=RunUsage(),
            )

        return PluginRunResponse(iterate())


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

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            try:
                async for item in call_next(exchange):
                    yield item
            finally:
                raise RuntimeError("cleanup failed")

        return PluginRunResponse(iterate())


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
