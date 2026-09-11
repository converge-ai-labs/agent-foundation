from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from a13n_environment import DirectLocalProviderConfiguration, DirectLocalRootConfiguration
from a13n_harness import HarnessBuilder, HarnessInstrumentation, HarnessTraceContent, RunBindings
from a13n_harness._tool_observation import record_tool_operation_failure
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.environment import (
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.providers import EnvironmentRuntimeMount
from a13n_harness.observation import record_tool_outcome_unknown, set_tool_span_attributes
from a13n_harness.toolsets.files import _environment_error_result as file_error_result
from a13n_harness.toolsets.shell import _environment_error_result as shell_error_result
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF
from opentelemetry.trace import StatusCode
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, RetryPromptPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

from .environment_helpers import DirectLocalEnvironmentProviderBinding

pytestmark = pytest.mark.anyio


def _provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _environment(root: Path):
    return create_environment_runtime(
        mounts={
            "workspace": EnvironmentRuntimeMount(
                binding=DirectLocalEnvironmentProviderBinding(
                    DirectLocalProviderConfiguration(root=DirectLocalRootConfiguration(path=root)),
                    environment_id="tool-observation-test",
                ),
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                working_directory="/",
                mount_path="/workspace",
            )
        },
        default_mount="workspace",
    )


@pytest.mark.parametrize("content", list(HarnessTraceContent))
@pytest.mark.parametrize("nested", [False, True])
async def test_environment_failures_mark_only_owning_native_tool_span(
    tmp_path: Path, content: HarnessTraceContent, nested: bool
) -> None:
    (tmp_path / "existing.txt").write_text("existing content")
    provider, exporter = _provider()
    requests = 0
    returned: list[Any] = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        requests += 1
        if requests == 1:
            if nested:
                code = (
                    "import asyncio\n"
                    "await asyncio.gather(view(file_path='/workspace/missing.txt'), "
                    "view(file_path='/workspace/existing.txt'), view(file_path='/unmounted/file.txt'))"
                )
                yield {0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": code}), tool_call_id="runner")}
            else:
                yield {
                    i: DeltaToolCall(name="view", json_args=json.dumps({"file_path": path}), tool_call_id=f"view-{i}")
                    for i, path in enumerate(
                        ("/workspace/missing.txt", "/workspace/existing.txt", "/unmounted/file.txt")
                    )
                }
        else:
            returned.extend(
                part.content
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart | RetryPromptPart)
            )
            yield "recovered"

    capabilities: list[Any] = [DynamicEnvironmentCapability(DynamicEnvironmentConfiguration())]
    if nested:
        from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
        from pydantic_ai.capabilities import AbstractCapability

        class FileCodeActPolicy(AbstractCapability[Any]):
            def get_wrapper_toolset(self, toolset):
                return CodeActPolicyToolset(wrapped=toolset, policy=CodeActToolPolicy(tools={"view": True}))

        capabilities.extend([FileCodeActPolicy(), CodeActCapability()])
    executable = HarnessBuilder(
        instrumentation=HarnessInstrumentation(tracer_provider=provider, trace_content=content)
    ).build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=capabilities)
    result = await executable.run("inspect", bindings=RunBindings.embedded(environment=_environment(tmp_path)))
    assert result.output_or_raise() == "recovered"
    assert requests == 2
    assert "environment_not_found" in json.dumps(returned)
    assert "environment_selection_invalid" in json.dumps(returned)
    assert "existing content" in json.dumps(returned)

    spans = exporter.get_finished_spans()
    tools = [span for span in spans if span.attributes.get("gen_ai.operation.name") == "execute_tool"]
    assert len(tools) == (4 if nested else 3)
    failed = [span for span in tools if span.attributes.get("a13n.tool.result.status") == "operation_failed"]
    assert len(failed) == 2
    assert {span.attributes["a13n.tool.failure.stage"] for span in failed} == {"execution"}
    assert {span.attributes["a13n.tool.failure.reason"] for span in failed} == {"path_not_found", "path_outside_mounts"}
    for span in failed:
        assert span.status.status_code is StatusCode.ERROR
        assert span.status.description is None
        assert not span.events
        assert span.attributes["langfuse.observation.metadata.tool_result_status"] == "operation_failed"
    for span in tools:
        if span not in failed:
            assert span.status.status_code is StatusCode.UNSET
            assert span.attributes["a13n.tool.result.status"] == "returned"
            assert "a13n.tool.failure.code" not in span.attributes
    if nested:
        runner = next(span for span in tools if span.attributes["gen_ai.tool.name"] == "run_code")
        assert all(span.parent.span_id == runner.context.span_id for span in tools if span is not runner)
    root = next(span for span in spans if span.name == "harness.run")
    assert root.attributes["a13n.run.outcome"] == "completed"
    assert root.status.status_code is StatusCode.UNSET
    assert "a13n.tool.result.status" not in root.attributes
    if content is HarnessTraceContent.NONE:
        encoded = json.dumps([dict(span.attributes) for span in spans])
        assert "existing content" not in encoded
        assert "missing.txt" not in encoded


