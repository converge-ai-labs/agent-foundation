"""Planned update pauses preserve work without accepting a durable input queue."""

import json

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.storage import StoredContinuation, open_local_store
from anyio import Event, fail_after, sleep
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


async def maintenance_phase(app, phase):
    with fail_after(15):
        while (view := await app.maintenance_status()).phase != phase:
            if view.phase == "blocked":
                pytest.fail(str(view))
            await sleep(0.01)
    return view


def test_update_instructions_belong_only_to_the_restored_run():
    from unittest.mock import Mock

    from a13n_harness_ui.maintenance import UpdateMaintenance, UpdatePauseCapability
    from a13n_harness_ui.storage.restarts import RestartRepository

    maintenance = UpdateMaintenance(Mock(spec=RestartRepository), enabled=True)
    maintenance.continued_threads.add("parent")
    maintenance.successors["parent"] = {"old-child": "new-child"}
    restored = UpdatePauseCapability(maintenance, "parent")
    maintenance.continued_threads.clear()
    maintenance.successors.clear()
    assert "old-child -> new-child" in restored.get_instructions()
    assert UpdatePauseCapability(maintenance, "parent").get_instructions() is None


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

    finalize = EnvironmentRunPlan.finalize

    async def failing(self, **kwargs):
        result = await finalize(self, **kwargs)
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

    monkeypatch.setattr(EnvironmentRunPlan, "finalize", failing)


@pytest.mark.parametrize("finalization_failure", [None, "state", "cleanup"])
async def test_planned_restart_continues_without_repeating_input_or_tool(tmp_path, monkeypatch, finalization_failure):
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
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
        return ModelResponse(parts=[TextPart("Finished after update")])

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Read and report")
        with fail_after(10):
            await started.wait()
        assert (
            await app.steer_root_operation(receipt_id=receipt.receipt_id, message="Keep the report concise")
        ).accepted
        assert (await app.prepare_update()).phase == "draining"
        assert not (await app.steer_root_operation(receipt_id=receipt.receipt_id, message="Not accepted")).accepted
        with pytest.raises(RunCoordinationError, match="maintenance"):
            await app.submit_thread(thread_id=thread.thread_id, prompt="Not accepted")
        release.set()
        await maintenance_phase(app, "paused")
        assert len(requests) == 1
        if finalization_failure:
            fail_environment_finalization(monkeypatch, finalization_failure)
    if finalization_failure:
        async with open_local_store(settings.storage) as store:
            batch = await store.restarts.get()
            assert batch is not None and batch.state == "blocked"
        async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
            assert (await app.maintenance_status()).phase == "blocked"
            await sleep(0.05)
        assert len(requests) == 1
        return
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is not None and batch.state == "ready", batch
        assert len(batch.items) == 1
        saved = await store.objects.read_model(batch.items[0].checkpoint, StoredContinuation)
        assert any(
            isinstance(p, ToolReturnPart) and p.tool_call_id == "read-once"
            for m in saved.harness_state.message_history
            for p in m.parts
        )
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        await maintenance_phase(app, "finished")
        with fail_after(10):
            while (await app.get_thread(thread.thread_id)).thread.completion is None:
                await sleep(0.01)
        assert len(requests) == 2
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
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui"):
        await sleep(0.05)
    assert len(requests) == 2


