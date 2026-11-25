import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness.mcp_inputs import McpInputResponse, McpTaskInputRequest
from a13n_harness.mcp_tasks import TaskHandle, TaskSnapshot, supervise_mcp_task, task_value, update_mcp_task
from a13n_harness.tool_execution import ToolExecutionRuntime


def handle():
    return TaskHandle.model_validate(
        {
            "resultType": "task",
            "taskId": "task-42",
            "createdAt": datetime.now(UTC),
            "status": "working",
            "ttlMs": 60000,
        }
    ).checkpoint("mcp", "binding", "slow")


@pytest.mark.anyio
async def test_mcp_handle_handoff_releases_foreground_without_cancelling(monkeypatch):
    state = handle()
    runtime = ToolExecutionRuntime(handoff_mcp_tasks=True)
    admitted = asyncio.Event()
    finish_admission = asyncio.Event()

    async def run():
        admitted.set()
        await finish_admission.wait()
        await supervise_mcp_task(None, state)

    async def unexpected(*args):
        pytest.fail("handoff must not poll or cancel")

    monkeypatch.setattr("a13n_harness.mcp_tasks.get_mcp_task", unexpected)
    monkeypatch.setattr("a13n_harness.mcp_tasks.cancel_mcp_task", unexpected)
    execution = runtime.admit(run, thread_id="thread", run_id="run", tool_call_id="call", tool_name="slow")
    await asyncio.wait_for(admitted.wait(), 2)
    await runtime.release_foreground("thread", execution.execution_id)
    finish_admission.set()
    await asyncio.wait_for(runtime.join(), 2)
    saved = await runtime.result("thread", execution.execution_id)
    assert saved.mcp_task == state
    assert saved.record.foreground_released and not saved.record.terminal


@pytest.mark.anyio
async def test_mcp_parked_input_and_update_wire_shape():
    state = handle()
    parked = TaskSnapshot.model_validate(
        {
            "resultType": "complete",
            "taskId": state.mcp_task_id,
            "status": "input_required",
            "inputRequests": {
                "ask": {
                    "method": "elicitation/create",
                    "params": {
                        "mode": "form",
                        "message": "Choose",
                        "requestedSchema": {"type": "object"},
                    },
                }
            },
        }
    ).checkpoint(state)
    assert parked.next_poll_at is None and parked.due_at == state.expires_at
    assert parked.input_required["ask"].schema_ == {"type": "object"}
    assert type(parked).model_validate_json(parked.model_dump_json(by_alias=True)) == parked
    responses = {"ask": {"action": "accept", "content": {}}}

    class Session:
        async def send_request(self, request, result_type, **kwargs):
            assert request.name_param == "taskId"
            assert request.method == "tasks/update"
            assert request.params.model_dump(by_alias=True, exclude_none=True) == {
                "taskId": state.mcp_task_id,
                "inputResponses": responses,
            }
            return result_type()

    resumed, snapshot = await update_mcp_task(Session(), parked, {"ask": McpInputResponse(action="accept", content={})})
    assert snapshot is None
    assert resumed.status == "working" and resumed.input_required is None
    assert resumed.due_at < datetime.now(UTC) + timedelta(seconds=1)
    result = TaskSnapshot.model_validate(
        {
            "resultType": "complete",
            "taskId": state.mcp_task_id,
            "status": "completed",
            "result": {"content": [{"type": "text", "text": "done"}]},
        }
    )
    assert task_value(result.outcome()) == "done"


@pytest.mark.anyio
async def test_mcp_update_can_return_the_terminal_snapshot():
    state = handle().model_copy(
        update={
            "status": "input_required",
            "input_required": {"url": McpTaskInputRequest(mode="url", message="Connect", url="https://example.test/")},
        }
    )

    class Session:
        async def send_request(self, request, result_type, **kwargs):
            return result_type.model_validate(
                {
                    "resultType": "complete",
                    "taskId": state.mcp_task_id,
                    "status": "completed",
                    "result": {"content": [{"type": "text", "text": "done"}]},
                }
            )

    terminal, snapshot = await update_mcp_task(Session(), state, {"url": McpInputResponse(action="accept")})
    assert terminal.status == "completed" and terminal.input_required is None
    assert snapshot is not None and task_value(snapshot.outcome()) == "done"
