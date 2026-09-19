from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from a13n_harness import HarnessInstrumentation, HarnessTraceContent
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.observation import UiObservation, open_observation
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
    captured = json.loads(host.attributes["a13n.ui.configuration"])
    assert captured["agent"]["id"] == "agent-test"
    assert captured["model"]["id"] == "model-test"
    assert captured["model"]["route"] == "openai:gpt-5"
    assert captured["model"]["authentication_kind"] == "api_key"
    assert captured["capabilities"]["count"] > 0
    assert "TEST_MODEL_KEY" not in host.attributes["a13n.ui.configuration"]
    for descendant in spans:
        if descendant is not host:
            assert "a13n.ui.configuration" not in descendant.attributes
            assert "langfuse.observation.metadata.configuration" not in descendant.attributes
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
            with observation.instrumentation.get_tracer("a13n-harness").start_as_current_span(
                "harness.run", attributes={"a13n.thread.id": "thread-child"}
            ):
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
    from a13n_harness_ui.environment_runtime import EnvironmentRunService
    from a13n_harness_ui.errors import CompositionError

    async def fail_preparation(self, composition):
        raise CompositionError("not captured", code="test_preparation_failed")

    monkeypatch.setattr(EnvironmentRunService, "prepare", fail_preparation)
    async with open_harness_ui_app(
        settings,
        configuration_path=configuration(tmp_path),
        instrumentation=HarnessInstrumentation(tracer_provider=provider, trace_content=HarnessTraceContent.NONE),
    ) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="not captured")
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.failed
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["harness_ui.prepare", "harness_ui.root"]
    preparation, root = spans
    assert preparation.parent.span_id == root.context.span_id
    assert preparation.attributes["a13n.phase.step"] == "environment"
    assert preparation.status.status_code is StatusCode.ERROR
    assert root.status.status_code is StatusCode.ERROR
    assert root.attributes["a13n.ui.operation.status"] == "failed"
    assert "not captured" not in str(root.attributes)


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
        return SimpleNamespace(status=result_status, run_id="run-test", output=None), None, events

    async def finish(*args):
        return events

    async def publish(*args):
        pass

    async def publish_cleanup(execution_id):
        assert execution_id == head.execution_id
        assert execution_id not in operator._active
        assert active.done.is_set()

    async def finish_restart(*args):
        return False

    operator = SimpleNamespace(
        _restart=None,
        _finish_restart=finish_restart,
        _consume_run=consume,
        _finish_result=finish,
        _publish_summary=publish,
        _publish_summary_by_execution=publish_cleanup,
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


async def test_child_resume_lineage_reaches_native_spans_without_merging_sessions(telemetry):
    from a13n_harness import HarnessBuilder
    from a13n_harness_ui.observation import finish_operation
    from pydantic_ai.agent.spec import AgentSpec
    from pydantic_ai.models.test import TestModel

    provider, exporter = telemetry
    instrumentation = HarnessInstrumentation(tracer_provider=provider, trace_content=HarnessTraceContent.NONE)
    observation = UiObservation(instrumentation)
    executable = HarnessBuilder(instrumentation=instrumentation).build(
        AgentSpec(name="reviewer"), model=TestModel(), output_type=str
    )
    state = None
    for index in range(2):
        with observation.operation("root", thread_id="thread-root", operation_id=f"receipt-{index}") as parent:
            with observation.operation(
                "subagent",
                thread_id=state.thread_id if state is not None else "thread_child",
                operation_id=f"execution-{index}",
                linked=True,
                root_thread_id="thread-root",
                parent_thread_id="thread-root",
                subagent_role="reviewer",
                segment_index=index,
                resumed_from_execution_id="execution-0" if index else None,
            ) as child:
                # Use an actual stable Harness Thread for both execution segments.
                from a13n_harness import HarnessState

                if state is None:
                    state = HarnessState.new(thread_id="thread_child")
                result = await executable.run("fictional review", previous_state=state)
                state = result.state
                finish_operation(child, status="succeeded", run_id=result.run_id)
            assert child.get_span_context().trace_id != parent.get_span_context().trace_id
    child_spans = [span for span in exporter.get_finished_spans() if span.name == "harness_ui.subagent"]
    assert len(child_spans) == 2
    assert child_spans[0].context.trace_id != child_spans[1].context.trace_id
    for index, root in enumerate(child_spans):
        assert root.parent is None
        assert len(root.links) == 1
        descendants = [span for span in exporter.get_finished_spans() if span.context.trace_id == root.context.trace_id]
        assert len(descendants) >= 4
        for span in descendants:
            attrs = span.attributes
            assert attrs["langfuse.session.id"] == "thread_child"
            assert attrs["langfuse.trace.tags"] == ("harness-ui", "subagent")
            assert attrs["langfuse.observation.metadata.root_thread_id"] == "thread-root"
            assert attrs["langfuse.observation.metadata.parent_thread_id"] == "thread-root"
            assert attrs["langfuse.observation.metadata.subagent_role"] == "reviewer"
            assert attrs["langfuse.observation.metadata.execution_id"] == f"execution-{index}"
            assert attrs["langfuse.observation.metadata.segment_index"] == str(index)
            if index:
                assert attrs["langfuse.observation.metadata.resumed_from_execution_id"] == "execution-0"
            else:
                assert "langfuse.observation.metadata.resumed_from_execution_id" not in attrs
            if span is not root:
                assert "a13n.ui.operation.status" not in attrs


async def test_external_task_cancellation_is_not_a_ui_operation_error(telemetry):
    from anyio import get_cancelled_exc_class

    provider, exporter = telemetry
    observation = UiObservation(HarnessInstrumentation(tracer_provider=provider))
    with pytest.raises(get_cancelled_exc_class()):
        with observation.operation("root", thread_id="thread-test", operation_id="receipt-test"):
            raise get_cancelled_exc_class()()
    span = exporter.get_finished_spans()[0]
    assert span.status.status_code is StatusCode.UNSET
    assert span.attributes["a13n.ui.operation.status"] == "cancelled"
    assert "error.type" not in span.attributes


def captured_configuration():
    from a13n_harness_ui.composition import ResolvedRunComposition

    return ResolvedRunComposition.model_validate(
        {
            "package_prompt_revision": "test-prompt-revision",
            "generation_digest": "a" * 64,
            "thread_id": "thread-test",
            "thread_configuration_version": 2,
            "root": {
                "source_kind": "agent",
                "source_id": "agent-test",
                "roster_name": "test",
                "instructions": ["PRIVATE_INSTRUCTIONS"],
                "global_guidance": ["PRIVATE_GUIDANCE"],
                "model": {
                    "model_id": "model-test",
                    "route": "openai:gpt-5",
                    "authentication": {"kind": "api_key", "env": "PRIVATE_CREDENTIAL_REFERENCE"},
                    "settings": {
                        "max_tokens": 1000,
                        "temperature": 0.2,
                        "parallel_tool_calls": False,
                        "openai_reasoning_effort": "high",
                        "extra_headers": {"Authorization": "PRIVATE_HEADER"},
                        "extra_body": {"secret": "PRIVATE_BODY"},
                        "stop_sequences": ["PRIVATE_STOP"],
                    },
                    "model_configuration": {"base_url": "https://PRIVATE_ENDPOINT", "api_key": "PRIVATE_KEY"},
                },
                "harness_plugins": [
                    {"plugin_id": "plugin-test", "plugin_key": "test", "configuration": {"secret": "PRIVATE_PLUGIN"}}
                ],
            },
            "environment_profile": {
                "profile_id": "environment-test",
                "behavior_digest": "b" * 64,
                "provider_key": "Native",
                "provider_schema_version": "1",
                "adapter_key": "DirectLocal",
                "provider_configuration": {"root": "/PRIVATE_ROOT"},
                "adapter_configuration": {"token": "PRIVATE_ADAPTER"},
            },
        }
    )


async def test_configuration_snapshot_is_allowlisted_bounded_and_operation_local(telemetry):
    from a13n_harness_ui.observation import record_configuration

    provider, exporter = telemetry
    instrumentation = HarnessInstrumentation(tracer_provider=provider, trace_content=HarnessTraceContent.NONE)
    observation = UiObservation(instrumentation)
    composition = captured_configuration()
    with observation.operation("root", thread_id="thread-test", operation_id="receipt-test"):
        record_configuration(composition, [f"capability-{index}" for index in range(128)])
        # Disabling an inner operation cannot overwrite the recorded outer snapshot.
        with UiObservation(None).operation("root", thread_id="thread-disabled", operation_id="receipt-disabled"):
            record_configuration(composition, [])
        with instrumentation.get_tracer("test").start_as_current_span(
            "model", attributes={"gen_ai.operation.name": "chat"}
        ):
            pass
        child_model = composition.root.model.model_copy(
            update={"model_id": "model-child", "settings": {"temperature": 0.7}}
        )
        child = composition.model_copy(update={"root": composition.root.model_copy(update={"model": child_model})})
        with observation.operation("subagent", thread_id="thread-child", operation_id="execution-child", linked=True):
            record_configuration(child, ["child-capability"])
    roots = {span.name: span for span in exporter.get_finished_spans()}
    root_json = roots["harness_ui.root"].attributes["a13n.ui.configuration"]
    assert "PRIVATE_" not in root_json
    assert len(root_json.encode()) <= 8192
    summary = json.loads(root_json)
    assert summary["thread_configuration_version"] == 2
    assert summary["model"]["settings"] == {
        "max_tokens": 1000,
        "temperature": 0.2,
        "parallel_tool_calls": False,
        "openai_reasoning_effort": "high",
    }
    assert summary["capabilities"]["count"] == 128
    assert summary["capabilities"]["omitted"] == 112
    assert summary["environment"] == {"profile": "environment-test", "provider": "Native", "adapter": "DirectLocal"}
    assert json.loads(roots["harness_ui.subagent"].attributes["a13n.ui.configuration"])["model"]["id"] == "model-child"
    assert "a13n.ui.configuration" not in roots["model"].attributes
    assert "langfuse.observation.metadata.configuration" not in roots["model"].attributes


async def test_disabled_and_unrecorded_operations_do_not_compute_configuration(monkeypatch):
    from a13n_harness_ui import observation as module
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF

    def unexpected(*args):
        pytest.fail("disabled operation computed a configuration snapshot")

    monkeypatch.setattr(module, "_configuration_summary", unexpected)
    for instrumentation in (None, HarnessInstrumentation(tracer_provider=TracerProvider(sampler=ALWAYS_OFF))):
        with UiObservation(instrumentation).operation("root", thread_id="thread-test", operation_id="receipt-test"):
            module.record_configuration(captured_configuration(), [])


@pytest.mark.parametrize("content", list(HarnessTraceContent))
async def test_root_io_and_host_phases_are_local_to_operation(tmp_path, monkeypatch, telemetry, content):
    provider, exporter = telemetry
    await install_model(monkeypatch)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(
        settings,
        configuration_path=configuration(tmp_path),
        instrumentation=HarnessInstrumentation(tracer_provider=provider, trace_content=content),
    ) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="this turn input")
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.completed
    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.name == "harness_ui.root")
    prepare = next(span for span in spans if span.name == "harness_ui.prepare")
    harness = next(span for span in spans if span.name == "harness.run")
    finalize = next(span for span in spans if span.name == "harness_ui.finalize")
    assert prepare.parent.span_id == harness.parent.span_id == finalize.parent.span_id == root.context.span_id
    assert prepare.end_time <= harness.start_time < harness.end_time <= finalize.start_time
    assert finalize.attributes["a13n.ui.continuation.status"] == "selected"
    assert root.attributes["a13n.output.kind"] == "completed"
    assert prepare.attributes["a13n.phase.status"] == "completed"
    assert finalize.attributes["langfuse.observation.metadata.phase_continuation_status"] == "selected"
    for owner in (prepare, finalize):
        if content is HarnessTraceContent.NONE:
            assert "a13n.output" not in owner.attributes
        else:
            assert isinstance(json.loads(owner.attributes["a13n.output"]), dict)
    if content is HarnessTraceContent.NONE:
        assert "a13n.input" not in root.attributes and "a13n.output" not in root.attributes
    else:
        assert json.loads(root.attributes["langfuse.observation.input"]) == "this turn input"
        assert json.loads(root.attributes["langfuse.observation.output"]) == "observation test completed"
    for span in spans:
        if span is not root:
            assert "langfuse.trace.input" not in span.attributes
            assert "langfuse.trace.output" not in span.attributes