async def test_cancel_preparation_releases_original_run(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    requests = 0
    started, release = Event(), Event()

    async def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            started.set()
            await release.wait()
            return ModelResponse(parts=[ToolCallPart("store", {"key": "saved-result", "value": "Saved tool result"})])
        return ModelResponse(parts=[TextPart("Done")])

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Read")
        with fail_after(10):
            await started.wait()
        await app.prepare_update()
        release.set()
        await maintenance_phase(app, "paused")
        assert (await app.cancel_update()).phase == "idle"
        with fail_after(10):
            outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status == "completed"
        assert requests == 2
    async with open_local_store(settings.storage) as store:
        assert await store.restarts.get() is None


async def test_stop_before_pause_never_arms_recovery(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
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
        await app.prepare_update()
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        await sleep(0.05)
        assert (await app.maintenance_status()).phase == "blocked"
        assert requests == 1
        assert (await app.dismiss_update()).phase == "idle"


@pytest.mark.parametrize("parent_completes", [False, True])
@pytest.mark.parametrize("finalization_failure", [None, "state"])
async def test_child_restores_with_active_or_completed_parent(
    tmp_path, monkeypatch, parent_completes, finalization_failure
):
    import yaml

    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
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
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Delegate work")
        with fail_after(10):
            await child_started.wait()
            if parent_completes:
                assert (await app.wait_root_operation(receipt.receipt_id)).status == "completed"
            else:
                await parent_waiting.wait()
        await app.prepare_update()
        release_child.set()
        paused = await maintenance_phase(app, "paused")
        assert len(paused.tasks) == (1 if parent_completes else 2)
        assert calls["child"] == 1
        if finalization_failure:
            fail_environment_finalization(monkeypatch, finalization_failure)
    if finalization_failure:
        async with open_local_store(settings.storage) as store:
            batch = await store.restarts.get()
            assert batch is not None and batch.state == "blocked"
        async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
            assert (await app.maintenance_status()).phase == "blocked"
            await sleep(0.05)
        assert calls["child"] == 1
        return
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is not None and batch.state == "ready", batch
        assert len(batch.items) == (1 if parent_completes else 2)
        child_item = next(item for item in batch.items if item.execution_id is not None)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        recovered = await maintenance_phase(app, "finished")
        child = next(task for task in recovered.tasks if task.execution_id is not None)
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
        assert (await app.prepare_update()).phase == "paused"
        await sleep(0.4)
        waiting = await app.thread_decisions(thread_id=thread.thread_id)
        assert waiting is not None and waiting.expires_at is None
        assert waiting.continuation_id == before.continuation_id
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        await maintenance_phase(app, "finished")
        await sleep(0.4)
        waiting = await app.thread_decisions(thread_id=thread.thread_id)
        assert waiting is not None and waiting.expires_at is None
        assert waiting.continuation_id == before.continuation_id
    assert requests == 1


async def test_handoff_claim_is_single_use_and_requires_explicit_resolution(tmp_path):
    from a13n_harness_ui.maintenance_models import RestartBatch
    from anyio import create_task_group

    settings = _settings(tmp_path / "state")
    async with open_local_store(settings.storage) as first, open_local_store(settings.storage) as second:
        batch = RestartBatch(batch_id="restart-test", owner_id="old", state="preparing")
        await first.restarts.begin(batch)
        assert await second.restarts.claim("new") is None
        ready = batch.model_copy(update={"state": "ready"})
        await first.restarts.replace(batch, ready)
        claimed = []

        async def claim(store, owner):
            claimed.append(await store.restarts.claim(owner))

        async with create_task_group() as group:
            group.start_soon(claim, first, "one")
            group.start_soon(claim, second, "two")
        assert sum(item is not None for item in claimed) == 1
        assert await first.restarts.claim("three") is None
        with pytest.raises(RunCoordinationError):
            await first.restarts.require_admission()
        with pytest.raises(RunCoordinationError):
            await first.restarts.clear(ready)


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
        await app.prepare_update()
        assert (await app.cancel_root_operation(receipt.receipt_id)).accepted
        assert (await app.wait_root_operation(receipt.receipt_id)).status == "cancelled"
        assert (await app.maintenance_status()).phase == "paused"
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is not None and batch.state == "ready" and not batch.items
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        await maintenance_phase(app, "finished")
    assert calls == 1


async def test_incompatible_reconstruction_is_blocked_and_never_retried(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    started, release = Event(), Event()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return ModelResponse(parts=[ToolCallPart("store", {"key": "once", "value": 1})])

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        await app.submit_thread(thread_id=thread.thread_id, prompt="Work")
        with fail_after(10):
            await started.wait()
        await app.prepare_update()
        release.set()
        await maintenance_phase(app, "paused")

    async def unavailable(self, context, model_id):
        raise ValueError("Synthetic incompatible model implementation")

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", unavailable)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        blocked = await maintenance_phase(app, "blocked")
        assert calls == 1
        assert blocked.can_dismiss
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert (await app.maintenance_status()).phase == "blocked"
        await sleep(0.05)
        assert calls == 1
