from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.model_context import (
    ModelContextHistory,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
    ModelContextRequestKind,
)
from a13n_harness_ui.app import AppState, HarnessUiApp
from a13n_harness_ui.subagent_tools import SubagentTools
from a13n_harness_ui.tool_executions import ToolExecutions
from anyio import Event, create_task_group, fail_after

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("kind", ["Subagent message", "Subagent result"])
@pytest.mark.parametrize(
    "status,archived,decision",
    [
        ("cancelled", False, False),
        ("failed", False, False),
        ("suspended", False, False),
        ("unknown", False, False),
        ("completed", True, False),
        ("completed", False, True),
    ],
)
async def test_subagent_delivery_does_not_wake_ineligible_parent(status, archived, decision, kind):
    app = object.__new__(HarnessUiApp)
    app._state = AppState.ready
    thread = SimpleNamespace(
        archived=archived, memory_scope=None, parent_thread_id=None, last_execution=SimpleNamespace(status=status)
    )
    app._projections = SimpleNamespace(
        detail=AsyncMock(
            return_value=SimpleNamespace(thread=thread, deferred_requests=("pending",) if decision else ())
        )
    )
    app._root_runs = SimpleNamespace(active=AsyncMock(return_value=None), submit_prompt=AsyncMock())
    assert not await app._deliver_subagent_input(
        "thread-parent", f"{kind} · child · execution_id=execution-1 · succeeded", ("execution-1",)
    )
    app._root_runs.submit_prompt.assert_not_awaited()


async def test_active_delivery_keeps_displayable_source_and_execution_references():
    app = object.__new__(HarnessUiApp)
    app._state = AppState.ready
    app._projections = SimpleNamespace(
        detail=AsyncMock(
            return_value=SimpleNamespace(
                thread=SimpleNamespace(archived=False, memory_scope=None, parent_thread_id=None), deferred_requests=()
            )
        )
    )
    steer = AsyncMock(return_value=SimpleNamespace(accepted=True))
    app._root_runs = SimpleNamespace(
        active=AsyncMock(return_value=SimpleNamespace(receipt=SimpleNamespace(receipt_id="receipt-parent"))),
        steer=steer,
    )
    assert await app._deliver_subagent_input(
        "thread-parent", "Subagent message · role · execution_id=sa_1 · running", ("sa_1",)
    )
    call = steer.call_args.kwargs
    assert call["receipt_id"] == "receipt-parent" and not call["immediate"]
    item = call["message"][0]
    assert item.metadata.display
    assert item.metadata.model_extra["a13n.steering-source"] == "async_subagent"
    assert item.metadata.model_extra["execution_ids"] == ["sa_1"]


async def test_child_inputs_coalesce_and_retry_in_existing_dispatch_lane():
    owner = ToolExecutions()
    delivered, first_offer = Event(), Event()
    ready = False
    offers = []

    async def deliver(thread_id, text, ids):
        offers.append((thread_id, text, ids))
        if ready:
            delivered.set()
        else:
            first_offer.set()
        return ready

    owner.deliver_subagent = deliver
    async with create_task_group() as group:
        owner.start(group)
        owner.offer_subagent_input("parent", "one", ("sa_1",))
        owner.offer_subagent_input("parent", "two", ("sa_2",))
        with fail_after(5):
            await first_offer.wait()
            ready = True
            owner.retry("parent")
            await delivered.wait()
        assert offers[-1] == ("parent", "one\n\ntwo", ("sa_1", "sa_2"))
        owner.stop()
    await owner.runtime.close()