async def test_business_false_is_not_a_failure_and_parallel_reports_are_isolated() -> None:
    provider, exporter = _provider()
    tools = FunctionToolset()
    started = asyncio.Event()

    @tools.tool_plain
    async def business() -> dict[str, Any]:
        started.set()
        await asyncio.sleep(0)
        return {"ok": False, "error": {"code": "environment_not_found"}}

    @tools.tool_plain
    async def environment_failure() -> dict[str, Any]:
        await started.wait()
        result = shell_error_result(EnvironmentError("private exception", code="environment_unknown_outcome"))
        await asyncio.sleep(0)
        return result

    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(tracer_provider=provider)).build(
        AgentSpec(), output_type=str, model=TestModel(), capabilities=[Capability(toolsets=[tools])]
    )
    assert (await executable.run("inspect")).status == "completed"
    spans = {span.attributes.get("gen_ai.tool.name"): span for span in exporter.get_finished_spans()}
    assert spans["business"].attributes["a13n.tool.result.status"] == "returned"
    assert spans["business"].status.status_code is StatusCode.UNSET
    failure = spans["environment_failure"]
    assert failure.attributes["a13n.tool.failure.code"] == "environment_unknown_outcome"
    assert failure.status.status_code is StatusCode.ERROR
    assert "private exception" not in json.dumps(dict(failure.attributes))


@pytest.mark.parametrize("mode", ["disabled", "metrics", "unsampled"])
async def test_inactive_tool_observation_does_not_enrich_host_span(mode: str) -> None:
    provider, exporter = _provider()
    configuration = None
    if mode == "metrics":
        configuration = HarnessInstrumentation(meter_provider=MeterProvider())
    elif mode == "unsampled":
        configuration = HarnessInstrumentation(tracer_provider=TracerProvider(sampler=ALWAYS_OFF))
    tools = FunctionToolset()

    @tools.tool_plain
    def failed() -> dict[str, Any]:
        record_tool_outcome_unknown()
        set_tool_span_attributes({"a13n.search.provider.type": "test"})
        return file_error_result(EnvironmentError("private exception", code="environment_not_found"))

    executable = HarnessBuilder(instrumentation=configuration).build(
        AgentSpec(), output_type=str, model=TestModel(), capabilities=[Capability(toolsets=[tools])]
    )
    with provider.get_tracer("host").start_as_current_span("host"):
        record_tool_operation_failure("environment_not_found")
        assert (await executable.run("inspect")).status == "completed"
        record_tool_operation_failure("environment_not_found")
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code is StatusCode.UNSET
    assert not any("tool" in key for key in spans[0].attributes)


