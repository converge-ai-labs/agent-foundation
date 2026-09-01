from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest
from a13n_harness import HarnessBuilder, RunBindings
from a13n_service.observability import (
    FOUNDATION_INSTRUMENTATION_SCOPE,
    RunAttemptCorrelation,
    TraceContent,
    build_observability_runtime,
)
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, SpanContext, StatusCode, TraceFlags
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel


def correlation() -> RunAttemptCorrelation:
    return RunAttemptCorrelation(
        organization_id="org_123",
        workspace_id="ws_123",
        session_id="sess_123",
        thread_id="thread_123",
        run_id="run_123",
        run_attempt_id="attempt_123",
        run_attempt_number=2,
        agent_preset_id="agent_123",
        agent_preset_version_id="agentv_123",
        model_id="model_123",
        model_provider_type="openai",
        replaces_run_attempt_id="attempt_122",
        recovery_reason="lease_expired",
    )


def runtime(exporter: InMemorySpanExporter, *, content: TraceContent = TraceContent.standard):
    return build_observability_runtime(
        enabled=True,
        trace_content=content,
        service_name="foundation-service",
        service_version="1.2.3",
        deployment_environment="test",
        service_role="worker",
        service_instance_id="worker-1",
        span_exporter=exporter,
    )


@pytest.mark.anyio
async def test_run_attempt_is_parentless_and_exports_only_approved_correlated_scopes() -> None:
    exporter = InMemorySpanExporter()
    observation = runtime(exporter)
    provider = observation.tracer_provider
    assert provider is not None
    incoming_tracer = provider.get_tracer("test-incoming")

    with incoming_tracer.start_as_current_span("incoming"):
        with observation.run_attempt(correlation(), input_value={"prompt": "hello"}) as attempt:
            with attempt.phase("foundation.reconstruct"):
                harness_tracer = provider.get_tracer("a13n-harness")
                with harness_tracer.start_as_current_span("harness.run"):
                    provider.get_tracer("unapproved-plugin").start_span("plugin.secret").end()
            attempt.set_outcome("succeeded", output_value={"answer": "world"})

    assert provider.force_flush()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans.keys() == {"foundation.run_attempt", "foundation.reconstruct", "harness.run"}
    root = spans["foundation.run_attempt"]
    phase = spans["foundation.reconstruct"]
    harness = spans["harness.run"]
    assert root.parent is None
    assert phase.parent is not None and phase.parent.span_id == root.context.span_id
    assert harness.parent is not None and harness.parent.span_id == phase.context.span_id
    assert root.attributes is not None
    assert root.attributes["input.value"] == '{"prompt":"hello"}'
    assert root.attributes["input.mime_type"] == "application/json"
    assert root.attributes["output.value"] == '{"answer":"world"}'
    assert root.attributes["a13n.run_attempt.outcome"] == "succeeded"
    for span in spans.values():
        assert span.attributes is not None
        assert span.attributes["a13n.run_attempt.id"] == "attempt_123"
        assert span.attributes["a13n.foundation.run.id"] == "run_123"
        assert span.attributes["session.id"] == "thread_123"
        assert span.resource.attributes["service.name"] == "foundation-service"
        assert span.resource.attributes["service.version"] == "1.2.3"
        assert span.resource.attributes["deployment.environment.name"] == "test"
        assert span.resource.attributes["a13n.service.role"] == "worker"
        assert span.resource.attributes["service.instance.id"] == "worker-1"
    await observation.aclose()


