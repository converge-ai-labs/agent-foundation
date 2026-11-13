"""Planned update pauses preserve work without accepting a durable input queue."""

import json

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.storage import StoredContinuation, open_local_store
from a13n_harness_ui.storage.continuations import read_continuation
from anyio import CancelScope, Event, create_task_group, fail_after, sleep
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings, _write_configuration

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


async def release_during_shutdown(app, release, receipt_id=None):
    await app._restart.pause_requested.wait()
    if receipt_id is not None:
        assert not (await app._root_runs.steer(receipt_id=receipt_id, message="Not accepted")).accepted
    release.set()


def test_update_instructions_belong_only_to_the_restored_run():
    from unittest.mock import Mock

    from a13n_harness_ui.restart import GracefulRestart, RestartPauseCapability
    from a13n_harness_ui.storage.restarts import RestartRepository

    restart_coordinator = GracefulRestart(Mock(spec=RestartRepository), enabled=True)
    restart_coordinator.continued_threads.add("parent")
    restart_coordinator.successors["parent"] = {"old-child": "new-child"}
    restored = RestartPauseCapability(restart_coordinator, "parent")
    restart_coordinator.continued_threads.clear()
    restart_coordinator.successors.clear()
    assert "old-child -> new-child" in restored.get_instructions()
    assert RestartPauseCapability(restart_coordinator, "parent").get_instructions() is None


def install_model(monkeypatch, function):
    async def resolve(self, context, model_id):
        async def stream(messages, info):
            response = await function(messages, info)
            for part in response.parts:
                if isinstance(part, TextPart):
                    yield part.content
                elif isinstance(part, ToolCallPart):
                    yield {
                        0: DeltaToolCall(
                            name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id
                        )
                    }

        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)


def fail_environment_finalization(monkeypatch, mode):
    from dataclasses import replace

    from a13n_harness_ui.environment_runtime import EnvironmentRunPlan, EnvironmentStatePublication
    from a13n_harness_ui.storage import EnvironmentBindingKey

    finalization = EnvironmentRunPlan.finalization.fget

    def failing(self):
        result = finalization(self)
        error = RuntimeError("Synthetic finalization failure")
        if mode == "cleanup":
            return replace(result, cleanup_errors=(error,))
        return replace(
            result,
            state_publications=(
                EnvironmentStatePublication(
                    key=EnvironmentBindingKey(
                        thread_id="test",
                        environment_profile_id="environment-native",
                        profile_digest="a" * 64,
                        adapter_key="test",
                        normalized_root="/test",
                    ),
                    previous=None,
                    replacement=None,
                    status="failed",
                    error=error,
                ),
            ),
        )

    monkeypatch.setattr(EnvironmentRunPlan, "finalization", property(failing))


