"""Planned update pauses preserve work without accepting a durable input queue."""

import json

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.storage import StoredContinuation, open_local_store
from anyio import CancelScope, Event, create_task_group, fail_after, sleep
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


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
            assert batch is None
        async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
            await sleep(0.05)
        assert len(requests) == 1
        return
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is not None and batch.state == "ready", batch
        assert len(batch.items) == 1
        saved = await store.objects.read_model(batch.items[0].checkpoint, StoredContinuation)
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
                object_kind=ObjectKind.continuation, value=saved.model_copy(update={"harness_state": legacy_state})
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
        continuation = await store.objects.read_model(current.continuation, StoredContinuation)
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
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui"):
        await sleep(0.05)
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
        assert await store.restarts.get() is None


async def test_drain_timeout_never_arms_recovery(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state", shutdown_timeout_seconds=0.05)
    started = Event()
    requests = 0

    async def model(messages, info):
        nonlocal requests
        requests += 1
        started.set()
        await Event().wait()
        return ModelResponse(parts=[TextPart("Never returned")])

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        await app.submit_thread(thread_id=thread.thread_id, prompt="Wait")
        with fail_after(10):
            await started.wait()
    assert not app._restart.committing
    assert "app" in app._restart.errors
    async with open_local_store(settings.storage) as store:
        assert await store.restarts.get() is None
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert await app._root_runs.active_count() == 0
        assert requests == 1


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
        group.start_soon(release_during_shutdown, app, release_child)
        if finalization_failure:
            fail_environment_finalization(monkeypatch, finalization_failure)
    # Finalization failures must not pass because an earlier drain timed out.
    assert app._restart.committing, app._restart.errors
    if finalization_failure:
        async with open_local_store(settings.storage) as store:
            batch = await store.restarts.get()
            assert batch is None
        async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
            await sleep(0.05)
        assert calls["child"] == 1
        return
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is not None and batch.state == "ready", batch
        assert len(batch.items) == (1 if parent_completes else 2)
        child_item = next(item for item in batch.items if item.execution_id is not None)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        recovered = await app._store.restarts.get()
        assert recovered is not None and recovered.state == "consumed"
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
        await sleep(0.4)
        waiting = await app.thread_decisions(thread_id=thread.thread_id)
        assert waiting is not None and waiting.expires_at is None
        assert waiting.continuation_id == before.continuation_id
    assert requests == 1


async def test_handoff_consumption_is_atomic_and_does_not_require_reset(tmp_path):
    from a13n_harness_ui.restart_models import RestartBatch

    settings = _settings(tmp_path / "state")
    async with open_local_store(settings.storage) as first, open_local_store(settings.storage) as second:
        batch = RestartBatch(batch_id="restart-test", state="ready")
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
        next_batch = RestartBatch(batch_id="restart-next", state="ready")
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
        assert batch is None
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui"):
        await sleep(0.05)
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
        await sleep(0.05)
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
        assert report is not None and report.state == "consumed"
        await sleep(0.05)
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