@pytest.mark.anyio
async def test_real_harness_and_pydantic_spans_descend_from_the_attempt_root() -> None:
    async def stream(_messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str]:
        yield "observed output"

    exporter = InMemorySpanExporter()
    observation = runtime(exporter)
    instrumentation = observation.harness_instrumentation
    assert instrumentation is not None
    executable = HarnessBuilder(instrumentation=instrumentation).build(
        AgentSpec(name="observed-agent"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    with observation.run_attempt(correlation(), input_value="observed input") as attempt:
        result = await executable.run("observed input", bindings=RunBindings.embedded())
        assert result.status == "completed"
        attempt.set_outcome("succeeded", output_value=result.output)

    provider = observation.tracer_provider
    assert provider is not None and provider.force_flush()
    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.name == "foundation.run_attempt")
    harness = next(span for span in spans if span.name == "harness.run")
    pydantic_spans = [span for span in spans if span.instrumentation_scope.name == "pydantic-ai"]
    assert harness.parent is not None and harness.parent.span_id == root.context.span_id
    assert pydantic_spans
    assert {span.context.trace_id for span in spans} == {root.context.trace_id}
    for span in spans:
        assert span.attributes is not None
        assert span.attributes["a13n.run_attempt.id"] == "attempt_123"
        assert span.attributes["a13n.workspace.id"] == "ws_123"
    await observation.aclose()


@pytest.mark.anyio
async def test_resource_uses_only_registered_process_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES",
        "secret.password=hunter2,service.version=operator-version,custom=value",
    )
    monkeypatch.setenv("OTEL_SERVICE_NAME", "operator-service")
    exporter = InMemorySpanExporter()
    observation = runtime(exporter)

    with observation.run_attempt(correlation()):
        pass

    assert observation.tracer_provider is not None
    assert observation.tracer_provider.force_flush()
    attributes = exporter.get_finished_spans()[0].resource.attributes
    assert attributes == {
        "service.name": "operator-service",
        "service.version": "operator-version",
        "deployment.environment.name": "test",
        "a13n.service.role": "worker",
        "service.instance.id": "worker-1",
    }
    await observation.aclose()


@pytest.mark.anyio
async def test_none_content_and_failed_outcome_preserve_topology_without_payload() -> None:
    exporter = InMemorySpanExporter()
    observation = runtime(exporter, content=TraceContent.none)
    provider = observation.tracer_provider
    assert provider is not None

    with observation.run_attempt(correlation(), input_value="secret input") as attempt:
        attempt.set_outcome("failed", output_value="must not appear", failure_code="model_unavailable")

    assert provider.force_flush()
    root = exporter.get_finished_spans()[0]
    assert root.name == "foundation.run_attempt"
    assert root.attributes is not None
    assert "input.value" not in root.attributes
    assert "output.value" not in root.attributes
    assert root.attributes["a13n.run_attempt.failure.code"] == "model_unavailable"
    assert root.status.status_code is StatusCode.ERROR
    await observation.aclose()


@pytest.mark.anyio
async def test_nested_run_attempt_roots_are_rejected() -> None:
    exporter = InMemorySpanExporter()
    observation = runtime(exporter)
    provider = observation.tracer_provider
    assert provider is not None

    with observation.run_attempt(correlation()):
        with pytest.raises(RuntimeError, match="already active"):
            with observation.run_attempt(correlation()):
                pytest.fail("nested RunAttempt root unexpectedly started")

    await observation.aclose()


@pytest.mark.anyio
async def test_service_phases_ignore_an_unrelated_current_span_for_parentage() -> None:
    exporter = InMemorySpanExporter()
    observation = runtime(exporter)
    provider = observation.tracer_provider
    assert provider is not None

    with observation.run_attempt(correlation()) as attempt:
        foundation_tracer = provider.get_tracer(FOUNDATION_INSTRUMENTATION_SCOPE)
        with foundation_tracer.start_as_current_span("foundation.unregistered"):
            with attempt.phase("foundation.reconstruct"):
                pass
            with attempt.phase("foundation.persist"):
                pass

    assert provider.force_flush()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans.keys() == {"foundation.run_attempt", "foundation.reconstruct", "foundation.persist"}
    root = spans["foundation.run_attempt"]
    assert spans["foundation.reconstruct"].parent is not None
    assert spans["foundation.reconstruct"].parent.span_id == root.context.span_id
    assert spans["foundation.persist"].parent is not None
    assert spans["foundation.persist"].parent.span_id == root.context.span_id
    await observation.aclose()


@pytest.mark.anyio
async def test_shutdown_failure_is_diagnostic_only(caplog: pytest.LogCaptureFixture) -> None:
    class ThrowingProvider:
        shutdown_called = False

        def force_flush(self, _timeout_millis: int) -> bool:
            raise RuntimeError("private flush failure")

        def shutdown(self) -> None:
            self.shutdown_called = True
            raise RuntimeError("private shutdown failure")

    provider = ThrowingProvider()
    observation = build_observability_runtime(
        enabled=False,
        trace_content=TraceContent.none,
        service_name="foundation-service",
        service_version="1.2.3",
        deployment_environment="test",
        service_role="worker",
    )
    observation.tracer_provider = provider  # type: ignore[assignment]

    await observation.aclose()

    assert provider.shutdown_called
    assert "observability_shutdown_failed" in caplog.messages
    assert "private" not in caplog.text