@pytest.mark.parametrize("finalization_failure", [None, "state", "cleanup"])
@pytest.mark.parametrize("mode", ["normal", "goal", "legacy"])
async def test_planned_restart_continues_without_repeating_input_or_tool(
    tmp_path, monkeypatch, finalization_failure, mode
):
    root = _write_configuration(tmp_path)
    # Preserve pending guidance while the first request reaches its tool checkpoint.
    root.write_text(root.read_text() + "\ninput:\n  steering_mode: queued\n")
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state"), pricing_auto_update=False)
    started, release = Event(), Event()
    requests = []

    async def model(messages, info):
        requests.append(messages)
        if len(requests) == 1:
            started.set()
            await release.wait()
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "store", {"key": "saved-result", "value": "Saved tool result"}, tool_call_id="read-once"
                    )
                ]
            )
        return ModelResponse(
            parts=[TextPart("Finished after update\n[GOAL_COMPLETE]" if mode == "goal" else "Finished after update")]
        )

    install_model(monkeypatch, model)
    async with (
        create_task_group() as group,
        open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app,
    ):
        thread = await app.create_thread()
        receipt = await app.submit_thread(
            thread_id=thread.thread_id, prompt="Read and report", mode="normal" if mode == "legacy" else mode
        )
        with fail_after(10):
            await started.wait()
        assert (
            await app.steer_root_operation(receipt_id=receipt.receipt_id, message="Keep the report concise")
        ).accepted
        group.start_soon(release_during_shutdown, app, release, receipt.receipt_id)
        if finalization_failure:
            fail_environment_finalization(monkeypatch, finalization_failure)
    # Finalization failures must not pass because an earlier drain timed out.
    assert app._restart.committing, app._restart.errors
    if finalization_failure:
        async with open_local_store(settings.storage) as store:
            batch = await store.restarts.get()
            assert batch is not None and not batch.items
            assert await store.restarts.claim() is None
        async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
            assert not app._restart.restoring
        assert len(requests) == 1
        return
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is not None and batch.state == "ready", batch
        assert len(batch.items) == 1
        saved = await read_continuation(store.objects, batch.items[0].checkpoint, include_display=True)
        from a13n_harness.usage import UsageSnapshot

        original_usage = UsageSnapshot.from_state(saved.harness_state)
        assert original_usage is not None and original_usage.summary.requests == 1
        if mode == "legacy":
            from a13n_harness.state import AgentContextStateSnapshot
            from a13n_harness.usage import USAGE_CAPABILITY_ID
            from a13n_harness_ui.storage.objects import ObjectKind

            entries = saved.harness_state.agent_context_state.entries
            entries.pop(USAGE_CAPABILITY_ID)
            legacy_state = saved.harness_state.model_copy(
                update={"agent_context_state": AgentContextStateSnapshot(entries=entries)}
            )
            legacy = await store.objects.publish_model(
                object_kind=ObjectKind.continuation,
                value=StoredContinuation(
                    harness_release=saved.harness_release,
                    run_composition=saved.run_composition,
                    harness_state=legacy_state,
                    created_at=saved.created_at,
                ),
            )
            current = await store.threads.get(thread.thread_id)
            assert current is not None
            await store.threads.select_continuation(
                thread_id=thread.thread_id,
                expected=current.continuation,
                replacement=legacy.ref,
                read_model=current.read_model,
            )
            await store.restarts.replace(
                batch,
                batch.model_copy(update={"items": (batch.items[0].model_copy(update={"checkpoint": legacy.ref}),)}),
            )
        if mode == "goal":
            from a13n_harness_ui.goal import saved_goal

            goal = saved_goal(saved.harness_state)
            assert goal.objective == "Read and report"
            assert goal.iteration == 0
            assert goal.max_iterations == 10
            # The handoff owns the last safe request-boundary checkpoint,
            # not the terminal cancellation used to close the old process.
            assert goal.status == "working"
        assert any(
            isinstance(p, ToolReturnPart) and p.tool_call_id == "read-once"
            for m in saved.harness_state.message_history
            for p in m.parts
        )
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        with fail_after(10):
            while (await app.get_thread(thread.thread_id)).thread.completion is None:
                await sleep(0.01)
        assert len(requests) == 2
        if mode == "goal":
            goal = (await app.get_thread(thread.thread_id)).thread.goal
            assert goal.objective == "Read and report"
            assert goal.status == "verified"
            assert goal.iteration == 0
            assert goal.max_iterations == 10
        final_messages = requests[-1]
        assert (
            sum(
                isinstance(p, ToolReturnPart) and p.tool_call_id == "read-once" for m in final_messages for p in m.parts
            )
            == 1
        )
        authored = [
            p
            for m in final_messages
            for p in m.parts
            if isinstance(p, UserPromptPart) and "Read and report" in p.content
        ]
        assert len(authored) == 1
        assert (
            sum(
                isinstance(p, UserPromptPart) and "Keep the report concise" in str(p.content)
                for m in final_messages
                for p in m.parts
            )
            == 1
        )
    async with open_local_store(settings.storage) as store:
        current = await store.threads.get(thread.thread_id)
        assert current is not None and current.continuation is not None
        continuation = await read_continuation(store.objects, current.continuation, include_display=True)
        resumed_usage = UsageSnapshot.from_state(continuation.harness_state)
        assert resumed_usage is not None
        if mode == "legacy":
            assert resumed_usage.usage_id != original_usage.usage_id
            assert resumed_usage.summary.requests == 1
        else:
            assert resumed_usage.usage_id == original_usage.usage_id
            assert resumed_usage.run_id == original_usage.run_id
            assert resumed_usage.summary.requests == 2 and resumed_usage.tool_calls == 1
        assert (await store.usage.snapshot(thread_id=thread.thread_id)).combined.model_requests == 2
        if mode == "goal":
            assert goal.input_tokens == resumed_usage.summary.input_tokens
            assert goal.output_tokens == resumed_usage.summary.output_tokens
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert not app._restart.restoring
    assert len(requests) == 2


