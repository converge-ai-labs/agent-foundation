from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from converge_agent_harness import (
    CompactionCapability,
    CompactionPolicy,
    CreateTask,
    DefinitionError,
    EmbeddedTaskStateCell,
    HandoffCapability,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessRunResultEvent,
    HarnessState,
    RunBindings,
    TaskMutation,
    TaskStateRunCapability,
    WorkingStateCapability,
    WorkingStateConfiguration,
)
from converge_agent_harness.capabilities.lifecycle import _safe_error_code
from converge_agent_harness.events import ContextOperationCompletedPayload
from pydantic import ValidationError
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


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
        bindings=bindings or RunBindings.local(),
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


async def test_model_request_lifecycle_events_are_ordered_and_fail_safely() -> None:
    async def success_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    success = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
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

    failing = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
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

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(HandoffCapability(),),
    )
    events, terminal = await _collect_extensions(executable, "Continue")

    assert terminal.result.status == "completed"
    assert _payloads(events, "context") == []


async def test_compaction_events_share_operation_identity_and_snapshot_request_indexes() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal calls
        del messages, info
        calls += 1
        if calls == 1:
            yield {
                0: DeltaToolCall(
                    name="summarize",
                    json_args=json.dumps({"content": "Compacted continuation"}),
                    tool_call_id="compact-1",
                )
            }
        else:
            yield "done"

    previous = HarnessState(
        message_history=(
            ModelRequest(
                parts=[UserPromptPart(content="Original long task")],
                metadata={"converge.context": "compaction", "converge.restored-boundary": "1"},
            ),
            ModelResponse(
                parts=[TextPart(content="Long response")],
                usage=RequestUsage(input_tokens=2_100, output_tokens=100),
            ),
        )
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            HandoffCapability(),
            CompactionCapability(CompactionPolicy(trigger_tokens=2_000, target_tokens=1_000)),
        ),
    )
    events, terminal = await _collect_extensions(executable, "Continue", previous_state=previous)

    assert terminal.result.status == "completed"
    context = _payloads(events, "context")
    assert [event["type"] for event in context] == [
        "context_snapshot",
        "compaction_started",
        "compaction_prepared",
        "context_snapshot",
        "compaction_completed",
    ]
    snapshots = [event for event in context if event["type"] == "context_snapshot"]
    assert [event["request_index"] for event in snapshots] == [0, 1]
    assert [event["compaction_pending"] for event in snapshots] == [False, True]
    operations = [event for event in context if event["type"].startswith("compaction_")]
    operation_ids = {event["operation_id"] for event in operations}
    assert len(operation_ids) == 1
    assert next(iter(operation_ids)).startswith("compaction-")


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

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
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
    assert reciprocal[0]["state_revision"] == reciprocal[1]["state_revision"] == 2
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

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(WorkingStateCapability(WorkingStateConfiguration(task_mode="provider")),),
    )
    bindings = RunBindings.local(
        capabilities=(TaskStateRunCapability(source="provider", cell=cell),),
    )
    events, terminal = await _collect_extensions(executable, "Observe", bindings=bindings)

    assert terminal.result.status == "completed"
    observed = [event for event in _payloads(events, "state") if event["type"] == "task_changed"]
    assert len(observed) == 1
    assert observed[0]["reason"] == "provider_observed"
    assert observed[0]["state_revision"] == 1
    assert observed[0]["task"] == {
        "id": "task-1",
        "revision": 1,
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
                snapshot.tasks[second.id].revision,
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

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(WorkingStateCapability(WorkingStateConfiguration(task_mode="provider")),),
    )
    bindings = RunBindings.local(
        capabilities=(TaskStateRunCapability(source="provider", cell=cell),),
    )
    events, terminal = await _collect_extensions(executable, "Mutate", bindings=bindings)

    assert terminal.result.status == "completed"
    changed = [event for event in _payloads(events, "state") if event["type"] == "task_changed"]
    external = next(event for event in changed if event["task"]["subject"] == "Externally changed")
    claimed = next(event for event in changed if event["task"]["id"] == first.id and event["reason"] == "claimed")
    assert external["reason"] == "provider_observed"
    assert external["operation_id"] != claimed["operation_id"]
