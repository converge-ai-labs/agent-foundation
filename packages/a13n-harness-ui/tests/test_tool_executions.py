"""App-held execution lifetime, completion input and steering integration."""

from __future__ import annotations

import json

import pytest
from a13n_harness import StoredToolExecution, ToolExecutionRecord
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import AgentReconstructor
from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import RootOperationStatus
from a13n_harness_ui.tool_executions import ToolExecutions
from anyio import Event, create_task_group, fail_after
from pydantic_ai import FunctionToolset, Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


async def complete(owner, execution_id, *, detached=True, delivered=False):
    record = ToolExecutionRecord(
        execution_id=execution_id,
        thread_id="thread-a",
        run_id="run-a",
        tool_call_id=execution_id,
        tool_name="lookup",
        status="succeeded",
        result_available=True,
        foreground_released=detached,
        delivery_acknowledged=delivered,
    )
    await owner.runtime.store.save(StoredToolExecution(record, 42))
    owner._completed(record)


async def test_completion_hints_coalesce_and_skip_fast_or_delivered_calls():
    owner = ToolExecutions()
    hints = []
    delivered = Event()

    async def deliver(thread_id, prompt):
        hints.append((thread_id, prompt))
        delivered.set()
        return True

    owner.deliver = deliver
    async with create_task_group() as group:
        owner.start(group)
        await complete(owner, "fast", detached=False)
        await complete(owner, "consumed", delivered=True)
        await complete(owner, "first")
        await complete(owner, "second")
        await delivered.wait()
        assert len(hints) == 1
        assert "first" in hints[0][1] and "second" in hints[0][1]
        assert "fast" not in hints[0][1] and "consumed" not in hints[0][1]
        await owner.close(timeout_seconds=0)


async def test_completion_blocked_by_preparation_retries_after_settlement_without_polling():
    owner = ToolExecutions()
    attempts = []
    attempted, delivered = Event(), Event()
    ready = False

    async def deliver(thread_id, prompt):
        attempts.append(prompt)
        attempted.set()
        if ready:
            delivered.set()
        return ready

    owner.deliver = deliver
    async with create_task_group() as group:
        owner.start(group)
        await complete(owner, "first")
        await attempted.wait()
        # An explicit lifecycle transition retries retained input, not a timer.
        ready = True
        owner.retry("thread-a")
        await delivered.wait()
        assert len(attempts) == 2
        await owner.close(timeout_seconds=0)


async def test_stop_cancels_pending_delivery_and_new_input_reenables_future_wakes():
    owner = ToolExecutions()
    preparing, resumed = Event(), Event()
    hints = []
    blocked = True

    async def deliver(thread_id, prompt):
        if blocked:
            preparing.set()
            await Event().wait()
        hints.append(prompt)
        resumed.set()
        return True

    owner.deliver = deliver
    async with create_task_group() as group:
        owner.start(group)
        await complete(owner, "before-stop")
        await preparing.wait()
        owner.suppress("thread-a")
        await complete(owner, "after-stop")
        blocked = False
        owner.user_input("thread-a")
        await complete(owner, "new-input")
        await resumed.wait()
        assert len(hints) == 1 and "new-input" in hints[0]
        assert "before-stop" not in hints[0] and "after-stop" not in hints[0]
        await owner.close(timeout_seconds=0)


async def test_shutdown_joins_retained_cleanup_after_bounded_cancellation():
    owner = ToolExecutions()
    entered, cleaning, finish, closed = Event(), Event(), Event(), Event()

    async def operation():
        entered.set()
        await Event().wait()

    async def release():
        cleaning.set()
        await finish.wait()

    owner.runtime.admit(
        operation, thread_id="thread-a", run_id="run-a", tool_call_id="call", tool_name="slow", release=release
    )
    await entered.wait()

    async def close():
        await owner.close(timeout_seconds=0)
        closed.set()

    async with create_task_group() as group:
        group.start_soon(close)
        await cleaning.wait()
        assert not closed.is_set()
        finish.set()
        await closed.wait()