@pytest.mark.parametrize("exit_kind", ["error", "cancel", "cli"])
async def test_abnormal_exit_and_cli_do_not_arm_recovery(tmp_path, monkeypatch, exit_kind):
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    started = Event()

    async def model(messages, info):
        started.set()
        await Event().wait()
        return ModelResponse(parts=[TextPart("Never")])

    install_model(monkeypatch, model)
    try:
        with CancelScope() as cancellation:
            async with open_harness_ui_app(
                settings, configuration_path=root, host_mode="cli" if exit_kind == "cli" else "webui"
            ) as app:
                thread = await app.create_thread()
                await app.submit_thread(thread_id=thread.thread_id, prompt="Wait")
                with fail_after(10):
                    await started.wait()
                if exit_kind == "error":
                    raise ValueError("Abnormal context exit")
                if exit_kind == "cancel":
                    cancellation.cancel()
                    await sleep(0)
    except BaseExceptionGroup as exc:
        assert exit_kind == "error"
        assert "Abnormal context exit" in str(exc.exceptions[0])
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is None or not batch.items
        assert await store.restarts.claim() is None


async def test_drain_timeout_recovers_the_exact_model_boundary(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state", shutdown_timeout_seconds=0.05)
    started = Event()
    requests = 0

    async def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            started.set()
            await Event().wait()
        return ModelResponse(parts=[TextPart("Continued from the saved model request")])

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        await app.submit_thread(thread_id=thread.thread_id, prompt="Wait")
        with fail_after(10):
            await started.wait()
    assert app._restart.committing and not app._restart.errors
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is not None and batch.state == "ready" and len(batch.items) == 1
        current = await store.threads.get(thread.thread_id)
        assert current.continuation == batch.items[0].checkpoint
        saved = await store.read_continuation(thread.thread_id, current.continuation, include_display=True)
        assert not any(isinstance(p, TextPart) for m in saved.harness_state.message_history for p in m.parts)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        with fail_after(10):
            while (await app.get_thread(thread.thread_id)).thread.completion is None:
                await sleep(0.01)
        assert requests == 2


@pytest.mark.parametrize("parent_completes", [False, True])
@pytest.mark.parametrize("finalization_failure", [None, "state"])
async def test_child_restores_with_active_or_completed_parent(
    tmp_path, monkeypatch, parent_completes, finalization_failure
):
    import yaml

    root = _write_configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state"), pricing_auto_update=False)
    agent_path = tmp_path / "agents/assistant.yaml"
    parent = yaml.safe_load(agent_path.read_text())
    parent["subagents"] = [{"agent": "agent-worker"}]
    agent_path.write_text(yaml.safe_dump(parent))
    (tmp_path / "agents/worker.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "kind": "agent",
                "id": "agent-worker",
                "name": "Worker",
                "model": "model-primary",
            }
        )
    )
    child_started, release_child, parent_waiting = Event(), Event(), Event()
    calls = {"root": 0, "child": 0}

    async def model(messages, info):
        is_root = "delegate" in {tool.name for tool in info.function_tools}
        key = "root" if is_root else "child"
        calls[key] += 1
        if not is_root:
            if calls[key] == 1:
                child_started.set()
                await release_child.wait()
                return ModelResponse(parts=[ToolCallPart("store", {"key": "child-once", "value": 1})])
            return ModelResponse(parts=[TextPart("Child finished")])
        if calls[key] == 1:
            return ModelResponse(
                parts=[ToolCallPart("delegate", {"subagent_name": "agent-worker", "prompt": "Bounded work"})]
            )
        if calls[key] == 2 and not parent_completes:
            parent_waiting.set()
            return ModelResponse(parts=[ToolCallPart("wait_subagent", {"timeout_seconds": 120})])
        return ModelResponse(parts=[TextPart("Parent finished")])

    install_model(monkeypatch, model)
    async with (
        create_task_group() as group,
        open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app,
    ):
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Delegate work")
        with fail_after(10):
            await child_started.wait()
            if parent_completes:
                assert (await app.wait_root_operation(receipt.receipt_id)).status == "completed"
            else:
                await parent_waiting.wait()
        heads, _ = await app._store.child_executions.list_for_parent(thread.thread_id)
        mid_head = heads[0]
        mid_checkpoint = mid_head.selected_checkpoint
        assert mid_checkpoint is not None
        assert not (await app._store.read_child_checkpoint(mid_head)).terminal
        group.start_soon(release_during_shutdown, app, release_child)
        if finalization_failure:
            fail_environment_finalization(monkeypatch, finalization_failure)
    # Finalization failures must not pass because an earlier drain timed out.
    assert app._restart.committing, app._restart.errors
    if finalization_failure:
        async with open_local_store(settings.storage) as store:
            batch = await store.restarts.get()
            assert batch is not None and not batch.items
            assert await store.restarts.claim() is None
        async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
            assert not app._restart.restoring
        assert calls["child"] == 1
        return
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is not None and batch.state == "ready", batch
        assert len(batch.items) == (1 if parent_completes else 2)
        child_item = next(item for item in batch.items if item.execution_id is not None)
        assert child_item.checkpoint != mid_checkpoint
        selected = await store.child_executions.get(child_item.execution_id)
        assert selected is not None and selected.selected_checkpoint == child_item.checkpoint
        # A stale request-boundary pointer cannot replace the exact terminal handoff.
        from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator

        operator = HarnessUiSubagentOperator(
            store=store,
            configurations=None,
            compositions=None,
            agent_reconstructor=None,
            environment_service=None,
        )
        with pytest.raises(RunCoordinationError, match="handoff checkpoint changed"):
            await operator.resume_restart(child_item.model_copy(update={"checkpoint": mid_checkpoint}), batch.batch_id)

    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        recovered = await app._store.restarts.get()
        assert recovered is not None and recovered.state in {"consumed", "watching"}
        assert all(task.state == "restored" for task in recovered.results)
        if recovered.state == "watching":
            assert all(item.execution_id is None for item in recovered.items)
        child = next(task for task in recovered.results if task.execution_id is not None)
        assert child.resumed_execution_id != child_item.execution_id
        assert child.resumed_run_id != child_item.run_id
        with fail_after(10):
            while (await app.active_work_summary()).child_executions:
                await sleep(0.01)
        assert calls["child"] == 2
        if parent_completes:
            assert calls["root"] == 2