async def test_reminder_is_bounded_and_appends_on_change():
    rows = [{"name": f"role-{i}", "execution_id": f"sa_{i}", "state": "running"} for i in range(21)]
    operator = SimpleNamespace(reminder=AsyncMock(return_value=rows))
    composition = SimpleNamespace(
        root=SimpleNamespace(children=("child",)),
        subagents=SimpleNamespace(dynamic=SimpleNamespace(enabled=False)),
    )
    capability = SubagentTools(operator, composition, subagent=False, has_children=True)
    ctx = SimpleNamespace(deps=SimpleNamespace(thread_id="parent"))

    async def handler(request):
        return ModelContextProjection()

    request = ModelContextProjectionRequest(kind=ModelContextRequestKind.TOOL_RESULTS)
    first = await capability.wrap_model_context(ctx, request, handler)
    block = first.blocks[0]
    assert block.context_group == "runtime_state" and block.placement is ModelContextPlacement.REQUEST_EPILOGUE
    assert '"execution_id": "sa_19"' in block.content and '"execution_id": "sa_20"' not in block.content
    assert "before reporting completion" in block.content
    assert "arrive automatically" in block.content
    unchanged = ModelContextProjectionRequest(kind=request.kind, history=ModelContextHistory.from_blocks((block,)))
    assert not (await capability.wrap_model_context(ctx, unchanged, handler)).blocks
    operator.reminder.return_value = []
    emptied = await capability.wrap_model_context(ctx, unchanged, handler)
    assert not emptied.blocks
    assert not (await capability.wrap_model_context(ctx, request, handler)).blocks


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
    assert item.metadata.model_extra["a13n.steering-source"] == "parent_agent"


@pytest.mark.parametrize("kind", ["Subagent message", "Subagent result"])
@pytest.mark.parametrize("active", [False, True])
async def test_grandchild_delivery_steers_active_child_and_consumes_idle_wake(kind, active):
    from a13n_harness.capabilities import SubagentSteerResult
    from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator

    head = SimpleNamespace(parent_thread_id="root", execution_id="child-execution")
    operator = HarnessUiSubagentOperator(
        store=SimpleNamespace(child_executions=SimpleNamespace(latest_for_child=AsyncMock(return_value=head))),
        configurations=None,
        compositions=None,
        agent_reconstructor=None,
        environment_service=None,
    )
    operator._accepting = True
    operator._active_segment = AsyncMock(return_value=object() if active else None)
    operator.steer_execution = AsyncMock(
        return_value=SubagentSteerResult(execution_id="child-execution", accepted=True)
    )
    operator._resume_child = AsyncMock()
    operator._store.threads = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(parent_thread_id="root")))
    operator._tool_executions = SimpleNamespace(offer_subagent_input=AsyncMock())
    prompt = f"{kind} · explorer · execution_id=grandchild-execution · running"
    await operator._deliver_parent("child-thread", prompt, ("grandchild-execution",))
    if active:
        call = operator.steer_execution.call_args.kwargs
        assert call["execution_id"] == "child-execution" and call["parent_thread_id"] == "root"
        (item,) = call["message"]
        assert item.value == prompt
        assert item.metadata.model_extra["a13n.steering-source"] == "async_subagent"
        assert item.metadata.model_extra["execution_ids"] == ["grandchild-execution"]
    else:
        operator.steer_execution.assert_not_awaited()
    operator._resume_child.assert_not_awaited()
    operator._tool_executions.offer_subagent_input.assert_not_called()


@pytest.mark.parametrize("source", ["async_subagent", "tool_execution"])
async def test_displayable_host_notices_are_excluded_from_composer_recall(source):
    from a13n_harness.content import ContentItem, ContentMetadata
    from a13n_harness_ui.conversation import input_excerpt

    notice = ContentItem("Background result", ContentMetadata.model_validate({"a13n.steering-source": source}))
    assert notice.metadata.display
    assert input_excerpt((notice,)) == ""
    assert input_excerpt((notice, ContentItem("Human task"))) == "Human task"