async def test_root_preserves_answer_but_marks_failed_save(tmp_path, monkeypatch, telemetry):
    from a13n_harness_ui.root_execution import RootContinuationSelection, RootRunExecutor

    provider, exporter = telemetry
    await install_model(monkeypatch)

    select_state = RootRunExecutor._select_state
    saves = 0

    async def fail_save(self, **kwargs):
        nonlocal saves
        saves += 1
        if saves == 1:
            return await select_state(self, **kwargs)
        # Fail terminal persistence only; the request checkpoint must succeed
        # before the model can produce the answer this test observes.
        return RootContinuationSelection(status="failed", error=ValueError("private save detail"))

    monkeypatch.setattr(RootRunExecutor, "_select_state", fail_save)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(
        settings,
        configuration_path=configuration(tmp_path),
        instrumentation=HarnessInstrumentation(tracer_provider=provider),
    ) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="hello")
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.failed
    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.name == "harness_ui.root")
    finalize = next(span for span in spans if span.name == "harness_ui.finalize")
    assert json.loads(root.attributes["a13n.output"]) == "observation test completed"
    assert root.attributes["a13n.output.kind"] == "failed"
    assert finalize.status.status_code is StatusCode.ERROR
    assert finalize.attributes["a13n.ui.continuation.status"] == "failed"
    assert json.loads(finalize.attributes["a13n.output"])["continuation_status"] == "failed"
    assert finalize.attributes["langfuse.observation.metadata.phase_status"] == "failed"
    assert "private save detail" not in str(finalize.attributes)