@pytest.mark.parametrize("failure", ["rejected", "exception"])
async def test_completion_racing_failed_delivery_is_retried_without_loss(failure):
    owner = ToolExecutions()
    entered, release, delivered = Event(), Event(), Event()
    attempts = []

    async def deliver(thread_id, prompt):
        attempts.append(prompt)
        if len(attempts) == 1:
            entered.set()
            await release.wait()
            if failure == "exception":
                raise RuntimeError("preparation failed")
            return False
        delivered.set()
        return True

    owner.deliver = deliver
    async with create_task_group() as group:
        owner.start(group)
        await complete(owner, "first")
        with fail_after(10):
            await entered.wait()
            await complete(owner, "second")
            release.set()
            await delivered.wait()
        assert len(attempts) == 2
        assert "first" in attempts[1] and "second" in attempts[1]
        for execution_id in ("first", "second"):
            assert not (await owner.runtime.info("thread-a", execution_id)).delivery_acknowledged
        await owner.close(timeout_seconds=0)


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "lost"])
async def test_terminal_notifications_are_status_only_and_refresh_suppressed_threads(status):
    owner = ToolExecutions()
    changed, delivered = Event(), Event()
    hints = []

    async def refresh(thread_id):
        assert thread_id == "thread-a"
        changed.set()

    async def deliver(thread_id, prompt):
        hints.append(prompt)
        delivered.set()
        return True

    owner.changed, owner.deliver = refresh, deliver
    record = ToolExecutionRecord(
        execution_id="exec-terminal",
        thread_id="thread-a",
        run_id="run-a",
        tool_call_id="call-a",
        tool_name="lookup",
        status=status,
        result_available=status == "succeeded",
        foreground_released=True,
        error="PRIVATE FAILURE DETAIL" if status != "succeeded" else None,
    )
    await owner.runtime.store.save(StoredToolExecution(record, "PRIVATE RESULT BODY"))
    async with create_task_group() as group:
        owner.start(group)
        owner._completed(record)
        with fail_after(10):
            await delivered.wait()
        assert len(hints) == 1 and f"lookup ({status})" in hints[0]
        assert "PRIVATE" not in hints[0]
        assert not (await owner.runtime.info("thread-a", record.execution_id)).delivery_acknowledged
        owner.suppress("thread-a")
        changed = Event()
        owner._completed(record)
        with fail_after(10):
            await changed.wait()
        assert len(hints) == 1
        await owner.close(timeout_seconds=0)


def install_slow_tool(monkeypatch, slow):
    original = AgentReconstructor.reconstruct

    def reconstruct(self, composition, **kwargs):
        capabilities = kwargs.pop("root_capabilities", ())
        return original(
            self,
            composition,
            root_capabilities=(
                *capabilities,
                Capability(
                    toolsets=[
                        CodeActPolicyToolset(
                            wrapped=FunctionToolset([Tool(slow, metadata={"a13n.tool-execution": "owned"})]),
                            policy=CodeActToolPolicy(tools={"slow": True}),
                        )
                    ]
                ),
            ),
            **kwargs,
        )

    monkeypatch.setattr(AgentReconstructor, "reconstruct", reconstruct)


