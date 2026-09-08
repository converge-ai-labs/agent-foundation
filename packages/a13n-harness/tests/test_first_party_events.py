from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from a13n_harness import (
    AgentContext,
    DefinitionError,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessModelCharacteristics,
    HarnessRunResultEvent,
    HarnessState,
    RunBindings,
)
from a13n_harness import AgentSpec as HarnessAgentSpec
from a13n_harness.capabilities import (
    CompactionCapability,
    CompactionPolicy,
    CreateTask,
    EmbeddedTaskStateCell,
    HandoffCapability,
    TaskMutation,
    TaskStateRunCapability,
    WorkingStateCapability,
    WorkingStateConfiguration,
)
from a13n_harness.capabilities.context import _COMPACTION_PROMPT
from a13n_harness.capabilities.lifecycle import _safe_error_code
from a13n_harness.events import (
    ContextOperationCompletedPayload,
    FileChangeProjection,
    FilesystemChangedValue,
    ToolExtraEventPayload,
)
from pydantic import ValidationError
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import (
    CapabilityEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


@dataclass(kw_only=True)
class _ExternalProgressEvent(CapabilityEvent, namespace="test.external", name="external_progress"):
    progress: int


class _CapabilityEventEmitter(AbstractCapability[AgentContext]):
    id = "test.external-event-emitter"

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        await ctx.emit(_ExternalProgressEvent(progress=1))
        return request_context


async def _collect_extensions(
    executable: Any,
    prompt: str,
    *,
    bindings: RunBindings | None = None,
    previous_state: HarnessState | None = None,
) -> tuple[list[HarnessExtensionEvent], HarnessRunResultEvent[Any]]:
    extensions: list[HarnessExtensionEvent] = []
    terminal: HarnessRunResultEvent[Any] | None = None
    async with executable.stream(
        prompt,
        bindings=bindings or RunBindings.embedded(),
        previous_state=previous_state,
    ) as stream:
        async for item in stream:
            if isinstance(item, HarnessEvent) and isinstance(item.event, HarnessExtensionEvent):
                extensions.append(item.event)
            elif isinstance(item, HarnessRunResultEvent):
                terminal = item
    assert terminal is not None
    return extensions, terminal


def _payloads(events: list[HarnessExtensionEvent], kind: str) -> list[dict[str, Any]]:
    return [event.payload for event in events if event.kind == kind]


async def test_native_capability_event_is_exposed_by_harness_stream() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(_CapabilityEventEmitter(),),
    )
    observed: list[_ExternalProgressEvent] = []

    async with executable.stream("run", bindings=RunBindings.embedded()) as stream:
        async for item in stream:
            if isinstance(item, HarnessEvent) and isinstance(item.event, _ExternalProgressEvent):
                observed.append(item.event)

    assert len(observed) == 1
    assert observed[0].event_kind == "capability"
    assert observed[0].kind == "test.external.external_progress"
    assert observed[0].capability_id == _CapabilityEventEmitter.id
    assert observed[0].progress == 1


async def test_model_request_lifecycle_events_are_ordered_and_fail_safely() -> None:
    async def success_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    success = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=success_stream),
    )
    events, terminal = await _collect_extensions(success, "run")

    assert terminal.result.status == "completed"
    lifecycle = _payloads(events, "lifecycle")
    assert [event["type"] for event in lifecycle] == ["model_request_started", "model_request_completed"]
    assert lifecycle[0] == {
        "type": "model_request_started",
        "request_id": "model-request-1",
        "request_index": 0,
        "message_count": 1,
    }
    assert lifecycle[1] == {
        "type": "model_request_completed",
        "request_id": "model-request-1",
        "request_index": 0,
    }

    async def failing_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        raise UnexpectedModelBehavior("provider body must not escape")
        yield "unreachable"

    failing = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=failing_stream),
    )
    events, terminal = await _collect_extensions(failing, "fail")

    assert terminal.result.status == "failed"
    lifecycle = _payloads(events, "lifecycle")
    assert [event["type"] for event in lifecycle] == ["model_request_started", "model_request_failed"]
    assert lifecycle[-1] == {
        "type": "model_request_failed",
        "request_id": "model-request-1",
        "request_index": 0,
        "error_code": "model_request_failed",
    }


