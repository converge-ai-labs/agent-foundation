from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness import input_source
from a13n_harness.envelopes import Envelope, ExecutionRef, Sender
from anyio import Event, create_task_group, fail_after

pytestmark = pytest.mark.anyio


async def test_admission_fences_isolate_conversations_and_release_cancelled_waiters():
    from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator, _SubagentRequestError
    from anyio import CancelScope

    operator = HarnessUiSubagentOperator(
        store=None,
        configurations=None,
        compositions=None,
        agent_reconstructor=None,
        environment_service=None,
    )
    operator._require_started_locked = lambda: None

    def scope(root):
        return SimpleNamespace(
            root_thread_id=root, composition=SimpleNamespace(subagents=SimpleNamespace(max_concurrent=1))
        )

    entered, release, first_done, waiter_started, waiter_done, other_entered = (Event() for _ in range(6))
    waiting = CancelScope()

    async def first():
        async with operator._admission(scope("root-a")):
            entered.set()
            await release.wait()
            operator._active["child"] = SimpleNamespace(root_thread_id="root-a")
        first_done.set()

    async def waiter():
        with waiting:
            waiter_started.set()
            async with operator._admission(scope("root-a")):
                pytest.fail("The same conversation admitted overlapping preparation")
        waiter_done.set()

    async def other():
        async with operator._admission(scope("root-b")):
            other_entered.set()

    async with create_task_group() as group:
        with fail_after(5):
            group.start_soon(first)
            await entered.wait()
            group.start_soon(waiter)
            await waiter_started.wait()
            fence = operator._admission_locks["root-a"]
            group.start_soon(other)
            await other_entered.wait()  # root-b does not wait for root-a's preparation.
            waiting.cancel()
            await waiter_done.wait()
            assert operator._admission_locks["root-a"] is fence
            release.set()
            await first_done.wait()
    assert set(operator._admission_locks) == {"root-a", "root-b"}
    with pytest.raises(_SubagentRequestError, match="max_concurrent"):
        async with operator._admission(scope("root-a")):
            pytest.fail("The conversation's registered child must count against its limit")
    assert set(operator._admission_locks) == {"root-a", "root-b"}


async def test_model_steer_into_child_is_marked_parent_agent():
    from a13n_harness.capabilities import SubagentOperatorContext, SubagentSteerRequest, SubagentSteerResult
    from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator

    operator = HarnessUiSubagentOperator(
        store=None,
        configurations=None,
        compositions=None,
        agent_reconstructor=None,
        environment_service=None,
    )
    operator._require_parent = AsyncMock(return_value=SimpleNamespace(thread_id="parent"))
    operator.steer_execution = AsyncMock(return_value=SubagentSteerResult(execution_id="sa_1", accepted=True))
    await operator.steer(
        SubagentOperatorContext(
            parent_thread_id="parent", parent_run_id="run", parent_agent_instance_id="agent", host_refs={}
        ),
        SubagentSteerRequest(execution_id="sa_1", message="Focus here"),
    )
    (item,) = operator.steer_execution.call_args.kwargs["message"]
    assert item.value == "Focus here"
    assert input_source(item.metadata).kind == "agent_message"


@pytest.mark.parametrize("source", ["async_subagent", "tool_execution"])
async def test_displayable_host_notices_are_excluded_from_composer_recall(source):
    from a13n_harness.content import ContentItem, ContentMetadata
    from a13n_harness_ui.conversation import input_excerpt

    notice = ContentItem("Background result", ContentMetadata.model_validate({"a13n.steering-source": source}))
    assert notice.metadata.display
    assert input_excerpt((notice,)) == ""
    assert input_excerpt((notice, ContentItem("Human task"))) == "Human task"


@pytest.mark.usefixtures("migrated_databases")
async def test_user_stop_delivers_cancelled_child_result_to_parent(tmp_path, monkeypatch):
    from pydantic_ai.models.function import DeltaToolCall

    from .test_dynamic_subagents import new_thread, open_app, returned

    started, notified = Event(), Event()
    inputs = []

    async def model(messages, info):
        if "spawn_subagent" not in {tool.name for tool in info.function_tools}:
            started.set()
            await Event().wait()
        elif "Subagent result · worker" in str(messages):
            inputs.append(str(messages))
            notified.set()
            yield "Child stopped"
        elif not returned(messages):
            yield {
                0: DeltaToolCall(
                    name="spawn_subagent", json_args='{"name":"worker","instructions":"Work","prompt":"Work"}'
                )
            }
        else:
            yield "Work is running"

    async with await open_app(tmp_path, monkeypatch, model) as app:
        parent = await new_thread(app)
        receipt = await app.submit_thread(thread_id=parent.thread_id, prompt="Start child")
        await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        with fail_after(10):
            await started.wait()
        (head,) = (await app.query_child_executions(parent_thread_id=parent.thread_id)).executions
        assert (
            await app.cancel_child_execution(parent_thread_id=parent.thread_id, execution_id=head.execution_id)
        ).accepted
        with fail_after(10):
            await notified.wait()
        assert f"Subagent result · worker · execution_id={head.execution_id} · cancelled" in inputs[-1]


@pytest.mark.usefixtures("migrated_databases")
async def test_long_child_result_stays_inline_when_it_wakes_idle_parent(tmp_path, monkeypatch):
    from a13n_harness.content import request_input_content
    from a13n_harness_ui.surfaces import RootOperationStatus
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart

    from .test_long_text_input import _app, _function_model

    notified = Event()
    text = (
        "Subagent result · worker · execution_id=execution-child · succeeded"
        "\nReply with resume_subagent.\n\nChild result\n" + "Full answer detail. " * 2000
    )
    notices = []

    def model(messages, info):
        notices.extend(
            item
            for request in messages
            if isinstance(request, ModelRequest)
            for item in request_input_content(request)
            if (origin := input_source(item.metadata)) is not None and origin.kind == "result"
        )
        if notices:
            notified.set()
        return ModelResponse(parts=[TextPart("Received")])

    async with _app(tmp_path, monkeypatch, _function_model(model)) as (app, _, _):
        retain = AsyncMock(wraps=app._thread_files.retain)
        monkeypatch.setattr(app._thread_files, "retain", retain)
        parent = await app.create_thread()
        receipt = await app.submit_thread(thread_id=parent.thread_id, prompt="Start")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        await app._inbox.deliver(
            parent.thread_id,
            Envelope(
                id="child-result",
                kind="result",
                sender=Sender(kind="agent"),
                execution=ExecutionRef(execution_id="execution-child", kind="agent", name="worker", state="succeeded"),
                text=text,
            ),
        )
        with fail_after(10):
            await notified.wait()
        assert len(notices) == 1
        assert text in notices[0].value
        retain.assert_not_awaited()
