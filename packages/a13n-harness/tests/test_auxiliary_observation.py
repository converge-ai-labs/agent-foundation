from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import a13n_harness.toolsets.file_media as file_media_module
import pytest
from a13n_harness import (
    AgentContext,
    AgentSpec,
    HarnessBuilder,
    HarnessInstrumentation,
    HarnessState,
    HarnessTraceContent,
    RunBindings,
)
from a13n_harness.capabilities import (
    AgentToolReviewer,
    CodeActCapability,
    CompactionCapability,
    CompactionPolicy,
    HandoffCapability,
    ToolReviewConfig,
)
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration
from a13n_harness.tools import (
    HarnessTool,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolPermissions,
    ToolPermissionsCapability,
)
from a13n_harness.toolsets import AgentMediaUnderstandingProvider, CodeActPolicyToolset, CodeActToolPolicy
from a13n_harness.toolsets.file_media import MediaUnderstandingRequest
from a13n_harness.usage import ProviderUsageRecord
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.trace import StatusCode
from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.usage import RequestUsage

from .test_tool_observation import _environment, _provider
from .test_tool_review import _metadata

pytestmark = pytest.mark.anyio


async def _allow(invocation, metadata, *, context):
    return InvocationPolicyDecision.allow()


async def _review_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[DeltaToolCalls]:
    yield {0: DeltaToolCall(name=info.output_tools[0].name, json_args='{"risk":"low","reason":"safe"}')}


@pytest.mark.parametrize("kind", ["shell", "image", "video", "audio"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("content", list(HarnessTraceContent))
async def test_auxiliary_models_are_native_descendants_of_the_invoking_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, nested: bool, content: HarnessTraceContent
) -> None:
    provider, exporter = _provider()
    capabilities: list[Any] = []
    executed: list[str] = []
    if kind == "shell":
        tool_name = "shell_exec"
        arguments = {"command": "printf private-command"}

        def shell_exec(command: str) -> str:
            executed.append(command)
            return "executed"

        capabilities.extend(
            [
                Capability(tools=[HarnessTool(shell_exec, harness_metadata=_metadata())]),
                ToolPermissionsCapability(
                    ToolPermissions(default="review"),
                    review=ToolReviewConfig(model="test:review"),
                    reviewer=AgentToolReviewer(
                        FunctionModel(stream_function=_review_model), config=ToolReviewConfig(model="test:review")
                    ),
                ),
            ]
        )
        auxiliary_name = "tool-review"
        source = "tool.review"
    else:
        tool_name = "view"
        suffix = {"image": "png", "video": "mp4", "audio": "mp3"}[kind]
        (tmp_path / f"private-media.{suffix}").write_bytes(b"private-media-bytes")
        arguments = {"file_path": f"/workspace/private-media.{suffix}", "instructions": "private-instructions"}
        model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("private-analysis")]))
        monkeypatch.setenv(f"A13N_HARNESS_{kind.upper()}_UNDERSTANDING_MODEL", "test:understanding")
        monkeypatch.setattr(file_media_module, "infer_model", lambda model_id: model)
        capabilities.append(DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()))
        auxiliary_name = f"{kind}-understanding"
        source = "files.media_understanding"

    if nested:

        class NestedPolicy(AbstractCapability[AgentContext]):
            def get_wrapper_toolset(self, toolset):
                return CodeActPolicyToolset(wrapped=toolset, policy=CodeActToolPolicy(tools={tool_name: True}))

        capabilities.extend([NestedPolicy(), CodeActCapability()])
    requests = 0

    async def main_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        requests += 1
        if requests == 1:
            if nested:
                code = f"await {tool_name}(" + ", ".join(f"{k}={v!r}" for k, v in arguments.items()) + ")"
                yield {0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": code}))}
            else:
                yield {0: DeltaToolCall(name=tool_name, json_args=json.dumps(arguments))}
        else:
            yield "done"

    executable = HarnessBuilder(
        instrumentation=HarnessInstrumentation(tracer_provider=provider, trace_content=content)
    ).build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=main_model), capabilities=capabilities)
    result = await executable.run(
        "inspect",
        bindings=RunBindings.embedded(
            environment=_environment(tmp_path), capabilities=[InvocationPolicyCapability(evaluator=_allow)]
        ),
    )
    assert result.output_or_raise() == "done"
    records = [record for record in result.usage_records if record.source == source]
    assert len(records) == (2 if kind == "shell" and nested else 1)
    assert source == records[0].source
    spans = exporter.get_finished_spans()
    review = next(
        (
            span
            for span in spans
            if span.attributes.get("a13n.operation.kind") == "tool_review"
            and span.attributes.get("a13n.tool.id") == "environment.shell_exec"
        ),
        None,
    )
    auxiliary = next(
        span
        for span in spans
        if span.attributes.get("gen_ai.operation.name") == "invoke_agent"
        and span.attributes.get("gen_ai.agent.name") == auxiliary_name
        and (review is None or span.parent.span_id == review.context.span_id)
    )
    tool = next(span for span in spans if span.attributes.get("gen_ai.tool.name") == tool_name)
    if kind == "shell":
        assert review is not None
        assert auxiliary.parent.span_id == review.context.span_id
        assert review.attributes["a13n.tool.id"] == "environment.shell_exec"
    else:
        assert auxiliary.parent.span_id == tool.context.span_id
    model_spans = [
        span
        for span in spans
        if span.parent is not None
        and span.parent.span_id == auxiliary.context.span_id
        and span.attributes.get("gen_ai.operation.name") == "chat"
    ]
    assert len(model_spans) == 1
    root = next(span for span in spans if span.name == "harness.run")
    assert sum(span.name == "harness.run" for span in spans) == 1
    for span in [auxiliary, *model_spans]:
        assert span.context.trace_id == root.context.trace_id
        assert span.attributes["a13n.run.id"] == root.attributes["a13n.run.id"]
        assert "a13n.model_attempt.index" not in span.attributes
    assert tool.attributes["a13n.tool.result.status"] == "returned"
    if nested:
        runner = next(span for span in spans if span.attributes.get("gen_ai.tool.name") == "run_code")
        assert tool.parent.span_id == runner.context.span_id
    encoded = json.dumps([dict(span.attributes) for span in spans])
    if content is HarnessTraceContent.NONE:
        assert "private-command" not in encoded
        assert "private-instructions" not in encoded
        assert "private-analysis" not in encoded
    if content is not HarnessTraceContent.FULL:
        assert base64.b64encode(b"private-media-bytes").decode() not in encoded
    if kind == "shell":
        assert executed == ["printf private-command"]