def test_tool_extra_event_payload_is_typed_and_bounds_file_changes() -> None:
    value = FilesystemChangedValue(
        changes=(FileChangeProjection(path="source.txt", action="moved", destination="target.txt"),)
    )
    payload = ToolExtraEventPayload(
        tool_call_id="call-1",
        tool_name="move",
        tool_id="filesystem.move",
        name="filesystem.changed",
        value=value.model_dump(mode="json"),
    )

    assert payload.model_dump(mode="json") == {
        "type": "tool_extra",
        "tool_call_id": "call-1",
        "tool_name": "move",
        "tool_id": "filesystem.move",
        "name": "filesystem.changed",
        "value": {
            "changes": [
                {"path": "source.txt", "action": "moved", "destination": "target.txt"},
            ]
        },
    }
    with pytest.raises(ValidationError):
        FileChangeProjection(path="source.txt", action="moved")
    with pytest.raises(ValidationError):
        FileChangeProjection(path="source.txt", action="modified", destination="target.txt")
    with pytest.raises(ValidationError):
        ToolExtraEventPayload(
            tool_call_id="call-1",
            tool_name="move",
            tool_id="filesystem.move",
            name="Filesystem Changed",
            value=value.model_dump(mode="json"),
        )


async def test_first_party_payloads_reject_unsafe_error_codes_and_operation_ids() -> None:
    assert _safe_error_code(DefinitionError("safe", code="safe_code")) == "safe_code"
    assert _safe_error_code(DefinitionError("unsafe", code="provider/body")) == "model_request_failed"
    assert _safe_error_code(DefinitionError("unbounded", code="x" * 129)) == "model_request_failed"
    with pytest.raises(ValidationError):
        ContextOperationCompletedPayload(
            type="compaction_completed",
            operation_id="handoff-wrong-kind",
        )


async def test_invalid_handoff_input_does_not_start_a_context_operation() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal calls
        del messages, info
        calls += 1
        if calls == 1:
            yield {
                0: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps(
                        {
                            "content": "Continue later",
                            "files_to_inspect": ["src/main.py", "src/main.py"],
                        }
                    ),
                    tool_call_id="invalid-summary-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(HandoffCapability(),),
    )
    events, terminal = await _collect_extensions(executable, "Continue")

    assert terminal.result.status == "completed"
    assert _payloads(events, "context") == []


def _is_compact_request(messages: list[ModelMessage]) -> bool:
    return any(
        isinstance(part, UserPromptPart) and part.content == _COMPACTION_PROMPT
        for message in messages
        for part in message.parts
    )


async def test_compaction_events_share_operation_identity_and_provider_usage_snapshot() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del info
        calls += 1
        if _is_compact_request(messages):
            yield "Compacted continuation"
        else:
            yield "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="Original long task")]),
            ModelResponse(
                parts=[TextPart(content="Long response")],
                usage=RequestUsage(input_tokens=2_100, output_tokens=100),
            ),
        )
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=2_000)),),
    )
    events, terminal = await _collect_extensions(executable, "Continue", previous_state=previous)

    assert terminal.result.status == "completed"
    assert calls == 2
    context = _payloads(events, "context")
    assert [event["type"] for event in context] == [
        "context_snapshot",
        "compaction_started",
        "compaction_completed",
    ]
    snapshot = context[0]
    assert snapshot["request_index"] == 0
    assert snapshot["request_tokens"] == 2_200
    assert snapshot["trigger_tokens"] == 2_000
    operations = [event for event in context if event["type"].startswith("compaction_")]
    operation_ids = {event["operation_id"] for event in operations}
    assert len(operation_ids) == 1
    assert next(iter(operation_ids)).startswith("compaction-")


@pytest.mark.parametrize("fails", [False, True])
async def test_compaction_summary_is_native_content_not_lifecycle_metadata(fails: bool) -> None:
    from a13n_harness.capabilities import CompactionSummaryEvent

    summary = "Keep this complete summary.\n" * 3000

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        if _is_compact_request(messages):
            if fails:
                raise RuntimeError("compactor unavailable")
            yield summary
        else:
            yield "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="Original task")]),
            ModelResponse(parts=[TextPart(content="Prior answer")], usage=RequestUsage(input_tokens=2200)),
        )
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=2000)),),
    )
    events = []
    async with executable.stream("Continue", bindings=RunBindings.embedded(), previous_state=previous) as stream:
        async for item in stream:
            events.append(item)
    summaries = [
        item.event
        for item in events
        if isinstance(item, HarnessEvent) and isinstance(item.event, CompactionSummaryEvent)
    ]
    extensions = [
        item.event
        for item in events
        if isinstance(item, HarnessEvent) and isinstance(item.event, HarnessExtensionEvent)
    ]
    if fails:
        assert summaries == []
        assert any(item.payload.get("type") == "compaction_failed" for item in extensions)
    else:
        assert len(summaries) == 1
        assert summaries[0].summary == summary.strip()
        assert summaries[0].kind == "a13n.context.compaction_summary"
        assert summaries[0].capability_id == "a13n.compaction"
        completed = next(item.payload for item in extensions if item.payload.get("type") == "compaction_completed")
        assert summaries[0].operation_id == completed["operation_id"]
        assert "summary" not in completed
        terminal = events[-1]
        assert isinstance(terminal, HarnessRunResultEvent) and terminal.result.state is not None
        assert any(
            isinstance(part, TextPart) and part.content == summary.strip()
            for message in terminal.result.state.message_history
            for part in message.parts
        )