async def test_skill_summary_ignores_child_thread_and_unrelated_events(telemetry):
    from datetime import UTC, datetime

    from a13n_harness import HarnessEvent, HarnessExtensionEvent
    from a13n_harness_ui.observation import record_skill_event

    provider, exporter = telemetry

    def event(thread, payload):
        return HarnessEvent(
            thread_id=thread,
            run_id="run-test",
            sequence=0,
            occurred_at=datetime.now(UTC),
            event=HarnessExtensionEvent(kind="context", payload=payload),
        )

    with UiObservation(HarnessInstrumentation(tracer_provider=provider)).operation(
        "root", thread_id="thread-parent", operation_id="receipt-test"
    ):
        record_skill_event(
            event("thread-parent", {"type": "skills_catalog_resolved", "skills": ["review"], "skill_count": 1})
        )
        record_skill_event(event("thread-child", {"type": "skill_accessed", "skill_name": "child-only"}))
        record_skill_event(event("thread-parent", {"type": "skill_accessed", "skill_name": "review"}))
        record_skill_event(event("thread-parent", "unrelated context payload"))
        with UiObservation(None).operation("root", thread_id="thread-parent", operation_id="disabled"):
            record_skill_event(event("thread-parent", {"type": "skill_accessed", "skill_name": "disabled"}))
    attributes = exporter.get_finished_spans()[-1].attributes
    assert attributes["a13n.skills.available"] == ("review",)
    assert attributes["a13n.skills.accessed"] == ("review",)
    assert attributes["a13n.skills.access_count"] == 1


async def test_automatic_provider_uses_explicit_local_deployment_resource(monkeypatch):
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: ProxyTracerProvider())
    monkeypatch.setattr("a13n_harness_ui.observation.OTLPSpanExporter", lambda: exporter)
    monkeypatch.setenv("A13N_HARNESS_TRACE_LEVEL", "verbose")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment.name=local")
    async with open_observation() as observation:
        with observation.operation("root", thread_id="thread-root", operation_id="receipt-test"):
            with observation.operation(
                "subagent", thread_id="thread-child", operation_id="execution-test", linked=True
            ):
                pass
    assert all(
        span.resource.attributes["deployment.environment.name"] == "local" for span in exporter.get_finished_spans()
    )