async def test_tool_metadata_failures_are_failsoft(monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness._tool_observation as observation

    (
        provider,
        _,
    ) = _provider()
    tools = FunctionToolset()

    @tools.tool_plain
    def failed() -> dict[str, Any]:
        return shell_error_result(EnvironmentError("private exception", code="environment_denied"))

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("telemetry failed")

    monkeypatch.setattr(observation, "record_span_metadata", broken)
    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(tracer_provider=provider)).build(
        AgentSpec(), output_type=str, model=TestModel(), capabilities=[Capability(toolsets=[tools])]
    )
    result = await executable.run("inspect")
    assert result.status == "completed"
    assert "environment_denied" in result.output_or_raise()


async def test_preparation_failure_is_reported_without_dispatch() -> None:
    from a13n_harness.tools import HarnessTool, HarnessToolMetadata, ToolOutputPolicy

    provider, exporter = _provider()

    def action() -> str:
        pytest.fail("preparation failure must not dispatch the tool")

    async def resources(arguments: Any, *, context: Any) -> Any:
        raise EnvironmentError(
            "private resolution error",
            code="environment_selection_invalid",
            details={"reason": "mount_selection_unavailable"},
        )

    tool = HarnessTool(
        action,
        harness_metadata=HarnessToolMetadata(
            tool_id="test.preparation",
            effects=frozenset({"read"}),
            credential_audiences=(),
            idempotency="read_only",
            resource_resolver=resources,
            output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
        ),
    )
    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(tracer_provider=provider)).build(
        AgentSpec(), output_type=str, model=TestModel(), capabilities=[Capability(tools=[tool])]
    )
    result = await executable.run("inspect")
    assert result.status == "completed"
    assert "environment_selection_invalid" in result.output_or_raise()
    span = next(span for span in exporter.get_finished_spans() if span.attributes.get("gen_ai.tool.name") == "action")
    assert span.attributes["a13n.tool.failure.stage"] == "preparation"
    assert span.attributes["a13n.tool.failure.reason"] == "mount_selection_unavailable"
    assert span.status.status_code is StatusCode.ERROR


async def test_disabled_inline_run_masks_parent_tool_reporting() -> None:
    provider, exporter = _provider()
    child_tools = FunctionToolset()

    @child_tools.tool_plain
    def child_failure() -> dict[str, Any]:
        record_tool_outcome_unknown()
        set_tool_span_attributes({"a13n.search.provider.type": "test"})
        return file_error_result(EnvironmentError("private exception", code="environment_not_found"))

    child = HarnessBuilder(instrumentation=None).build(
        AgentSpec(), output_type=str, model=TestModel(), capabilities=[Capability(toolsets=[child_tools])]
    )
    parent_tools = FunctionToolset()

    @parent_tools.tool_plain
    async def parent() -> str:
        result = await child.run("inspect")
        assert result.status == "completed"
        return "child recovered"

    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(tracer_provider=provider)).build(
        AgentSpec(), output_type=str, model=TestModel(), capabilities=[Capability(toolsets=[parent_tools])]
    )
    assert (await executable.run("inspect")).status == "completed"
    span = next(span for span in exporter.get_finished_spans() if span.attributes.get("gen_ai.tool.name") == "parent")
    assert span.attributes["a13n.tool.result.status"] == "returned"
    assert span.status.status_code is StatusCode.UNSET
    assert "a13n.search.provider.type" not in span.attributes


async def test_reporting_is_bounded_and_does_not_take_over_tool_internal_spans() -> None:
    from opentelemetry import trace

    provider, exporter = _provider()
    tools = FunctionToolset()

    @tools.tool_plain
    def action() -> str:
        with provider.get_tracer("tool-internal").start_as_current_span("internal"):
            record_tool_operation_failure("environment_denied")
            record_tool_outcome_unknown()
            set_tool_span_attributes({"a13n.search.provider.type": "test"})
        record_tool_operation_failure("/private/error", reason="/private/path")
        record_tool_operation_failure("second_error", reason="second_reason")
        assert trace.get_current_span().is_recording()
        return "failure reported"

    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(tracer_provider=provider)).build(
        AgentSpec(), output_type=str, model=TestModel(), capabilities=[Capability(toolsets=[tools])]
    )
    assert (await executable.run("inspect")).status == "completed"
    spans = exporter.get_finished_spans()
    internal = next(span for span in spans if span.name == "internal")
    assert not internal.attributes
    assert internal.status.status_code is StatusCode.UNSET
    tool = next(span for span in spans if span.attributes.get("gen_ai.tool.name") == "action")
    assert tool.attributes["a13n.tool.result.status"] == "operation_failed"
    assert "a13n.tool.failure.code" not in tool.attributes
    assert "a13n.tool.failure.reason" not in tool.attributes
    assert tool.status.status_code is StatusCode.ERROR