@pytest.mark.parametrize("mode", ["disabled", "metrics"])
async def test_auxiliary_agents_do_not_fall_back_to_global_instrumentation(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    provider, exporter = _provider()
    monkeypatch.setattr(Agent, "_instrument_default", InstrumentationSettings(tracer_provider=provider))
    reader = InMemoryMetricReader()
    configuration = (
        HarnessInstrumentation(meter_provider=MeterProvider(metric_readers=[reader])) if mode == "metrics" else None
    )
    media = AgentMediaUnderstandingProvider(
        models={"image": FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("analysis")]))}
    )
    reviewer = AgentToolReviewer(
        FunctionModel(stream_function=_review_model), config=ToolReviewConfig(model="test:review")
    )

    async def shell_exec(command: str) -> str:
        return (
            await media.understand(
                MediaUnderstandingRequest(
                    kind="image", media_type="image/png", source_name="test.png", source_bytes=b"test"
                )
            )
        ).text

    executable = HarnessBuilder(instrumentation=configuration).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=_single_shell_call()),
        capabilities=[
            Capability(tools=[HarnessTool(shell_exec, harness_metadata=_metadata())]),
            ToolPermissionsCapability(
                ToolPermissions(default="review"), review=ToolReviewConfig(model="test:review"), reviewer=reviewer
            ),
        ],
    )
    with provider.get_tracer("host").start_as_current_span("host"):
        result = await executable.run(
            "go", bindings=RunBindings.embedded(capabilities=[InvocationPolicyCapability(evaluator=_allow)])
        )
    assert result.output_or_raise() == "done"
    assert [span.name for span in exporter.get_finished_spans()] == ["host"]
    if mode == "metrics":
        data = reader.get_metrics_data()
        assert data is not None
        metrics = [
            metric for resource in data.resource_metrics for scope in resource.scope_metrics for metric in scope.metrics
        ]
        tokens = next(metric for metric in metrics if metric.name == "gen_ai.client.token.usage")
        assert (
            sum(point.count for point in tokens.data.data_points if point.attributes["gen_ai.token.type"] == "input")
            == 4
        )


def _single_shell_call():
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {0: DeltaToolCall(name="shell_exec", json_args='{"command":"test"}')}
        else:
            yield "done"

    return stream


async def test_compaction_keeps_native_model_trace_and_handoff_has_no_extra_model() -> None:
    provider, exporter = _provider()
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield "compacted summary"
        elif calls == 2:
            yield {0: DeltaToolCall(name="summarize", json_args='{"content":"explicit summary"}')}
        else:
            yield "done"

    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(tracer_provider=provider)).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=[CompactionCapability(CompactionPolicy(trigger_tokens=2_000)), HandoffCapability()],
    )
    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("original task")]),
            ModelResponse(parts=[TextPart("old answer")], usage=RequestUsage(input_tokens=2_100, output_tokens=100)),
        )
    )
    result = await executable.run("continue", previous_state=previous)
    assert result.output_or_raise() == "done"
    spans = exporter.get_finished_spans()
    compaction = next(span for span in spans if span.attributes.get("a13n.operation.kind") == "compaction")
    child = next(
        span for span in spans if span.parent is not None and span.parent.span_id == compaction.context.span_id
    )
    assert child.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert any(
        span.parent is not None
        and span.parent.span_id == child.context.span_id
        and span.attributes.get("gen_ai.operation.name") == "chat"
        for span in spans
    )
    handoff = next(span for span in spans if span.attributes.get("a13n.operation.kind") == "handoff")
    tool = next(span for span in spans if span.attributes.get("gen_ai.tool.name") == "summarize")
    assert handoff.parent.span_id == tool.context.span_id
    assert not any(span.parent is not None and span.parent.span_id == handoff.context.span_id for span in spans)
    assert sum(span.attributes.get("gen_ai.operation.name") == "chat" for span in spans) == calls == 3