@pytest.mark.parametrize("complete_while_active", [False, True])
async def test_app_detached_codeact_completion_resumes_once_and_keeps_late_state(
    tmp_path, monkeypatch, complete_while_active
):
    path = _write_configuration(tmp_path)
    path.write_text(path.read_text() + "tools:\n  execution: {mode: auto, foreground_wait_seconds: 0}\n")
    entered, finish, resumed = Event(), Event(), Event()
    waiting, delivered, continue_model = Event(), Event(), Event()
    calls = 0
    stage = 0
    execution_id = None

    async def slow() -> int:
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return 42

    install_slow_tool(monkeypatch, slow)

    async def model(messages, info):
        nonlocal stage, execution_id
        current = stage
        stage += 1
        if current == 0:
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args=json.dumps({"code": 'await slow(); await store(key="late", value=42); 42'}),
                    tool_call_id="runner",
                )
            }
        elif current == 1:
            result = next(
                part
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_call_id == "runner"
            )
            execution_id = result.content["execution_id"]
            waiting.set()
            if complete_while_active:
                await continue_model.wait()
            yield "Waiting for the retained operation."
        elif current == 2:
            yield {
                0: DeltaToolCall(
                    name="tool_execution_result",
                    json_args='{"execution_id":"' + execution_id + '"}',
                    tool_call_id="result",
                )
            }
        else:
            resumed.set()
            yield "Result received."

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        thread = await app.create_thread()
        original_deliver = app._tool_executions.deliver

        async def observe_delivery(thread_id, prompt):
            accepted = await original_deliver(thread_id, prompt)
            if accepted:
                delivered.set()
            return accepted

        app._tool_executions.deliver = observe_delivery
        first = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
        with fail_after(10):
            await waiting.wait()
            if not complete_while_active:
                outcome = await app.wait_root_operation(first.receipt_id)
                assert outcome.status is RootOperationStatus.completed, outcome
        assert entered.is_set(), await app._tool_executions.runtime.result(thread.thread_id, execution_id)
        assert execution_id is not None
        record = await app._tool_executions.runtime.info(thread.thread_id, execution_id)
        assert record.status == "running" and record.foreground_released
        finish.set()
        with fail_after(10):
            await delivered.wait()
            if complete_while_active:
                active = await app.active_root_operation(thread.thread_id)
                assert active is not None and active.receipt.receipt_id == first.receipt_id
                continue_model.set()
            await resumed.wait()
            while (active := await app.active_root_operation(thread.thread_id)) is not None:
                await app.wait_root_operation(active.receipt.receipt_id)
        result = await app._tool_executions.runtime.result(thread.thread_id, execution_id)
        assert result.record.delivery_acknowledged
        assert calls == 1 and stage == 4
        transcript = await app.get_thread_transcript(thread_id=thread.thread_id)
        hints = [
            part
            for entry in transcript.entries
            for part in entry.parts
            if part.metadata.source_id == "tool_execution.completion"
        ]
        assert len(hints) == 1 and not hints[0].metadata.display
        assert (await app._tool_executions.state(thread.thread_id).snapshot()).values == {"late": 42}
        other = await app.create_thread()
        assert await app.tool_executions(other.thread_id) == ()
        with pytest.raises(Exception, match="unavailable"):
            await app.cancel_tool_execution(other.thread_id, execution_id)
        assert app._tool_executions.state(other.thread_id) is not app._tool_executions.state(thread.thread_id)


async def test_clear_context_replaces_codeact_owner_and_isolates_late_writes(tmp_path, monkeypatch):
    path = _write_configuration(tmp_path)
    path.write_text(path.read_text() + "tools:\n  execution: {mode: auto, foreground_wait_seconds: 0}\n")
    entered, finish = Event(), Event()
    stage = 0

    async def slow() -> int:
        entered.set()
        await finish.wait()
        return 42

    install_slow_tool(monkeypatch, slow)

    async def model(messages, info):
        nonlocal stage
        current = stage
        stage += 1
        if current == 0:
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args=json.dumps(
                        {"code": 'await store(key="old", value=1); await slow(); await store(key="late", value=42)'}
                    ),
                    tool_call_id="runner",
                )
            }
        else:
            yield "Waiting"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Start")
        with fail_after(10):
            await entered.wait()
            outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status is RootOperationStatus.completed
        owner = app._tool_executions.state(thread.thread_id)
        assert (await owner.snapshot()).values == {"old": 1}
        selected = await app.get_thread(thread.thread_id)
        with pytest.raises(ThreadError, match="context changed"):
            await app.clear_thread_context(thread_id=thread.thread_id, expected_continuation_id="stale")
        assert app._tool_executions.state(thread.thread_id) is owner
        await app.clear_thread_context(thread_id=thread.thread_id, expected_continuation_id=selected.continuation_id)
        fresh = app._tool_executions.state(thread.thread_id)
        assert fresh is not owner
        app._tool_executions.suppress(thread.thread_id)
        finish.set()
        records = await app.tool_executions(thread.thread_id)
        result = await app._tool_executions.runtime.wait(thread.thread_id, records[0].execution_id, timeout_seconds=10)
        assert result.record.result_available
        assert (await owner.snapshot()).values == {"old": 1, "late": 42}
        next_receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="New context")
        assert (await app.wait_root_operation(next_receipt.receipt_id)).status is RootOperationStatus.completed
        assert (await fresh.snapshot()).values == {}


