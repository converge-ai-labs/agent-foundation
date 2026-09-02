from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from a13n_harness import (
    DefinitionError,
    HarnessBuilder,
    HarnessState,
    RunBindings,
)
from a13n_harness.capabilities import (
    CreateTask,
    EmbeddedTaskStateCell,
    ProviderTaskCursor,
    TaskMutation,
    TaskStateError,
    TaskStateRunCapability,
    WorkingState,
    WorkingStateCapability,
    WorkingStateConfiguration,
)
from a13n_harness.capabilities.context import _requires_exact_history
from a13n_harness.capabilities.working_state import (
    WORKING_STATE_CAPABILITY_ID,
    _render_working_state_blocks,
)
from a13n_harness.state import (
    AgentContextStateSnapshot,
    CapabilityState,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("tasks_enabled", "notes_enabled"),
    [(True, False), (False, True), (False, False)],
)
async def test_working_state_instructions_follow_enabled_tool_groups(
    tasks_enabled: bool,
    notes_enabled: bool,
) -> None:
    captured: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        captured.append(info)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            WorkingStateCapability(
                WorkingStateConfiguration(
                    tasks_enabled=tasks_enabled,
                    notes_enabled=notes_enabled,
                )
            ),
        ),
    )
    result = await executable.run("inspect", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert len(captured) == 1
    names = {tool.name for tool in captured[0].function_tools}
    instructions = captured[0].instructions or ""
    assert ({"task_create", "task_get", "task_list", "task_update"} <= names) is tasks_enabled
    assert ({"note_write", "note_delete", "note_get"} <= names) is notes_enabled
    assert ('<tool-instruction name="task-manager">' in instructions) is tasks_enabled
    assert ('<tool-instruction name="note">' in instructions) is notes_enabled
    if notes_enabled:
        note_write = next(tool for tool in captured[0].function_tools if tool.name == "note_write")
        assert set(note_write.parameters_json_schema["required"]) == {"key", "value"}


async def test_embedded_task_cell_linearizes_claims_dependencies_and_allocator() -> None:
    changed = []

    async def on_change(state) -> None:
        changed.append(state)

    root = EmbeddedTaskStateCell(owner="root", on_change=on_change)
    child = root.bind("child-1")
    first = await root.create(CreateTask(subject="First", description="First task"))
    second = await root.create(CreateTask(subject="Second", description="Second task", blocked_by=(first.id,)))

    with pytest.raises(TaskStateError) as blocked:
        await child.claim(second.id, second.version)
    assert blocked.value.code == "task_blocked"

    current_first = (await root.snapshot()).tasks[first.id]
    claimed = await root.claim(first.id, current_first.version)
    repeated = await root.claim(first.id, current_first.version)
    assert repeated == claimed
    assert repeated.owner == "root"

    with pytest.raises(TaskStateError) as owner_conflict:
        await child.claim(first.id)
    assert owner_conflict.value.code == "task_owner_conflict"

    completed = await root.update(
        first.id,
        TaskMutation(status="completed"),
        expected_version=claimed.version,
    )
    with pytest.raises(TaskStateError) as cycle:
        await root.update(
            first.id,
            TaskMutation(add_blocked_by=(second.id,)),
            expected_version=completed.version,
        )
    assert cycle.value.code == "task_dependency_invalid"

    child_claim = await child.claim(second.id, second.version)
    assert child_claim.owner == "child-1"
    assert child_claim.status == "in_progress"

    third = await root.create(CreateTask(subject="Third", description="Third task"))
    assert third.id == "task-3"
    snapshot = await root.snapshot()
    detached = snapshot.tasks
    detached.pop(first.id)
    assert first.id in (await root.snapshot()).tasks
    assert changed[-1].version == (await root.snapshot()).version


async def test_embedded_task_cell_mutation_is_atomic_and_owner_bound() -> None:
    fail_changes = False

    async def on_change(state) -> None:
        del state
        if fail_changes:
            raise RuntimeError("checkpoint unavailable")

    root = EmbeddedTaskStateCell(owner="root", on_change=on_change)
    child = root.bind("child")
    first = await root.create(CreateTask(subject="First", description="First task"))
    second = await root.create(CreateTask(subject="Second", description="Second task", blocked_by=(first.id,)))
    before_invalid = await root.snapshot()

    with pytest.raises(TaskStateError) as invalid:
        await root.mutate(
            first.id,
            TaskMutation(status="completed", add_blocked_by=(second.id,)),
            before_invalid.tasks[first.id].version,
            claim=True,
        )
    assert invalid.value.code == "task_dependency_invalid"
    assert await root.snapshot() == before_invalid

    claimed = await root.claim(first.id, before_invalid.tasks[first.id].version)
    before_owner_conflict = await root.snapshot()
    with pytest.raises(TaskStateError) as owner_conflict:
        await child.update(
            first.id,
            TaskMutation(description="cross-owner mutation"),
            claimed.version,
        )
    assert owner_conflict.value.code == "task_owner_conflict"
    assert await root.snapshot() == before_owner_conflict

    fail_changes = True
    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        await root.mutate(
            first.id,
            TaskMutation(status="completed", description="must roll back"),
            claimed.version,
            claim=True,
        )
    assert await root.snapshot() == before_owner_conflict


async def test_embedded_task_cell_rolls_back_when_state_observer_fails() -> None:
    calls = 0

    async def fail_once(state) -> None:
        nonlocal calls
        del state
        calls += 1
        raise RuntimeError("checkpoint unavailable")

    cell = EmbeddedTaskStateCell(on_change=fail_once)
    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        await cell.create(CreateTask(subject="First", description="First task"))

    snapshot = await cell.snapshot()
    assert snapshot.version == 1
    assert snapshot.next_task_sequence == 1
    assert snapshot.tasks == {}
    assert calls == 1


async def test_working_state_tools_persist_and_refresh_bounded_context() -> None:
    calls: list[tuple[list[ModelMessage], AgentInfo]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        calls.append((messages, info))
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            task_update = next(tool for tool in info.function_tools if tool.name == "task_update")
            assert "owner" not in task_update.parameters_json_schema["properties"]
            assert "expected_version" not in task_update.parameters_json_schema["properties"]
            assert "clear_active_form" not in task_update.parameters_json_schema["properties"]
            assert "task_claim" not in {tool.name for tool in info.function_tools}
            yield {
                0: DeltaToolCall(
                    name="task_create",
                    json_args=json.dumps(
                        {
                            "subject": "Review <unsafe>",
                            "description": "Review the implementation",
                            "active_form": "Reviewing",
                        }
                    ),
                    tool_call_id="task-create-1",
                )
            }
            return
        result = returns[-1].content
        assert isinstance(result, dict)
        if len(returns) == 1:
            task = result["task"]
            yield {
                0: DeltaToolCall(
                    name="task_update",
                    json_args=json.dumps({"task_id": task["id"], "status": "in_progress"}),
                    tool_call_id="task-start-1",
                )
            }
        elif len(returns) == 2:
            task = result["task"]
            yield {
                0: DeltaToolCall(
                    name="task_update",
                    json_args=json.dumps(
                        {
                            "task_id": task["id"],
                            "status": "completed",
                        }
                    ),
                    tool_call_id="task-update-1",
                )
            }
        elif len(returns) == 3:
            yield {
                0: DeltaToolCall(
                    name="note_write",
                    json_args=json.dumps({"key": "review<&", "value": "Remember details"}),
                    tool_call_id="note-write-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(WorkingStateCapability(),),
    )
    first = await executable.run("Coordinate work", bindings=RunBindings.embedded())

    assert first.output_or_raise() == "done"
    assert first.state is not None
    state_data = first.state.agent_context_state.entries[WORKING_STATE_CAPABILITY_ID].data
    restored = WorkingState.model_validate(state_data)
    task = restored.tasks.tasks["task-1"] if restored.tasks is not None else None
    assert task is not None
    assert task.status == "completed"
    assert task.owner == "root"
    assert restored.notes == {"review<&": "Remember details"}

    resumed_messages: list[ModelMessage] = []

    async def resume_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        resumed_messages.extend(messages)
        yield "resumed"

    resumed = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=resume_stream),
        capabilities=(WorkingStateCapability(),),
    )
    result = await resumed.run(
        "Continue",
        bindings=RunBindings.embedded(),
        previous_state=first.state,
    )

    assert result.output_or_raise() == "resumed"
    historical_context = "\n".join(
        part.content
        for message in resumed_messages[:-1]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )
    latest_request = resumed_messages[-1]
    assert isinstance(latest_request, ModelRequest)
    current_context = "\n".join(
        part.content
        for part in latest_request.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )
    assert '<task id="task-1"' in historical_context
    assert "Review &lt;unsafe&gt;" in historical_context
    assert '<task id="task-1"' not in current_context
    assert "Review &lt;unsafe&gt;" not in current_context
    assert '<notes source="a13n-harness">' in current_context
    assert '<note key="review&lt;&amp;">Remember details</note>' in current_context
    assert "<note-ref" not in current_context


async def test_note_tools_report_semantic_mutation_actions_and_list_count() -> None:
    observed: list[dict[str, Any]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        observed[:] = returns
        calls = (
            ("note_write", {"key": "decision", "value": "first"}),
            ("note_write", {"key": "decision", "value": "second"}),
            ("note_delete", {"key": "decision"}),
            ("note_delete", {"key": "decision"}),
            ("note_get", {}),
        )
        if len(returns) < len(calls):
            name, arguments = calls[len(returns)]
            yield {
                0: DeltaToolCall(
                    name=name,
                    json_args=json.dumps(arguments),
                    tool_call_id=f"note-action-{len(returns)}",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(WorkingStateCapability(),),
    )
    result = await executable.run("Track a decision", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert [entry.get("action") for entry in observed[:4]] == [
        "created",
        "updated",
        "deleted",
        "already_absent",
    ]
    assert observed[-1] == {"ok": True, "keys": [], "count": 0}


async def test_pending_task_non_status_mutation_atomically_claims_owner() -> None:
    observed: list[dict[str, Any]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        observed[:] = returns
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="task_create",
                    json_args=json.dumps({"subject": "Pending", "description": "Before"}),
                    tool_call_id="task-create-pending",
                )
            }
        elif len(returns) == 1:
            yield {
                0: DeltaToolCall(
                    name="task_update",
                    json_args=json.dumps({"task_id": "task-1", "description": "After"}),
                    tool_call_id="task-update-description",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(WorkingStateCapability(),),
    )
    result = await executable.run("Coordinate", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert observed[-1]["task"]["description"] == "After"
    assert observed[-1]["task"]["status"] == "in_progress"
    assert observed[-1]["task"]["owner"] == "root"


async def test_working_state_context_has_hard_utf8_budget() -> None:
    cell = EmbeddedTaskStateCell()
    for index in range(12):
        await cell.create(
            CreateTask(
                subject=f"{index}-" + "😀" * 500,
                description="Large projected task",
            )
        )
    seen: list[ModelMessage] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.extend(messages)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(WorkingStateCapability(WorkingStateConfiguration(task_mode="provider", max_context_bytes=1024)),),
    )
    result = await executable.run(
        "Continue",
        bindings=RunBindings.embedded(capabilities=(TaskStateRunCapability(source="provider", cell=cell),)),
    )

    assert result.output_or_raise() == "done"
    projected = next(
        part.content
        for message in seen
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str) and part.content.startswith("<tasks")
    )
    assert len(projected.encode("utf-8")) <= 1024
    assert "tasks-omitted" in projected
    assert "version=" not in projected


async def test_working_state_projects_complete_notes_refs_and_omission_counts_separately() -> None:
    cell = EmbeddedTaskStateCell()
    await cell.create(CreateTask(subject="Active", description="Keep tasks last"))
    snapshot = await cell.snapshot()
    notes = {
        "a-large": "<" + "😀" * 1_000,
        "b-small": "visible & complete",
        "c-omitted": "not selected",
    }

    blocks = _render_working_state_blocks(
        tuple(snapshot.tasks.values()),
        snapshot.tasks,
        notes,
        WorkingStateConfiguration(max_context_notes=2, max_context_bytes=1024),
    )

    assert len(blocks) == 2
    assert blocks[0].source_id.endswith(".notes")
    assert blocks[1].source_id.endswith(".tasks")
    assert '<note-ref key="a-large" />' in blocks[0].content
    assert "😀" not in blocks[0].content
    assert '<note key="b-small">visible &amp; complete</note>' in blocks[0].content
    assert '<notes-omitted count="1" />' in blocks[0].content
    assert '<task id="task-1"' in blocks[1].content
    assert sum(len(block.content.encode("utf-8")) for block in blocks) <= 1024

    task_only = _render_working_state_blocks(
        tuple(snapshot.tasks.values()),
        snapshot.tasks,
        {},
        WorkingStateConfiguration(max_context_bytes=1024),
    )
    assert len(task_only) == 1
    assert task_only[0].source_id.endswith(".tasks")


async def test_task_list_and_task_results_hide_internal_versions() -> None:
    observed: list[dict[str, Any]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        observed[:] = returns
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="task_create",
                    json_args=json.dumps({"subject": "Visible", "description": "No CAS details"}),
                    tool_call_id="task-create-visible",
                )
            }
        elif len(returns) == 1:
            yield {
                0: DeltaToolCall(
                    name="task_list",
                    json_args="{}",
                    tool_call_id="task-list-visible",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(WorkingStateCapability(),),
    )
    result = await executable.run("Coordinate", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert "version" not in observed[0]["task"]
    assert "version" not in observed[1]
    assert "version" not in observed[1]["tasks"][0]


async def test_working_state_tools_normalize_internal_validation_errors() -> None:
    results: list[dict[str, object]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        results[:] = returns
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="task_create",
                    json_args=json.dumps({"subject": "   ", "description": "invalid"}),
                    tool_call_id="invalid-create",
                )
            }
        elif len(returns) == 1:
            assert returns[-1]["error"]["code"] == "task_request_invalid"
            yield {
                0: DeltaToolCall(
                    name="task_create",
                    json_args=json.dumps({"subject": "Valid", "description": "valid"}),
                    tool_call_id="valid-create",
                )
            }
        elif len(returns) == 2:
            task = returns[-1]["task"]
            yield {
                0: DeltaToolCall(
                    name="task_update",
                    json_args=json.dumps(
                        {
                            "task_id": task["id"],
                            "add_blocked_by": [task["id"]],
                        }
                    ),
                    tool_call_id="invalid-update",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(WorkingStateCapability(),),
    )
    result = await executable.run("Coordinate", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert results[0]["error"]["code"] == "task_request_invalid"
    assert results[-1]["error"]["code"] == "task_dependency_invalid"


async def test_provider_mode_requires_fresh_binding_and_discards_mismatched_cursor() -> None:
    capability = WorkingStateCapability(WorkingStateConfiguration(task_mode="provider"))
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(capability,),
    )
    previous = HarnessState.new(
        agent_context_state=AgentContextStateSnapshot(
            entries={
                WORKING_STATE_CAPABILITY_ID: CapabilityState(
                    version="1",
                    data=WorkingState(
                        task_mode="provider",
                        provider_cursor=ProviderTaskCursor(
                            provider_type="old-provider",
                            state_version="old-version",
                            observed_version=9,
                        ),
                    ).model_dump(mode="json"),
                )
            }
        )
    )

    with pytest.raises(DefinitionError) as missing:
        await executable.run(
            "Continue",
            bindings=RunBindings.embedded(),
            previous_state=previous,
        )
    assert missing.value.code == "task_state_binding_missing"

    result = await executable.run(
        "Continue",
        bindings=RunBindings.embedded(
            capabilities=(
                TaskStateRunCapability(
                    source="provider",
                    cell=EmbeddedTaskStateCell(),
                    provider_type="current-provider",
                    state_version="2",
                ),
            )
        ),
        previous_state=previous,
    )
    assert result.state is not None
    restored = WorkingState.model_validate(result.state.agent_context_state.entries[WORKING_STATE_CAPABILITY_ID].data)
    assert restored.provider_cursor == ProviderTaskCursor(
        provider_type="current-provider",
        state_version="2",
        observed_version=1,
    )


async def test_provider_mode_without_task_surface_does_not_require_task_binding() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(WorkingStateCapability(WorkingStateConfiguration(task_mode="provider", tasks_enabled=False)),),
    )

    result = await executable.run("Continue", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"


async def test_task_attachment_without_working_state_owner_fails_closed() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run(
            "Continue",
            bindings=RunBindings.embedded(
                capabilities=(TaskStateRunCapability(source="provider", cell=EmbeddedTaskStateCell()),)
            ),
        )
    assert exc_info.value.code == "task_state_owner_missing"


async def test_working_state_exact_continuation_detection_matches_provider_boundaries() -> None:
    suspended = [
        ModelRequest(parts=[UserPromptPart("start")]),
        ModelResponse(parts=[TextPart("partial")], state="suspended"),
    ]
    pending = [
        ModelRequest(parts=[UserPromptPart("start")]),
        ModelResponse(parts=[ToolCallPart(tool_name="external", args={}, tool_call_id="call-1")]),
    ]
    integrated = [
        *pending,
        ModelRequest(parts=[ToolReturnPart(tool_name="external", content="done", tool_call_id="call-1")]),
    ]

    assert _requires_exact_history(suspended)
    assert _requires_exact_history(pending)
    assert not _requires_exact_history(integrated)


async def _text(value: str) -> AsyncIterator[str]:
    yield value