async def test_shared_auxiliary_agents_use_each_concurrent_runs_observation() -> None:
    entered = 0
    both_entered = asyncio.Event()

    async def review(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[DeltaToolCalls]:
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await both_entered.wait()
        async for delta in _review_model(messages, info):
            yield delta

    reviewer = AgentToolReviewer(FunctionModel(stream_function=review), config=ToolReviewConfig(model="test:review"))
    media = AgentMediaUnderstandingProvider(
        models={"image": FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("private-analysis")]))}
    )

    async def shell_exec(command: str) -> str:
        return (
            await media.understand(
                MediaUnderstandingRequest(
                    kind="image", media_type="image/png", source_name="test.png", source_bytes=b"test"
                )
            )
        ).text

    exports = []
    executables = []
    for content in (HarnessTraceContent.NONE, HarnessTraceContent.STANDARD):
        provider, exporter = _provider()
        exports.append(exporter)
        executables.append(
            HarnessBuilder(
                instrumentation=HarnessInstrumentation(tracer_provider=provider, trace_content=content)
            ).build(
                AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=_single_shell_call()),
                capabilities=[
                    Capability(tools=[HarnessTool(shell_exec, harness_metadata=_metadata())]),
                    ToolPermissionsCapability(
                        ToolPermissions(default="review"),
                        review=ToolReviewConfig(model="test:review"),
                        reviewer=reviewer,
                    ),
                ],
            )
        )
    async with asyncio.timeout(5):
        results = await asyncio.gather(
            *(
                executable.run(
                    "go", bindings=RunBindings.embedded(capabilities=[InvocationPolicyCapability(evaluator=_allow)])
                )
                for executable in executables
            )
        )
    assert all(result.output_or_raise() == "done" for result in results)
    trace_ids = []
    for exporter in exports:
        spans = exporter.get_finished_spans()
        roots = [span for span in spans if span.name == "harness.run"]
        assert len(roots) == 1
        root = roots[0]
        trace_ids.append(root.context.trace_id)
        assert all(span.context.trace_id == root.context.trace_id for span in spans)
        for name in ("tool-review", "image-understanding"):
            assert (
                sum(
                    span.attributes.get("gen_ai.operation.name") == "invoke_agent"
                    and span.attributes.get("gen_ai.agent.name") == name
                    for span in spans
                )
                == 1
            )
    assert trace_ids[0] != trace_ids[1]
    assert "private-analysis" not in json.dumps([dict(span.attributes) for span in exports[0].get_finished_spans()])
    assert "private-analysis" in json.dumps([dict(span.attributes) for span in exports[1].get_finished_spans()])


@pytest.mark.parametrize("failure", ["invalid", "timeout", "cancelled"])
async def test_instrumented_review_preserves_failure_policy_and_cancellation(failure: str) -> None:
    provider, exporter = _provider()
    started = asyncio.Event()
    executed = False

    async def review(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        started.set()
        if failure == "invalid":
            yield "invalid assessment"
        else:
            await asyncio.Event().wait()

    reviewer = AgentToolReviewer(
        FunctionModel(stream_function=review),
        config=ToolReviewConfig(model="test:review", timeout_seconds=0.01 if failure == "timeout" else 120),
    )

    def shell_exec(command: str) -> str:
        nonlocal executed
        executed = True
        return "executed"

    executable = HarnessBuilder(instrumentation=HarnessInstrumentation(tracer_provider=provider)).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=_single_shell_call()),
        capabilities=[
            Capability(tools=[HarnessTool(shell_exec, harness_metadata=_metadata())]),
            ToolPermissionsCapability(
                ToolPermissions(default="review"), review=ToolReviewConfig(model="test:review"), reviewer=reviewer
            ),
        ],
    )
    task = asyncio.create_task(
        executable.run("go", bindings=RunBindings.embedded(capabilities=[InvocationPolicyCapability(evaluator=_allow)]))
    )
    if failure == "cancelled":
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        assert result.status == ("completed" if failure == "timeout" else "suspended")
        if failure == "timeout":
            assert "Tool review timed out; the tool was not executed" in str(result.all_messages())
        if failure == "invalid":
            assert any(
                isinstance(record, ProviderUsageRecord) and record.source == "tool.review"
                for record in result.usage_records
            )
    assert not executed
    spans = exporter.get_finished_spans()
    auxiliary = next(
        span
        for span in spans
        if span.attributes.get("gen_ai.operation.name") == "invoke_agent"
        and span.attributes.get("gen_ai.agent.name") == "tool-review"
    )
    review = next(span for span in spans if span.attributes.get("a13n.operation.kind") == "tool_review")
    assert auxiliary.parent.span_id == review.context.span_id
    assert not any(span.attributes.get("gen_ai.tool.name") == "shell_exec" for span in spans)
    if failure == "invalid":
        assert auxiliary.status.status_code is StatusCode.ERROR