async def test_questions_stay_waiting_without_timeout_or_automatic_answer(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "tools:\n  interaction_timeout_seconds: 0.3\n")
    settings = _settings(tmp_path / "state")
    requests = 0

    async def model(messages, info):
        nonlocal requests
        requests += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "ask_user_question",
                    {
                        "questions": [
                            {
                                "header": "Choice",
                                "question": "Choose one",
                                "options": [
                                    {"label": "One", "description": "First"},
                                    {"label": "Two", "description": "Second"},
                                ],
                            }
                        ]
                    },
                )
            ]
        )

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Ask")
        assert (await app.wait_root_operation(receipt.receipt_id)).status == "suspended"
        before = await app.thread_decisions(thread_id=thread.thread_id)
        assert before is not None and before.expires_at is not None
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        with fail_after(10):
            while True:
                waiting = await app.thread_decisions(thread_id=thread.thread_id)
                if waiting is not None and waiting.expires_at is None:
                    break
                await sleep(0)
        assert waiting.expires_at is None
        assert waiting.continuation_id == before.continuation_id
    assert requests == 1


@pytest.mark.parametrize("state", ["watching", "ready"])
async def test_handoff_consumption_is_atomic_and_does_not_require_reset(tmp_path, state):
    from a13n_harness_ui.restart_models import RestartBatch, RestartItem
    from a13n_harness_ui.storage.objects import ObjectKind, ObjectRef

    reference = ObjectRef(object_kind=ObjectKind.continuation, object_schema_version="1", logical_digest="a" * 64)
    item = RestartItem(
        thread_id="thread-test",
        root_thread_id="thread-test",
        run_id="run-test",
        composition=reference,
        checkpoint=reference,
    )

    settings = _settings(tmp_path / "state")
    async with open_local_store(settings.storage) as first, open_local_store(settings.storage) as second:
        batch = RestartBatch(batch_id="restart-test", state=state, items=(item,))
        await first.restarts.publish(batch)
        claimed = []

        async def claim(store):
            claimed.append(await store.restarts.claim())

        async with create_task_group() as group:
            group.start_soon(claim, first)
            group.start_soon(claim, second)
        assert sum(item is not None for item in claimed) == 1
        assert await first.restarts.claim() is None
        consumed = await first.restarts.get()
        assert consumed.state == "consumed"
        with pytest.raises(RunCoordinationError):
            await first.restarts.replace(batch, consumed)
        # A consumed attempt is historical data, not an operator lock.
        next_batch = RestartBatch(batch_id="restart-next", state=state, items=(item,))
        await first.restarts.publish(next_batch)
        assert (await first.restarts.claim()).batch_id == "restart-next"


