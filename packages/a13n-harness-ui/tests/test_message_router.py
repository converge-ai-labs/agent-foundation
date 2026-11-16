"""Host message routing preserves scope and records acceptance, not target reads."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness import input_source
from a13n_harness.capabilities import SubagentOperatorContext
from a13n_harness.content import request_input_content
from a13n_harness.envelopes import MessageRouter
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus
from anyio import Event, fail_after
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings
from .test_builtin_subagents import _source

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


@pytest.fixture(autouse=True)
async def bounded_test(anyio_backend):
    with fail_after(30):
        yield


async def test_local_cli_binds_router_for_root_and_child_and_retains_child_output(tmp_path, monkeypatch):
    import json

    path = _source(tmp_path, ("explorer",))
    received = Event()
    observations, receipts = [], []

    async def resolve(self, context, model_id):
        child = context.deps.instance.delegation_id is not None
        observations.append((child, isinstance(context.deps.message_router, MessageRouter)))

        async def model(messages, info):
            returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            if child:
                if not returned:
                    yield {
                        0: DeltaToolCall(
                            name="send_message", json_args=json.dumps({"to": "parent", "message": "Verified progress"})
                        )
                    }
                else:
                    receipts.append(returned[-1].content)
                    yield "Child final answer"
            else:
                for message in messages:
                    for part in request_input_content(message):
                        origin = input_source(part.metadata)
                        if origin is not None and origin.kind == "agent_message" and origin.execution is not None:
                            assert origin.execution.name == "explorer" and origin.execution.state == "running"
                            assert "Verified progress" in part.value
                            received.set()
                if not returned and not received.is_set():
                    yield {0: DeltaToolCall(name="delegate", json_args='{"subagent_name":"explorer","prompt":"Work"}')}
                else:
                    yield "Parent integrated"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path) as app:
        parent = await app.create_thread(defaults=NewThreadDefaults(agent_id="agent-main"))
        receipt = await app.submit_thread(thread_id=parent.thread_id, prompt="Delegate")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        await received.wait()
        await app.wait_child_executions(parent_thread_id=parent.thread_id, timeout_seconds=10)
        assert set(receipts[0]) == {"accepted", "input_id"} and receipts[0]["accepted"]
        assert observations and all(bound for _, bound in observations) and any(child for child, _ in observations)
        views = await app._subagent_operator.inspect_executions(parent_thread_id=parent.thread_id)
        assert views.executions[0].output == "Child final answer"


@pytest.mark.parametrize("change", ["root", "thread", "run", "stopping", "saved_only"])
async def test_parent_requires_the_exact_locally_running_child(change):
    head = SimpleNamespace(
        execution_id="child-execution",
        child_thread_id="thread_child",
        parent_thread_id="thread_parent",
        status="running",
    )
    operator = HarnessUiSubagentOperator(
        store=SimpleNamespace(child_executions=SimpleNamespace(get=AsyncMock(return_value=head))),
        configurations=None,
        compositions=None,
        agent_reconstructor=None,
        environment_service=None,
    )
    active = SimpleNamespace(status="running", stream=SimpleNamespace(run_id="run-child"))
    operator._active_segment = AsyncMock(return_value=None if change == "saved_only" else active)
    operator.inbox = SimpleNamespace(deliver=AsyncMock())
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            thread_id="thread_other" if change == "thread" else "thread_child",
            run_id="run-other" if change == "run" else "run-child",
            instance=SimpleNamespace(delegation_id=None if change == "root" else "child-execution"),
        )
    )
    if change == "stopping":
        active.status = "cancelling"
    with pytest.raises(ToolFailed, match="not a running child"):
        await operator.message_parent(ctx, "Progress")
    operator.inbox.deliver.assert_not_awaited()


async def test_attention_uses_authorized_parent_and_requested_bound():
    repository = SimpleNamespace(
        attention_for_parent=AsyncMock(
            return_value=(
                SimpleNamespace(execution_id="sa-running", status="running"),
                SimpleNamespace(execution_id="sa-newest", status="succeeded"),
            )
        )
    )
    operator = HarnessUiSubagentOperator(
        store=SimpleNamespace(child_executions=repository),
        configurations=None,
        compositions=None,
        agent_reconstructor=None,
        environment_service=None,
    )
    operator._require_parent = AsyncMock(return_value=SimpleNamespace(thread_id="thread_parent"))
    operator._execution_identity = AsyncMock(return_value=SimpleNamespace(name="reviewer"))
    context = SubagentOperatorContext(
        parent_thread_id="thread_parent",
        parent_run_id="run-parent",
        parent_agent_instance_id="agent-parent",
        host_refs={},
    )
    rows = await operator.attention(context, limit=2)
    repository.attention_for_parent.assert_awaited_once_with("thread_parent", limit=2)
    assert [(row.execution_id, row.kind, row.name, row.state) for row in rows] == [
        ("sa-running", "agent", "reviewer", "running"),
        ("sa-newest", "agent", "reviewer", "succeeded"),
    ]