@pytest.mark.usefixtures("migrated_databases")
async def test_terminal_result_offer_precedes_child_settlement(tmp_path, monkeypatch):
    from pydantic_ai.models.function import DeltaToolCall

    from .test_dynamic_subagents import new_thread, open_app, returned

    offering, release = Event(), Event()

    async def model(messages, info):
        if "send_parent_message" in {tool.name for tool in info.function_tools}:
            yield "Child result"
        elif not returned(messages):
            yield {
                0: DeltaToolCall(
                    name="spawn_subagent",
                    json_args='{"name":"worker","instructions":"Child task","prompt":"Work"}',
                )
            }
        else:
            yield "Parent finished"

    async with await open_app(tmp_path, monkeypatch, model) as app:
        original = app._subagent_operator._deliver_parent

        async def offer(thread_id, prompt, execution_ids):
            offering.set()
            await release.wait()
            await original(thread_id, prompt, execution_ids)

        monkeypatch.setattr(app._subagent_operator, "_deliver_parent", offer)
        parent = await new_thread(app)
        try:
            receipt = await app.submit_thread(thread_id=parent.thread_id, prompt="Start child")
            await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
            with fail_after(10):
                await offering.wait()
            (head,) = (await app.query_child_executions(parent_thread_id=parent.thread_id)).executions
            assert head.persisted_status == "succeeded"
            assert head.execution_id in await app._subagent_operator.active_execution_ids()
            active = app._subagent_operator._active[head.execution_id]
            assert not active.done.is_set()
            stored = await app._store.child_executions.get(head.execution_id)
            assert stored is not None
            checkpoint = await app._store.read_child_checkpoint(stored)
            assert checkpoint.display.final_answer == "Child result"
            assert active.display.final_answer is None
            release.set()
            with fail_after(10):
                await active.done.wait()
            assert head.execution_id not in await app._subagent_operator.active_execution_ids()
        finally:
            release.set()


@pytest.mark.usefixtures("migrated_databases")
async def test_result_offer_preparation_failure_settles_child_and_keeps_operator_available(tmp_path, monkeypatch):
    from anyio import sleep
    from pydantic_ai.models.function import DeltaToolCall

    from .test_dynamic_subagents import new_thread, open_app, returned

    ready, release = Event(), Event()

    async def model(messages, info):
        if "send_parent_message" in {tool.name for tool in info.function_tools}:
            ready.set()
            await release.wait()
            yield "Child result"
        elif not returned(messages):
            yield {
                0: DeltaToolCall(
                    name="spawn_subagent", json_args='{"name":"worker","instructions":"Work","prompt":"Work"}'
                )
            }
        else:
            yield "Parent finished"

    async with await open_app(tmp_path, monkeypatch, model) as app:
        operator = app._subagent_operator
        monkeypatch.setattr(
            operator, "_read_checkpoint", AsyncMock(side_effect=RuntimeError("Offer preparation failed"))
        )
        parent = await new_thread(app)
        receipt = await app.submit_thread(thread_id=parent.thread_id, prompt="Start child")
        await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        with fail_after(10):
            await ready.wait()
        (execution_id,) = await operator.active_execution_ids()
        active = operator._active[execution_id]
        release.set()
        with fail_after(10):
            await active.done.wait()
        assert execution_id not in await operator.active_execution_ids()
        parent = await new_thread(app)
        receipt = await app.submit_thread(thread_id=parent.thread_id, prompt="Start another child")
        await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        with fail_after(10):
            while await operator.active_execution_ids():
                await sleep(0.01)
        (head,) = (await app.query_child_executions(parent_thread_id=parent.thread_id)).executions
        assert head.persisted_status == "succeeded"


@pytest.mark.usefixtures("migrated_databases")
async def test_user_stop_delivers_cancelled_child_result_to_parent(tmp_path, monkeypatch):
    from pydantic_ai.models.function import DeltaToolCall

    from .test_dynamic_subagents import new_thread, open_app, returned

    started, notified = Event(), Event()
    inputs = []

    async def model(messages, info):
        if "send_parent_message" in {tool.name for tool in info.function_tools}:
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