@pytest.mark.anyio
async def test_exporter_failure_never_escapes_or_discloses_private_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class ThrowingExporter(SpanExporter):
        def export(self, _spans: Sequence[ReadableSpan]) -> SpanExportResult:
            raise RuntimeError("private exporter failure")

        def shutdown(self) -> None:
            pass

    observation = build_observability_runtime(
        enabled=True,
        trace_content=TraceContent.none,
        service_name="foundation-service",
        service_version="1.2.3",
        deployment_environment="test",
        service_role="worker",
        span_exporter=ThrowingExporter(),
    )

    with observation.run_attempt(correlation()) as attempt:
        attempt.set_outcome("succeeded")
    provider = observation.tracer_provider
    assert provider is not None
    provider.force_flush()
    await observation.aclose()

    assert "observability_export_failed" in caplog.messages
    assert "private exporter failure" not in caplog.text


def test_disabled_runtime_passes_disabled_instrumentation_to_harness() -> None:
    observation = build_observability_runtime(
        enabled=False,
        trace_content=TraceContent.full,
        service_name="foundation-service",
        service_version="1.2.3",
        deployment_environment="test",
        service_role="worker",
    )

    assert observation.tracer_provider is None
    assert observation.harness_instrumentation is None
    with observation.run_attempt(correlation(), input_value="not traced") as attempt:
        attempt.set_outcome("succeeded", output_value="not traced")


def test_invalid_sampler_configuration_fails_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "not-a-sampler")

    with pytest.raises(ValueError, match="unsupported head sampler"):
        build_observability_runtime(
            enabled=True,
            trace_content=TraceContent.none,
            service_name="foundation-service",
            service_version="1.2.3",
            deployment_environment="test",
            service_role="worker",
            span_exporter=InMemorySpanExporter(),
        )


@pytest.mark.anyio
async def test_unset_exporter_keeps_structural_tracing_without_network(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    observation = build_observability_runtime(
        enabled=True,
        trace_content=TraceContent.none,
        service_name="foundation-service",
        service_version="1.2.3",
        deployment_environment="test",
        service_role="worker",
    )

    assert observation.tracer_provider is not None
    assert "observability_exporter_unconfigured" in caplog.messages
    await observation.aclose()


def test_multiple_or_unknown_exporters_fail_static_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp,console")

    with pytest.raises(ValueError, match="at most one"):
        build_observability_runtime(
            enabled=True,
            trace_content=TraceContent.none,
            service_name="foundation-service",
            service_version="1.2.3",
            deployment_environment="test",
            service_role="worker",
        )


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("OTEL_RESOURCE_ATTRIBUTES", "valid=value,missing-separator", "invalid key-value pair"),
        ("OTEL_BSP_MAX_QUEUE_SIZE", "many", "positive integer"),
        ("OTEL_BSP_EXPORT_TIMEOUT", "0", "positive integer"),
    ],
)
def test_malformed_active_otel_configuration_fails_startup(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        runtime(InMemorySpanExporter())


def test_export_batch_cannot_exceed_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_BSP_MAX_QUEUE_SIZE", "10")
    monkeypatch.setenv("OTEL_BSP_MAX_EXPORT_BATCH_SIZE", "11")

    with pytest.raises(ValueError, match="must not exceed"):
        runtime(InMemorySpanExporter())


def test_registered_foundation_scope_name_is_stable() -> None:
    assert FOUNDATION_INSTRUMENTATION_SCOPE == "a13n-foundation-service"


@pytest.mark.anyio
async def test_run_attempt_rejects_unregistered_link_attributes() -> None:
    exporter = InMemorySpanExporter()
    observation = runtime(exporter)
    context = SpanContext(
        trace_id=1,
        span_id=1,
        is_remote=True,
        trace_flags=TraceFlags(1),
    )

    with pytest.raises(ValueError, match="link is invalid"):
        with observation.run_attempt(
            correlation(),
            links=(Link(context, {"a13n.link.kind": "dispatch", "secret": "value"}),),
        ):
            pytest.fail("invalid link unexpectedly admitted")
    await observation.aclose()
