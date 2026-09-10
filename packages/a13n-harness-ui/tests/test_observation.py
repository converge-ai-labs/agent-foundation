from __future__ import annotations

import os
from pathlib import Path

import pytest
from a13n_harness import HarnessInstrumentation, HarnessTraceContent
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.observation import HarnessUiSpanProcessor, UiObservation, open_observation
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.surfaces import RootOperationStatus
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import ProxyTracerProvider, StatusCode
from pydantic_ai.models.function import FunctionModel

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def isolated_observation_environment(monkeypatch):
    for key in os.environ:
        if key.startswith(("OTEL_", "A13N_HARNESS_TRACE", "A13N_HARNESS_METRICS")):
            monkeypatch.delenv(key)


@pytest.fixture
def telemetry():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(HarnessUiSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield provider, exporter
    provider.shutdown()


def configuration(root: Path) -> Path:
    (root / "models").mkdir()
    (root / "agents").mkdir()
    path = root / "a13n-harness-ui.yaml"
    path.write_text('schema_version: "1"\ndefaults: {agent: agent-test}\nsubagents: {include: []}\n')
    (root / "models/test.yaml").write_text(
        'schema_version: "1"\nkind: model\nid: model-test\nname: Test\nroute: openai:gpt-5\n'
        "authentication: {kind: api_key, env: TEST_MODEL_KEY}\n"
    )
    (root / "agents/test.yaml").write_text(
        'schema_version: "1"\nkind: agent\nid: agent-test\nname: Test\nmodel: model-test\n'
    )
    return path


async def install_model(monkeypatch):
    async def build(self, recipe, authentication):
        async def stream(messages, info):
            yield "observation test completed"

        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "_api_key_model", build)


async def test_app_trace_covers_real_harness_and_saved_continuation(tmp_path, monkeypatch, telemetry):
    provider, exporter = telemetry
    await install_model(monkeypatch)
    path = configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    original_global = trace.get_tracer_provider()
    instrumentation = HarnessInstrumentation(tracer_provider=provider, trace_content=HarnessTraceContent.NONE)
    async with open_harness_ui_app(settings, configuration_path=path, instrumentation=instrumentation) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="private test prompt")
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.completed

    spans = exporter.get_finished_spans()
    host = next(span for span in spans if span.name == "harness_ui.root")
    harness = next(span for span in spans if span.name == "harness.run")
    assert harness.parent.span_id == host.context.span_id
    assert len({span.context.trace_id for span in spans}) == 1
    assert host.start_time <= harness.start_time < harness.end_time <= host.end_time
    assert host.attributes["a13n.ui.operation.status"] == "completed"
    assert host.attributes["a13n.ui.operation.id"] == receipt.receipt_id
    assert host.attributes["a13n.run.id"] == result.run_id
    assert any(span.attributes.get("gen_ai.operation.name") == "chat" for span in spans)
    assert all(span.attributes["langfuse.session.id"] == thread.thread_id for span in spans)
    assert "private test prompt" not in str(host.attributes)
    assert trace.get_tracer_provider() is original_global
    with provider.get_tracer("embedding").start_as_current_span("after-app"):
        pass
    assert exporter.get_finished_spans()[-1].name == "after-app"


async def test_automatic_provider_is_app_local_and_flushes_on_close(tmp_path, monkeypatch):
    exporter = InMemorySpanExporter()
    proxy = ProxyTracerProvider()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: proxy)
    monkeypatch.setenv("A13N_HARNESS_TRACE_LEVEL", "verbose")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "ui-test")
    monkeypatch.setattr("a13n_harness_ui.observation.OTLPSpanExporter", lambda: exporter)
    async with open_observation() as observation:
        assert observation.instrumentation is not None
        provider = observation.instrumentation.tracer_provider
        assert isinstance(provider, TracerProvider)
        assert provider.resource.attributes["service.name"] == "ui-test"
        assert trace.get_tracer_provider() is proxy
        with observation.operation("root", thread_id="thread-test", operation_id="receipt-test"):
            pass
    assert [span.name for span in exporter.get_finished_spans()] == ["harness_ui.root"]


async def test_explicit_none_is_inert_even_when_environment_enables_traces(monkeypatch, telemetry):
    provider, exporter = telemetry
    monkeypatch.setenv("A13N_HARNESS_TRACE_LEVEL", "verbose")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    async with open_observation(None) as observation:
        assert observation.instrumentation is None
        with observation.operation("root", thread_id="thread-test", operation_id="receipt-test"):
            pass
    assert not exporter.get_finished_spans()


async def test_environment_reuses_external_provider_without_adding_exporters(monkeypatch, telemetry):
    provider, exporter = telemetry
    monkeypatch.setenv("A13N_HARNESS_TRACE_LEVEL", "verbose")
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    async with open_observation() as observation:
        assert observation.instrumentation.tracer_provider is provider
    with provider.get_tracer("external").start_as_current_span("still-open"):
        pass
    assert len(exporter.get_finished_spans()) == 1


async def test_linked_child_starts_new_trace_and_retains_dispatch_link(telemetry):
    provider, exporter = telemetry
    observation = UiObservation(HarnessInstrumentation(tracer_provider=provider))
    with observation.operation("root", thread_id="thread-root", operation_id="receipt-root") as root:
        with observation.operation(
            "subagent", thread_id="thread-child", operation_id="execution-child", linked=True
        ) as child:
            with provider.get_tracer("a13n-harness").start_as_current_span("harness.run"):
                pass
    spans = exporter.get_finished_spans()
    child_span = next(span for span in spans if span.name == "harness_ui.subagent")
    assert child_span.parent is None
    assert child_span.context.trace_id != root.get_span_context().trace_id
    assert child_span.links[0].context == root.get_span_context()
    harness = next(span for span in spans if span.name == "harness.run")
    assert harness.parent.span_id == child.get_span_context().span_id
    assert harness.attributes["langfuse.session.id"] == "thread-child"