@pytest.mark.parametrize("source", ["web", "documents", "media", "mem0", "file_media", "note_key", "note_value"])
async def test_first_party_error_projectors_report_without_changing_results(source: str) -> None:
    from a13n_harness.toolsets.documents import _document_error
    from a13n_harness.toolsets.files import _media_understanding_error
    from a13n_harness.toolsets.media import _media_error
    from a13n_harness.toolsets.mem0 import _failure
    from a13n_harness.toolsets.web import _web_error
    from a13n_harness.toolsets.working_state import _validate_note, _validate_note_key

    projectors = {
        "web": lambda: _web_error("web_timeout"),
        "documents": lambda: _document_error("document_conversion_failed"),
        "media": lambda: _media_error("media_read_failed"),
        "mem0": lambda: _failure("mem0_timeout"),
        "file_media": lambda: _media_understanding_error("media_understanding_failed"),
        "note_key": lambda: _validate_note_key(""),
        "note_value": lambda: _validate_note("key", "\x00"),
    }
    project = projectors[source]
    expected = project()
    provider, exporter = _provider()
    tools = FunctionToolset()
    returned = []

    @tools.tool_plain
    def action() -> Any:
        result = project()
        returned.append(result)
        return result

    executable = HarnessBuilder(
        instrumentation=HarnessInstrumentation(tracer_provider=provider, trace_content=HarnessTraceContent.NONE)
    ).build(AgentSpec(), output_type=str, model=TestModel(), capabilities=[Capability(toolsets=[tools])])
    assert (await executable.run("inspect")).status == "completed"
    assert returned == [expected]
    span = next(span for span in exporter.get_finished_spans() if span.attributes.get("gen_ai.tool.name") == "action")
    assert expected is not None
    assert span.attributes["a13n.tool.failure.code"] == expected["error"]["code"]
    assert span.attributes["a13n.tool.result.status"] == "operation_failed"
    assert span.status.status_code is StatusCode.ERROR


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("note_get", {"key": "missing"}, "note_not_found"),
        ("note_delete", {"key": "missing"}, None),
        ("task_get", {"task_id": "task-1"}, "task_not_found"),
        ("task_create", {"subject": "", "description": "invalid"}, "task_request_invalid"),
    ],
)
async def test_working_state_distinguishes_operation_failure_from_idempotent_absence(
    name: str, arguments: dict[str, Any], code: str | None
) -> None:
    from a13n_harness.capabilities import WorkingStateCapability

    provider, exporter = _provider()
    requests = 0

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        requests += 1
        if requests == 1:
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id="working-state")}
        else:
            yield "done"

    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(tracer_provider=provider)).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=[WorkingStateCapability()],
    )
    assert (await executable.run("inspect")).status == "completed"
    span = next(span for span in exporter.get_finished_spans() if span.attributes.get("gen_ai.tool.name") == name)
    if code is None:
        assert span.attributes["a13n.tool.result.status"] == "returned"
        assert span.status.status_code is StatusCode.UNSET
    else:
        assert span.attributes["a13n.tool.failure.code"] == code
        assert span.status.status_code is StatusCode.ERROR