async def test_cancelled_task_is_excluded_from_planned_handoff(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    started = Event()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        started.set()
        await Event().wait()
        return ModelResponse(parts=[TextPart("Never")])

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Wait")
        with fail_after(10):
            await started.wait()
        assert (await app.cancel_root_operation(receipt.receipt_id)).accepted
        assert (await app.wait_root_operation(receipt.receipt_id)).status == "cancelled"
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is not None and not batch.items
        assert await store.restarts.claim() is None
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert not app._restart.restoring
    assert calls == 1


async def test_incompatible_reconstruction_is_blocked_and_never_retried(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state"), pricing_auto_update=False)
    started, release = Event(), Event()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return ModelResponse(parts=[ToolCallPart("store", {"key": "once", "value": 1})])

    install_model(monkeypatch, model)
    async with (
        create_task_group() as group,
        open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app,
    ):
        thread = await app.create_thread()
        await app.submit_thread(thread_id=thread.thread_id, prompt="Work")
        with fail_after(10):
            await started.wait()
        group.start_soon(release_during_shutdown, app, release)

    async def finished_model():
        return ModelResponse(parts=[TextPart("Normal admission remains usable")])

    async def unavailable(self, context, model_id):
        raise ValueError("Synthetic incompatible model implementation")

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", unavailable)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        report = await app._store.restarts.get()
        assert report is not None and report.state == "consumed" and report.error
        assert calls == 1
        install_model(monkeypatch, lambda messages, info: finished_model())
        unrelated = await app.create_thread()
        receipt = await app.submit_thread(thread_id=unrelated.thread_id, prompt="Ordinary work")
        assert (await app.wait_root_operation(receipt.receipt_id)).status == "completed"
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert not app._restart.restoring
        assert calls == 1


@pytest.mark.parametrize("startup", ["success", "parent_failure", "timeout", "cancel"])
async def test_children_admitted_during_drain_require_complete_family_staging(tmp_path, monkeypatch, startup):
    import yaml
    from a13n_harness_ui.root_run import RootRunCoordinator
    from anyio import move_on_after

    root = _write_configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state"), pricing_auto_update=False)
    parent_path = tmp_path / "agents/assistant.yaml"
    parent = yaml.safe_load(parent_path.read_text())
    parent["subagents"] = [{"agent": "agent-worker"}]
    parent_path.write_text(yaml.safe_dump(parent))
    (tmp_path / "agents/worker.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": "1", "kind": "agent", "id": "agent-worker", "name": "Worker", "model": "model-primary"}
        )
    )
    started = Event()
    calls = {"root": 0, "child": 0}

    async def model(messages, info):
        role = "root" if "delegate" in {tool.name for tool in info.function_tools} else "child"
        calls[role] += 1
        if role == "root" and calls[role] == 1:
            started.set()
            # The model finishes its current request after shutdown begins. Its
            # delegation must still be admitted, then join the same safe drain.
            await app._restart.pause_requested.wait()
            return ModelResponse(
                parts=[ToolCallPart("delegate", {"subagent_name": "agent-worker", "prompt": "Bounded work"})]
            )
        return ModelResponse(parts=[TextPart("Completed")])

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        await app.submit_thread(thread_id=thread.thread_id, prompt="Delegate at shutdown")
        with fail_after(10):
            await started.wait()
    assert calls == {"root": 1, "child": 0}
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is not None and batch.state == "ready"
        assert len(batch.items) == 2

    resume = RootRunCoordinator.resume_restart

    async def resume_parent(self, item):
        if startup == "parent_failure":
            raise ValueError("Synthetic incompatible parent")
        if startup == "timeout":
            await Event().wait()
        if startup == "cancel":
            cancellation.cancel()
            await sleep(0)
        return await resume(self, item)

    monkeypatch.setattr(RootRunCoordinator, "resume_restart", resume_parent)
    if startup == "timeout":
        # Exercise the entire reconstruction budget, not merely the final
        # boundary wait. The public startup must not hang inside resume_parent.
        monkeypatch.setattr("a13n_harness_ui.restart_recovery.move_on_after", lambda seconds: move_on_after(0.2))
    with fail_after(10), CancelScope() as cancellation:
        async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
            assert startup != "cancel", "Interrupted staging must not yield a ready App"
            with fail_after(5):
                while (await app.active_work_summary()).child_executions:
                    await sleep(0.01)
            if startup == "success":
                with fail_after(5):
                    while (await app.get_thread(thread.thread_id)).thread.completion is None:
                        await sleep(0.01)
                assert calls == {"root": 2, "child": 1}
            else:
                assert calls == {"root": 1, "child": 0}
                report = await app._store.restarts.get()
                assert report is not None and report.error
                assert all(result.state == "blocked" for result in report.results)
                # A failed forest is not an admission lock.
                unrelated = await app.create_thread()
                receipt = await app.submit_thread(thread_id=unrelated.thread_id, prompt="Ordinary work")
                assert (await app.wait_root_operation(receipt.receipt_id)).status == "completed"
    before = dict(calls)
    monkeypatch.setattr(RootRunCoordinator, "resume_restart", resume)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        report = await app._store.restarts.get()
        assert report is not None and (report.state == "consumed" or not report.items)
        assert await app._store.restarts.claim() is None
        assert not app._restart.restoring
    assert calls == before