@pytest.mark.parametrize("change", [None, "source", "parent", "child", "stop"])
async def test_child_completion_keeps_lane_and_admitted_authority(tmp_path, monkeypatch, change):
    from a13n_harness_ui.surfaces import NewThreadDefaults

    from .test_builtin_subagents import _source

    path = _source(tmp_path, ("explorer",))
    path.write_text(path.read_text() + "tools:\n  execution: {mode: auto, foreground_wait_seconds: 0}\n")
    finish, resumed, delivered = Event(), Event(), Event()
    stages = {"parent": 0, "child": 0}
    resume_results = []
    execution_id = None

    async def slow() -> int:
        await finish.wait()
        return 42

    install_slow_tool(monkeypatch, slow)

    async def model(messages, info):
        nonlocal execution_id
        lane = "parent" if "delegate" in {tool.name for tool in info.function_tools} else "child"
        stage = stages[lane]
        stages[lane] += 1
        if lane == "parent":
            if stage == 0:
                yield {
                    0: DeltaToolCall(
                        name="delegate",
                        json_args='{"subagent_name":"explorer","prompt":"Work"}',
                        tool_call_id="delegate",
                    )
                }
            elif stage in {1, 4}:
                if stage == 4:
                    returned = next(
                        part
                        for message in messages
                        for part in message.parts
                        if isinstance(part, ToolReturnPart) and part.tool_call_id == "resume"
                    )
                    resume_results.append(returned.content)
                yield {
                    0: DeltaToolCall(
                        name="wait_subagent", json_args='{"timeout_seconds":10}', tool_call_id=f"wait-{stage}"
                    )
                }
            elif stage == 3:
                yield {
                    0: DeltaToolCall(
                        name="resume_subagent",
                        json_args=json.dumps({"execution_id": first.execution_id, "prompt": "Continue"}),
                        tool_call_id="resume",
                    )
                }
            else:
                yield "Parent finished."
        elif stage == 0:
            yield {0: DeltaToolCall(name="run_code", json_args='{"code":"await slow()"}', tool_call_id="runner")}
        elif stage == 1:
            result = next(
                part
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_call_id == "runner"
            )
            execution_id = result.content["execution_id"]
            if change == "stop":
                resumed.set()
                await Event().wait()
            yield "Child waiting."
        elif stage == 2:
            yield {
                0: DeltaToolCall(
                    name="tool_execution_result",
                    json_args=json.dumps({"execution_id": execution_id}),
                    tool_call_id="result",
                )
            }
        else:
            resumed.set()
            yield "Child received result."

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        thread = await app.create_thread(defaults=NewThreadDefaults(agent_id="agent-main"))
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Delegate")
        if change == "stop":
            with fail_after(10):
                await resumed.wait()
            page = await app.query_child_executions(parent_thread_id=thread.thread_id)
            assert (
                await app.cancel_child_execution(
                    parent_thread_id=thread.thread_id, execution_id=page.executions[0].execution_id
                )
            ).accepted
        with fail_after(10):
            assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        page = await app.query_child_executions(parent_thread_id=thread.thread_id)
        first = page.executions[0]
        child_id = first.child_thread_id
        assert len(page.executions) == 1
        assert await app.tool_executions(thread.thread_id) == ()
        assert len(await app.tool_executions(child_id)) == 1
        retained = app._subagent_operator._completion_continuations[child_id]
        if change == "source":
            agent = tmp_path / "agents/main.yaml"
            agent.write_text(agent.read_text() + "instructions: Changed authority.\n")
            await app.reload_configuration()
        elif change in {"parent", "child"}:
            selected_id = thread.thread_id if change == "parent" else child_id
            stored = await app._store.threads.get(selected_id)
            await app._store.threads.update_configuration(
                thread_id=selected_id,
                expected_version=stored.configuration.version,
                replacement=stored.configuration.model_copy(update={"version": stored.configuration.version + 1}),
            )
        original = app._tool_executions.deliver
        accepted = []

        async def observe(thread_id, prompt):
            result = await original(thread_id, prompt)
            accepted.append(result)
            delivered.set()
            return result

        app._tool_executions.deliver = observe
        finish.set()
        with fail_after(10):
            result = await app._tool_executions.runtime.wait(child_id, execution_id, timeout_seconds=10)
            assert result.record.result_available
            if change != "stop":
                await delivered.wait()
            if change is None:
                await resumed.wait()
                await app.wait_child_executions(parent_thread_id=thread.thread_id, timeout_seconds=10)
        page = await app.query_child_executions(parent_thread_id=thread.thread_id)
        if change is None:
            assert accepted == [True]
            assert len(page.executions) == 2
            assert {item.child_thread_id for item in page.executions} == {child_id}
            latest = app._subagent_operator._completion_continuations[child_id]
            assert latest.identity == retained.identity and latest.usage_limits == retained.usage_limits
            assert (await app._tool_executions.runtime.info(child_id, execution_id)).delivery_acknowledged
        else:
            assert accepted == ([] if change == "stop" else [False])
            assert len(page.executions) == 1
        assert stages["parent"] == 3
        if change == "stop":
            assert child_id in app._tool_executions._suppressed
            next_receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Resume the child")
            with fail_after(10):
                assert (await app.wait_root_operation(next_receipt.receipt_id)).status is RootOperationStatus.completed
            page = await app.query_child_executions(parent_thread_id=thread.thread_id)
            assert resume_results == ["The retained subagent execution is not resumable."]
            assert len(page.executions) == 1
            assert child_id in app._tool_executions._suppressed