async def test_compaction_uses_native_context_window_and_run_context_usage() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del info
        calls += 1
        if _is_compact_request(messages):
            yield "Compacted continuation"
        else:
            yield "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="Original long task")]),
            ModelResponse(
                parts=[TextPart(content="Long response")],
                usage=RequestUsage(input_tokens=1_700, output_tokens=100),
            ),
        )
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(
            stream_function=stream,
            profile={"context_window": 2_000},
        ),
        capabilities=(CompactionCapability(),),
    )

    events, terminal = await _collect_extensions(executable, "Continue", previous_state=previous)

    assert terminal.result.status == "completed"
    assert calls == 2
    context = _payloads(events, "context")
    assert [event["type"] for event in context] == [
        "context_snapshot",
        "compaction_started",
        "compaction_completed",
    ]
    assert context[0]["request_tokens"] == 1_800
    assert context[0]["trigger_tokens"] == 1_800


async def test_automatic_compaction_skips_history_without_reported_usage() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        yield "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="Original task")]),
            ModelResponse(parts=[TextPart(content="Previous response")]),
        )
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream, profile={"context_window": 2_000}),
        capabilities=(CompactionCapability(),),
    )

    events, terminal = await _collect_extensions(executable, "Continue", previous_state=previous)

    assert terminal.result.status == "completed"
    assert calls == 1
    assert _payloads(events, "context") == []


@pytest.mark.parametrize("context_window", [None, 0, -1])
async def test_automatic_compaction_skips_unusable_native_context_window(context_window: int | None) -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        yield "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="Original task")]),
            ModelResponse(
                parts=[TextPart(content="Previous response")],
                usage=RequestUsage(input_tokens=900, output_tokens=100),
            ),
        )
    )
    profile = {} if context_window is None else {"context_window": context_window}
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream, profile=profile),
        capabilities=(CompactionCapability(),),
    )

    events, terminal = await _collect_extensions(executable, "Continue", previous_state=previous)

    assert terminal.result.status == "completed"
    assert calls == 1
    assert _payloads(events, "context") == []


async def test_automatic_compaction_uses_the_first_token_satisfying_the_float_ratio() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del info
        calls += 1
        if _is_compact_request(messages):
            yield "Compacted continuation"
        else:
            yield "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="Original task")]),
            ModelResponse(
                parts=[TextPart(content="Previous response")],
                usage=RequestUsage(input_tokens=54_900, output_tokens=100),
            ),
        )
    )
    executable = HarnessBuilder().build(
        HarnessAgentSpec(model_characteristics=HarnessModelCharacteristics(compact_threshold=0.55)),
        output_type=str,
        model=FunctionModel(stream_function=stream, profile={"context_window": 100_000}),
        capabilities=(CompactionCapability(),),
    )

    events, terminal = await _collect_extensions(executable, "Continue", previous_state=previous)

    assert terminal.result.status == "completed"
    assert calls == 2
    context = _payloads(events, "context")
    assert [event["type"] for event in context] == [
        "context_snapshot",
        "compaction_started",
        "compaction_completed",
    ]
    assert context[0]["request_tokens"] == 55_000
    assert context[0]["trigger_tokens"] == 55_000


async def test_automatic_compaction_reports_the_reachable_integer_trigger() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        yield "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="Original task")]),
            ModelResponse(
                parts=[TextPart(content="Previous response")],
                usage=RequestUsage(input_tokens=1_700, output_tokens=100),
            ),
        )
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream, profile={"context_window": 2_001}),
        capabilities=(CompactionCapability(),),
    )

    events, terminal = await _collect_extensions(executable, "Continue", previous_state=previous)

    assert terminal.result.status == "completed"
    assert calls == 1
    context = _payloads(events, "context")
    assert [event["type"] for event in context] == ["context_snapshot"]
    assert context[0]["request_tokens"] == 1_800
    assert context[0]["trigger_tokens"] == 1_801