async def test_restart_boundary_is_not_stale_when_shutdown_immediately_follows_startup():
    from unittest.mock import Mock

    from a13n_harness import HarnessState
    from a13n_harness_ui.restart import GracefulRestart
    from a13n_harness_ui.storage.restarts import RestartRepository

    coordinator = GracefulRestart(Mock(spec=RestartRepository), enabled=True)
    coordinator.register("thread")
    coordinator.restoring = True
    coordinator.restoration_pending.add("thread")
    state = HarnessState.new()
    reached_provider = Event()

    async def run():
        await coordinator.checkpoint("thread", state)
        reached_provider.set()

    async with create_task_group() as group:
        group.start_soon(run)
        with fail_after(2):
            while coordinator.active["thread"].state is None:
                await coordinator.changed.wait()
        # Exactly the release cutover used by startup, followed by shutdown
        # before the paused task gets an event-loop turn.
        coordinator.restoring = False
        coordinator.restoration_pending.clear()
        coordinator.active["thread"].state = None
        coordinator.active["thread"].release.set()
        await coordinator.drain(1)
        assert coordinator.committing
        assert coordinator.saved_state("thread") is state
        assert not reached_provider.is_set()
        group.cancel_scope.cancel()


async def test_slow_unsafe_tool_does_not_discard_an_unrelated_family(tmp_path, monkeypatch):
    from a13n_harness.codeact.state import CodeActStateOwner

    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state", shutdown_timeout_seconds=1)
    unsafe_started, healthy_started = Event(), Event()
    effects = []
    requests = {"Unsafe": 0, "Healthy": 0}
    original = CodeActStateOwner.store

    async def store(self, key, value, config):
        effects.append(key)
        if key == "uncertain":
            unsafe_started.set()
            await Event().wait()
        await original(self, key, value, config)

    monkeypatch.setattr(CodeActStateOwner, "store", store)

    async def model(messages, info):
        role = (
            "Unsafe"
            if any(isinstance(p, UserPromptPart) and "Unsafe" in str(p.content) for m in messages for p in m.parts)
            else "Healthy"
        )
        requests[role] += 1
        if role == "Unsafe":
            return ModelResponse(parts=[ToolCallPart("store", {"key": "uncertain", "value": 1})])
        if requests[role] == 1:
            healthy_started.set()
            await app._restart.pause_requested.wait()
            return ModelResponse(parts=[ToolCallPart("store", {"key": "finished-once", "value": 1})])
        return ModelResponse(parts=[TextPart("Healthy family continued")])

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        unsafe, healthy = await app.create_thread(), await app.create_thread()
        await app.submit_thread(thread_id=unsafe.thread_id, prompt="Unsafe")
        await app.submit_thread(thread_id=healthy.thread_id, prompt="Healthy")
        with fail_after(10):
            await unsafe_started.wait()
            await healthy_started.wait()
    async with open_local_store(settings.storage) as storage:
        batch = await storage.restarts.get()
        assert batch.state == "ready", (
            app._restart.errors,
            tuple(app._restart.finalized),
            unsafe.thread_id,
            healthy.thread_id,
        )
        assert [item.thread_id for item in batch.items] == [healthy.thread_id]
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        with fail_after(10):
            while (await app.get_thread(healthy.thread_id)).thread.completion is None:
                await sleep(0.01)
        assert requests == {"Unsafe": 1, "Healthy": 2}
    assert sorted(effects) == ["finished-once", "uncertain"]