async def test_http_execution_status_is_non_consuming_and_cancel_is_thread_scoped(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    import httpx
    from a13n_harness_ui.surfaces import ChildControlResult, RootControlResult
    from a13n_harness_ui.webui import create_webui

    path = _write_configuration(tmp_path)
    app = None

    @asynccontextmanager
    async def factory():
        nonlocal app
        async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as opened:
            app = opened
            yield opened

    server = create_webui(factory, api_key="test-only-key")
    async with server.router.lifespan_context(server):
        assert app is not None
        first, other = await app.create_thread(), await app.create_thread()
        owner = app._tool_executions
        owner.suppress(first.thread_id)
        entered = Event()

        async def slow():
            entered.set()
            await Event().wait()

        running = owner.runtime.admit(
            slow, thread_id=first.thread_id, run_id="run-a", tool_call_id="call-a", tool_name="slow"
        )
        await entered.wait()
        completed = ToolExecutionRecord(
            execution_id="tool-exec-ready",
            thread_id=first.thread_id,
            run_id="run-a",
            tool_call_id="call-b",
            tool_name="read",
            status="succeeded",
            result_available=True,
            foreground_released=True,
        )
        await owner.runtime.store.save(StoredToolExecution(completed, {"private": "retained-result"}))
        calls = []

        async def steer_root(**kwargs):
            calls.append(kwargs)
            return RootControlResult(receipt_id=kwargs["receipt_id"], accepted=True)

        async def steer_child(**kwargs):
            calls.append(kwargs)
            return ChildControlResult(execution_id=kwargs["execution_id"], accepted=True)

        monkeypatch.setattr(app, "steer_root_operation", steer_root)
        monkeypatch.setattr(app, "steer_child_execution", steer_child)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://localhost") as client:
            endpoint = f"/api/threads/{first.thread_id}/tool-executions"
            assert (await client.get(endpoint)).status_code == 401
            client.headers["Authorization"] = "Bearer test-only-key"
            response = await client.get(endpoint)
            assert response.status_code == 200
            assert len(response.json()) == 2 and "retained-result" not in response.text
            assert not (await owner.runtime.info(first.thread_id, completed.execution_id)).delivery_acknowledged
            assert len((await client.get(endpoint, params={"offset": 1, "limit": 1})).json()) == 1
            assert (await client.get(f"/api/threads/{other.thread_id}/tool-executions")).json() == []
            denied = await client.post(f"/api/threads/{other.thread_id}/tool-executions/{running.execution_id}/cancel")
            assert denied.status_code >= 400 and denied.json()["error"]["code"] == "tool_execution_unavailable"
            assert (await owner.runtime.info(first.thread_id, running.execution_id)).status == "running"
            assert (await client.post(f"{endpoint}/{running.execution_id}/cancel")).status_code == 200
            assert (
                await owner.runtime.wait(first.thread_id, running.execution_id, timeout_seconds=10)
            ).record.status == "cancelled"
            for route in ("/api/operations/receipt-a/steer", f"/api/threads/{first.thread_id}/children/child-a/steer"):
                body = {"parts": ["Focus"]} if "/operations/" in route else {"prompt": "Focus"}
                assert (await client.post(route, json=body)).status_code == 200
            assert len(calls) == 2 and all("immediate" not in call for call in calls)


async def test_tool_wait_policy_is_captured_and_old_compositions_default_inline(tmp_path):
    from a13n_harness import ToolExecutionConfig
    from a13n_harness.capabilities import ToolExecutionCapability
    from a13n_harness_ui.composition.models import ResolvedRunComposition
    from a13n_harness_ui.configuration import load_harness_ui_configuration

    from .test_builtin_subagents import _resolve, _source
    from .test_composition import _UnusedOperator

    path = _source(tmp_path, ())
    path.write_text(path.read_text() + "tools:\n  execution: {mode: auto, foreground_wait_seconds: 0.25}\n")
    captured = _resolve(await load_harness_ui_configuration(path))
    expected = ToolExecutionConfig(mode="auto", foreground_wait_seconds=0.25)
    assert captured.tool_execution == expected
    restored = ResolvedRunComposition.model_validate_json(captured.model_dump_json())
    assert restored == captured
    path.write_text('schema_version: "1"\n')
    defaults = _resolve(await load_harness_ui_configuration(path)).tool_execution
    assert defaults.mode == "inline" and defaults.foreground_wait_seconds == 5.0
    rebuilt = AgentReconstructor().reconstruct(restored, subagent_operator=_UnusedOperator())
    capability = next(
        item for item in rebuilt.executable.definition.capabilities if isinstance(item, ToolExecutionCapability)
    )
    assert capability.config == expected
    legacy = captured.model_dump(mode="json")
    legacy.pop("tool_execution")
    assert ResolvedRunComposition.model_validate(legacy).tool_execution == ToolExecutionConfig()


async def test_global_steering_mode_defaults_immediate_for_root_and_child_but_not_completion(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_harness_ui.surfaces import ChildControlResult, RootControlResult

    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        thread = await app.create_thread()
        receipt = SimpleNamespace(receipt_id="receipt-test", thread_id=thread.thread_id)
        operation = SimpleNamespace(receipt=receipt)
        root = AsyncMock(return_value=RootControlResult(receipt_id=receipt.receipt_id, accepted=False))
        child = AsyncMock(return_value=ChildControlResult(execution_id="child-test", accepted=False))
        monkeypatch.setattr(app._root_runs, "get", AsyncMock(return_value=operation))
        monkeypatch.setattr(app._root_runs, "active", AsyncMock(return_value=operation))
        monkeypatch.setattr(app._root_runs, "steer", root)
        monkeypatch.setattr(app._subagent_operator, "steer_execution", child)
        original = path.read_text()
        for mode in (None, "queued", "immediate"):
            path.write_text(original + (f"\ninput:\n  steering_mode: {mode}\n" if mode else ""))
            await app.reload_configuration()
            await app.steer_root_operation(receipt_id=receipt.receipt_id, message="Focus")
            await app.steer_child_execution(
                parent_thread_id=thread.thread_id, execution_id="child-test", message="Focus"
            )
            assert root.call_args.kwargs["immediate"] is (mode != "queued")
            assert child.call_args.kwargs["immediate"] is (mode != "queued")
            await app._deliver_tool_completion(thread.thread_id, "Tool completed")
            assert root.call_args.kwargs["immediate"] is False
            content = root.call_args.kwargs["message"]
            assert content[0].value == "Tool completed"
            assert content[0].metadata.display is False
            assert content[0].metadata.source_id == "tool_execution.completion"