async def test_http_status_failure_is_distinct_from_head_absence_probe() -> None:
    from a13n_harness.toolsets.web import WebResponse, _head_projection, _status_error

    provider, exporter = _provider()

    async def body() -> AsyncIterator[bytes]:
        yield b""

    response = WebResponse(
        status_code=404,
        final_url="https://example.test/private",
        canonical_url="https://example.test/private",
        headers={},
        body=body(),
        reason="private",
    )
    tools = FunctionToolset()

    @tools.tool_plain
    def probe() -> Any:
        return _head_projection(response)

    @tools.tool_plain
    def fetch() -> Any:
        return _status_error(response)

    executable = HarnessBuilder(
        instrumentation=HarnessInstrumentation(tracer_provider=provider, trace_content=HarnessTraceContent.NONE)
    ).build(AgentSpec(), output_type=str, model=TestModel(), capabilities=[Capability(toolsets=[tools])])
    assert (await executable.run("inspect")).status == "completed"
    spans = {span.attributes.get("gen_ai.tool.name"): span for span in exporter.get_finished_spans()}
    assert spans["probe"].status.status_code is StatusCode.UNSET
    assert spans["probe"].attributes["a13n.tool.result.status"] == "returned"
    assert spans["fetch"].status.status_code is StatusCode.ERROR
    assert spans["fetch"].attributes["a13n.tool.failure.code"] == "web_http_status"
    assert "private" not in json.dumps(dict(spans["fetch"].attributes))


@pytest.mark.parametrize("content", list(HarnessTraceContent))
async def test_explicit_uncertainty_is_not_failure_or_business_result_inference(content: HarnessTraceContent) -> None:
    provider, exporter = _provider()
    tools = FunctionToolset()
    calls = []

    @tools.tool_plain
    async def unknown() -> dict[str, str]:
        calls.append("unknown")
        record_tool_outcome_unknown()
        set_tool_span_attributes({"a13n.search.provider.type": "test"})
        await asyncio.sleep(0)
        return {"kind": "outcome_unknown"}

    @tools.tool_plain
    async def business() -> dict[str, str]:
        calls.append("business")
        await asyncio.sleep(0)
        return {"kind": "outcome_unknown"}

    executable = HarnessBuilder(
        instrumentation=HarnessInstrumentation(tracer_provider=provider, trace_content=content)
    ).build(AgentSpec(), output_type=str, model=TestModel(), capabilities=[Capability(toolsets=[tools])])
    assert (await executable.run("inspect")).status == "completed"
    assert sorted(calls) == ["business", "unknown"]
    spans = exporter.get_finished_spans()
    tools_by_name = {span.attributes.get("gen_ai.tool.name"): span for span in spans}
    unknown_span = tools_by_name["unknown"]
    assert unknown_span.attributes["a13n.tool.result.status"] == "outcome_unknown"
    assert unknown_span.attributes["langfuse.observation.metadata.tool_result_status"] == "outcome_unknown"
    assert unknown_span.attributes["a13n.search.provider.type"] == "test"
    assert unknown_span.status.status_code is StatusCode.UNSET
    assert not unknown_span.events
    assert "a13n.tool.failure.code" not in unknown_span.attributes
    assert tools_by_name["business"].attributes["a13n.tool.result.status"] == "returned"
    for span in spans:
        if span is not unknown_span:
            assert "a13n.search.provider.type" not in span.attributes
            assert span.attributes.get("a13n.tool.result.status") != "outcome_unknown"


async def test_public_tool_enrichment_is_failsoft(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry.sdk.trace import Span

    provider, exporter = _provider()
    original = Span.set_attributes
    tools = FunctionToolset()

    @tools.tool_plain
    def action() -> str:
        def broken(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("telemetry unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(Span, "set_attributes", broken)
            set_tool_span_attributes({"a13n.search.provider.type": "test"})
            patch.setattr(Span, "is_recording", broken)
            record_tool_outcome_unknown()
        assert Span.set_attributes is original
        return "done"

    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(tracer_provider=provider)).build(
        AgentSpec(), output_type=str, model=TestModel(), capabilities=[Capability(toolsets=[tools])]
    )
    assert (await executable.run("inspect")).status == "completed"
    span = next(span for span in exporter.get_finished_spans() if span.attributes.get("gen_ai.tool.name") == "action")
    assert span.attributes["a13n.tool.result.status"] == "returned"
    assert "a13n.search.provider.type" not in span.attributes