async def test_preparation_failure_is_visible_without_a_harness_run(tmp_path, monkeypatch, telemetry):
    provider, exporter = telemetry
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    from a13n_harness_ui.errors import CompositionError
    from a13n_harness_ui.root_execution import RootRunExecutor

    async def fail_preparation(self):
        raise CompositionError("not captured", code="test_preparation_failed")

    monkeypatch.setattr(RootRunExecutor, "_required_configuration", fail_preparation)
    async with open_harness_ui_app(
        settings,
        configuration_path=configuration(tmp_path),
        instrumentation=HarnessInstrumentation(tracer_provider=provider),
    ) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="not captured")
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.failed
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "harness_ui.root"
    assert spans[0].status.status_code is StatusCode.ERROR
    assert spans[0].attributes["a13n.ui.operation.status"] == "failed"
    assert "not captured" not in str(spans[0].attributes)


@pytest.mark.parametrize("selection", [None, "off"])
async def test_default_and_off_do_not_construct_an_exporter(monkeypatch, selection):
    if selection is not None:
        monkeypatch.setenv("A13N_HARNESS_TRACE_LEVEL", selection)
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")

    def unexpected_exporter():
        pytest.fail("disabled tracing constructed an exporter")

    monkeypatch.setattr("a13n_harness_ui.observation.OTLPSpanExporter", unexpected_exporter)
    async with open_observation() as observation:
        assert observation.instrumentation is None


async def test_disabled_child_observation_preserves_unrelated_host_context(telemetry):
    provider, exporter = telemetry
    with provider.get_tracer("external").start_as_current_span("dispatch") as parent:
        with UiObservation(None).operation(
            "subagent", thread_id="thread-child", operation_id="execution-child", linked=True
        ):
            assert trace.get_current_span() is parent
            with provider.get_tracer("external").start_as_current_span("external-tool"):
                pass
    tool = next(span for span in exporter.get_finished_spans() if span.name == "external-tool")
    assert tool.parent.span_id == parent.get_span_context().span_id


@pytest.mark.parametrize(
    ("result_status", "event_code", "expected_status"),
    [("cancelled", "run_cancelled", "cancelled"), ("completed", "checkpoint_failed", "failed")],
)
async def test_child_host_status_preserves_cancellation_and_reports_persistence_failure(
    monkeypatch, telemetry, result_status, event_code, expected_status
):
    from types import SimpleNamespace

    from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator
    from ag_ui.core.events import RunErrorEvent
    from anyio import Event, Lock

    provider, exporter = telemetry
    head = SimpleNamespace(execution_id="execution-test")
    prepared = SimpleNamespace(head=head)
    active = SimpleNamespace(done=Event())
    events = (RunErrorEvent(message="not exported", code=event_code),)

    async def consume(*args):
        return SimpleNamespace(status=result_status, run_id="run-test"), None, events

    async def finish(*args):
        return events

    async def publish(*args):
        pass

    operator = SimpleNamespace(
        _consume_run=consume,
        _finish_result=finish,
        _publish_summary=publish,
        _publish_live=publish,
        _lock=Lock(),
        _active={head.execution_id: active},
        _signal_change_locked=lambda: None,
    )
    observation = UiObservation(HarnessInstrumentation(tracer_provider=provider))
    with observation.operation("subagent", thread_id="thread-test", operation_id=head.execution_id) as span:
        await HarnessUiSubagentOperator._execute_segment(operator, prepared, active, span)
    observed = exporter.get_finished_spans()[-1]
    assert observed.attributes["a13n.ui.operation.status"] == expected_status
    assert (observed.status.status_code is StatusCode.ERROR) == (expected_status == "failed")
    assert active.done.is_set()


async def test_full_app_automatic_otlp_path_exports_without_global_registration(tmp_path, monkeypatch):
    exporter = InMemorySpanExporter()
    proxy = ProxyTracerProvider()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: proxy)
    monkeypatch.setenv("A13N_HARNESS_TRACE_LEVEL", "verbose")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setattr("a13n_harness_ui.observation.OTLPSpanExporter", lambda: exporter)
    await install_model(monkeypatch)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=configuration(tmp_path)) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="fictional test")
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.completed
    spans = exporter.get_finished_spans()
    assert {"harness_ui.root", "harness.run"} <= {span.name for span in spans}
    assert all(span.resource.attributes["service.name"] == "a13n-harness-ui" for span in spans)
    assert len({span.context.trace_id for span in spans}) == 1
    assert trace.get_tracer_provider() is proxy


async def test_exporter_shutdown_failure_does_not_replace_application_failure(monkeypatch, caplog):
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(trace, "get_tracer_provider", ProxyTracerProvider)
    monkeypatch.setenv("A13N_HARNESS_TRACE_LEVEL", "verbose")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setattr("a13n_harness_ui.observation.OTLPSpanExporter", lambda: exporter)

    def fail_shutdown():
        raise RuntimeError("private exporter error")

    monkeypatch.setattr(exporter, "shutdown", fail_shutdown)
    with pytest.raises(ValueError, match="application error"):
        async with open_observation():
            raise ValueError("application error")
    assert "trace shutdown failed" in caplog.text
    assert "private exporter error" not in caplog.text