async def test_task_changed_events_are_committed_deltas_and_skip_semantic_noop() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        if not returns:
            name = "task_create"
            arguments = {"subject": "First", "description": "First task"}
        elif len(returns) == 1:
            name = "task_create"
            arguments = {
                "subject": "Second",
                "description": "Second task",
                "blocked_by": ["task-1"],
            }
        elif len(returns) == 2:
            name = "task_update"
            arguments = {"task_id": "task-1"}
        elif len(returns) == 3:
            name = "task_update"
            arguments = {"task_id": "task-1", "status": "in_progress"}
        elif len(returns) == 4:
            name = "task_update"
            arguments = {"task_id": "task-1", "status": "completed"}
        else:
            yield "done"
            return
        yield {
            0: DeltaToolCall(
                name=name,
                json_args=json.dumps(arguments),
                tool_call_id=f"call-{len(returns) + 1}",
            )
        }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(WorkingStateCapability(),),
    )
    events, terminal = await _collect_extensions(executable, "Coordinate")

    assert terminal.result.status == "completed"
    changed = [event for event in _payloads(events, "state") if event["type"] == "task_changed"]
    assert [(event["task"]["id"], event["reason"]) for event in changed] == [
        ("task-1", "created"),
        ("task-1", "dependency_updated"),
        ("task-2", "created"),
        ("task-1", "claimed"),
        ("task-1", "completed"),
    ]
    reciprocal = changed[1:3]
    assert reciprocal[0]["operation_id"] == reciprocal[1]["operation_id"]
    assert reciprocal[0]["task_state_version"] == reciprocal[1]["task_state_version"] == 3
    assert all("description" not in event["task"] and "metadata" not in event["task"] for event in changed)


async def test_provider_task_observation_emits_once_at_harness_read_boundaries() -> None:
    cell = EmbeddedTaskStateCell()
    await cell.create(CreateTask(subject="External", description="Provider-owned details"))

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            assert "task_list" in {tool.name for tool in info.function_tools}
            yield {
                0: DeltaToolCall(
                    name="task_list",
                    json_args="{}",
                    tool_call_id="list-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(WorkingStateCapability(WorkingStateConfiguration(task_mode="provider")),),
    )
    bindings = RunBindings.embedded(
        capabilities=(TaskStateRunCapability(source="provider", cell=cell),),
    )
    events, terminal = await _collect_extensions(executable, "Observe", bindings=bindings)

    assert terminal.result.status == "completed"
    observed = [event for event in _payloads(events, "state") if event["type"] == "task_changed"]
    assert len(observed) == 1
    assert observed[0]["reason"] == "provider_observed"
    assert observed[0]["task_state_version"] == 2
    assert observed[0]["task"] == {
        "id": "task-1",
        "version": 2,
        "subject": "External",
        "active_form": None,
        "status": "pending",
        "owner": None,
        "blocks": [],
        "blocked_by": [],
    }


async def test_provider_changes_before_harness_mutation_keep_provider_observed_reason() -> None:
    cell = EmbeddedTaskStateCell()
    first = await cell.create(CreateTask(subject="First", description="Harness mutation target"))
    second = await cell.create(CreateTask(subject="Second", description="External mutation target"))
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal calls
        del messages, info
        calls += 1
        if calls == 1:
            snapshot = await cell.snapshot()
            await cell.update(
                second.id,
                TaskMutation(subject="Externally changed"),
                snapshot.tasks[second.id].version,
            )
            yield {
                0: DeltaToolCall(
                    name="task_update",
                    json_args=json.dumps({"task_id": first.id, "status": "in_progress"}),
                    tool_call_id="claim-first",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(WorkingStateCapability(WorkingStateConfiguration(task_mode="provider")),),
    )
    bindings = RunBindings.embedded(
        capabilities=(TaskStateRunCapability(source="provider", cell=cell),),
    )
    events, terminal = await _collect_extensions(executable, "Mutate", bindings=bindings)

    assert terminal.result.status == "completed"
    changed = [event for event in _payloads(events, "state") if event["type"] == "task_changed"]
    external = next(event for event in changed if event["task"]["subject"] == "Externally changed")
    claimed = next(event for event in changed if event["task"]["id"] == first.id and event["reason"] == "claimed")
    assert external["reason"] == "provider_observed"
    assert external["operation_id"] != claimed["operation_id"]