async def test_response_retirement_failure_blocks_tools_without_stranding_waiters(tmp_path, monkeypatch):
    from a13n_harness.codeact.state import CodeActStateOwner

    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    effects = []
    requests = 0

    async def store(self, key, value, config):
        effects.append(key)

    async def model(messages, info):
        nonlocal requests
        requests += 1
        return ModelResponse(parts=[ToolCallPart("store", {"key": "never", "value": 1})])

    monkeypatch.setattr(CodeActStateOwner, "store", store)
    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        publish = app._store.restarts.publish

        async def fail_retirement(batch):
            if not batch.items:
                raise OSError("Synthetic recovery retirement failure")
            await publish(batch)

        monkeypatch.setattr(app._store.restarts, "publish", fail_retirement)
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Work")
        with fail_after(10):
            outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status == "failed"
        assert not effects and requests == 1
    # Even if retirement itself cannot be persisted, terminal execution and the
    # selected checkpoint fence the remaining old marker before a model action.
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert requests == 1 and not effects
        batch = await app._store.restarts.get()
        assert batch.state == "consumed" and all(item.state == "blocked" for item in batch.results)


async def test_nonowner_app_cannot_claim_or_overwrite_the_owners_candidate(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state", shutdown_timeout_seconds=0.01)
    started = Event()

    async def model(messages, info):
        started.set()
        await Event().wait()
        return ModelResponse(parts=[TextPart("Never")])

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as owner:
        thread = await owner.create_thread()
        receipt = await owner.submit_thread(thread_id=thread.thread_id, prompt="Owner's work")
        with fail_after(10):
            await started.wait()
        before = await owner._store.restarts.get()
        assert before.state == "watching" and len(before.items) == 1
        async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as other:
            assert not other._restart.enabled
            assert await other._store.restarts.get() == before
        assert await owner._store.restarts.get() == before
        await owner.cancel_root_operation(receipt.receipt_id)
        await owner.wait_root_operation(receipt.receipt_id)


async def test_root_candidates_do_not_reconstruct_active_child_executions(tmp_path):
    from a13n_harness_ui.restart import GracefulRestart
    from a13n_harness_ui.restart_models import RestartItem
    from a13n_harness_ui.storage.objects import ObjectKind, ObjectRef

    reference = ObjectRef(object_kind=ObjectKind.continuation, object_schema_version="1", logical_digest="b" * 64)
    async with open_local_store(_settings(tmp_path / "state").storage) as store:
        restart = GracefulRestart(store.restarts, enabled=True)
        restart.register("parent")
        restart.register("independent")
        parent = RestartItem(
            thread_id="parent",
            root_thread_id="parent",
            run_id="old",
            cause="process_loss",
            composition=reference,
            checkpoint=reference,
        )
        independent = parent.model_copy(update={"thread_id": "independent", "root_thread_id": "independent"})
        await restart.boundary(parent)
        restart.register("child", "execution-child", root_thread_id="parent")
        await restart.boundary(parent)
        await restart.boundary(independent)
        batch = await store.restarts.get()
        assert {item.thread_id for item in batch.items} == {"independent", "parent"}
        assert all(item.execution_id is None for item in batch.items)
        restart.finished("child")
        assert await store.restarts.get() == batch


@pytest.mark.parametrize("conflict", ["checkpoint", "composition", "execution"])
async def test_process_loss_validates_selected_checkpoint_and_execution_before_action(tmp_path, monkeypatch, conflict):
    from a13n_harness_ui.storage.objects import ObjectKind, ObjectRef

    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state", shutdown_timeout_seconds=0.01)
    started = Event()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        started.set()
        await Event().wait()
        return ModelResponse(parts=[TextPart("Never")])

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        await app.submit_thread(thread_id=thread.thread_id, prompt="Work")
        with fail_after(10):
            await started.wait()
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        item = batch.items[0]
        if conflict == "execution":
            item = item.model_copy(update={"cause": "process_loss"})
        else:
            kind = ObjectKind.continuation if conflict == "checkpoint" else ObjectKind.run_composition
            item = item.model_copy(
                update={conflict: ObjectRef(object_kind=kind, object_schema_version="1", logical_digest="c" * 64)}
            )
        # The original execution is now cancelled by graceful shutdown. A raw
        # cancelled summary, unlike a running original Run, grants no authority.
        await store.restarts.replace(batch, batch.model_copy(update={"state": "watching", "items": (item,)}))
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert calls == 1
        report = await app._store.restarts.get()
        assert all(result.state == "blocked" for result in report.results)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui"):
        assert calls == 1


async def test_terminal_retirement_keeps_the_admission_slot_until_old_registration_is_gone(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    first_started, finish_first, retiring, release_retirement, second_started = (Event() for _ in range(5))
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await finish_first.wait()
            return ModelResponse(parts=[TextPart("First finished")])
        second_started.set()
        await Event().wait()
        return ModelResponse(parts=[TextPart("Never")])

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        first = await app.submit_thread(thread_id=thread.thread_id, prompt="First")
        with fail_after(10):
            await first_started.wait()
        invalidate = app._restart.invalidate

        async def block_terminal_retirement(thread_id, *, suppress=False):
            if (await app._root_runs.get(first.receipt_id)).status == "completed":
                retiring.set()
                await release_retirement.wait()
            await invalidate(thread_id, suppress=suppress)

        monkeypatch.setattr(app._restart, "invalidate", block_terminal_retirement)
        finish_first.set()
        with fail_after(10):
            await retiring.wait()
        try:
            with pytest.raises(RunCoordinationError, match="active root operation"):
                await app.submit_thread(thread_id=thread.thread_id, prompt="Too early")
        finally:
            release_retirement.set()
        with fail_after(10):
            assert (await app.wait_root_operation(first.receipt_id)).status == "completed"
        second = await app.submit_thread(thread_id=thread.thread_id, prompt="Next")
        with fail_after(10):
            await second_started.wait()
        batch = await app._store.restarts.get()
        assert len(batch.items) == 1
        assert batch.items[0].run_id == (await app._root_runs.get(second.receipt_id)).run_id
        await app.cancel_root_operation(second.receipt_id)
        await app.wait_root_operation(second.receipt_id)


async def test_completed_root_does_not_block_saved_children_after_a_drain_timeout(tmp_path):
    from a13n_harness import HarnessState
    from a13n_harness_ui.restart import GracefulRestart
    from a13n_harness_ui.restart_models import RestartItem
    from a13n_harness_ui.storage.objects import ObjectKind, ObjectRef

    reference = ObjectRef(object_kind=ObjectKind.continuation, object_schema_version="1", logical_digest="d" * 64)
    async with open_local_store(_settings(tmp_path / "state").storage) as store:
        restart = GracefulRestart(store.restarts, enabled=True)
        restart.register("parent")
        restart.register("child", "child-execution", root_thread_id="parent")
        item = RestartItem(
            thread_id="parent",
            root_thread_id="parent",
            run_id="parent-run",
            cause="process_loss",
            composition=reference,
            checkpoint=reference,
        )
        await restart.boundary(item)
        # A final response withdraws the last request before normal completion.
        # Its historical boundary is not evidence of failed finalization.
        await restart.invalidate("parent")
        restart.requested = restart.committing = True
        restart.active["child"].state = HarnessState.new()
        child = item.model_copy(update={"thread_id": "child", "execution_id": "child-execution", "cause": "graceful"})
        restart.saved(child)
        restart.finished("parent")
        restart.finished("child")
        await restart.commit()
        assert not restart.errors
        assert (await store.restarts.get()).items == (child,)


@pytest.mark.parametrize("mode", ["state", "cleanup"])
async def test_model_timeout_with_failed_environment_finalization_is_not_recovered(tmp_path, monkeypatch, mode):
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state", shutdown_timeout_seconds=0.01)
    started = Event()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        started.set()
        await Event().wait()
        return ModelResponse(parts=[TextPart("Never")])

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        await app.submit_thread(thread_id=thread.thread_id, prompt="Wait")
        with fail_after(10):
            await started.wait()
        fail_environment_finalization(monkeypatch, mode)
    assert thread.thread_id in app._restart.errors
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is not None and not batch.items
        assert await store.restarts.claim() is None
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui"):
        assert calls == 1
