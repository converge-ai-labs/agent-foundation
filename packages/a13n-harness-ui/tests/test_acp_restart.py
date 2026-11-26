"""Exercise ACP restart handoff across App lifetimes with a real stdio peer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from a13n_harness.acp import AcpSessionRuntime
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration.models import AcpAgentResource
from a13n_harness_ui.goal import GoalCapability, saved_goal
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.storage import open_local_store
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus
from anyio import Event, create_task_group, fail_after, sleep
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart

from .test_app import _write_configuration
from .test_restart_recovery import install_model, release_during_shutdown

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]
_PEER = Path(__file__).parent / "fixtures" / "acp_restart_peer.py"
_PROMPT = "Finish the original work."
_GUIDANCE = "Initial Agent instructions from the captured setup."


def configure(tmp_path, transport="resume", behavior="interrupt", shutdown_timeout_seconds=5):
    config = _write_configuration(tmp_path)
    journal = tmp_path / "acp.jsonl"
    resource = AcpAgentResource(
        schema_version="1",
        kind="acp_agent",
        id="acp-restart",
        name="Restart Fixture",
        instructions=_GUIDANCE,
        command=sys.executable,
        args=(str(_PEER), str(journal), transport, behavior),
    )
    folder = tmp_path / "acp_agents"
    folder.mkdir()
    (folder / "restart.yaml").write_text(resource.model_dump_json(exclude_none=True))
    settings = HarnessUiSettings(
        storage=StorageSettings(data_root=tmp_path / "data"),
        shutdown_timeout_seconds=shutdown_timeout_seconds,
        pricing_auto_update=False,
    )
    return settings, config, journal


def protocol(journal, method=None):
    if not journal.exists():
        return []
    messages = [json.loads(line) for line in journal.read_text().splitlines()]
    return [item for item in messages if method is None or item["method"] == method]


async def wait_for_prompt(journal, number=1):
    with fail_after(10):
        while len(protocol(journal, "session/prompt")) < number:
            await sleep(0.01)


async def new_acp_thread(app, **kwargs):
    return await app.create_thread(defaults=NewThreadDefaults(agent_id="acp-restart"), **kwargs)


async def saved_batch(settings):
    async with open_local_store(settings.storage) as store:
        return await store.restarts.get()


async def checkpoint(store, thread_id, reference):
    return await store.read_continuation(thread_id, reference, include_display=True)


async def wait_for_completion(app, thread_id):
    with fail_after(10):
        while True:
            thread = await app._store.threads.get(thread_id)
            if thread is not None and thread.completion is not None:
                return thread
            await sleep(0.01)


@pytest.mark.parametrize("transport", ["resume", "load"])
async def test_acp_shutdown_reopens_session_without_repeating_user_input(tmp_path, monkeypatch, transport):
    settings, config, journal = configure(tmp_path, transport)
    prompt = AcpSessionRuntime.prompt

    async def require_release(self, content, *, binding, context=()):
        if any("The host application restarted" in part for part in context):
            batch = await saved_batch(settings)
            assert batch is not None and batch.state == "consumed" and batch.results[0].state == "restored"
        return await prompt(self, content, binding=binding, context=context)

    monkeypatch.setattr(AcpSessionRuntime, "prompt", require_release)
    async with open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app:
        thread = await new_acp_thread(app)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=_PROMPT)
        await wait_for_prompt(journal)
        offered = await app.steer_root_operation(
            receipt_id=receipt.receipt_id, message="Undispatched restart steering", allow_acp_interrupt=False
        )
        assert offered.accepted
        assert await app._store.restarts.get() is None  # No process-loss candidate while ACP is active.
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is not None and batch.state == "ready"
        (item,) = batch.items
        assert item.thread_id == thread.thread_id and item.cause == "graceful"
        saved = await checkpoint(store, thread.thread_id, item.checkpoint)
        session = saved.harness_state.agent_context_state.get("a13n.acp")
        assert session is not None
        assert session.data["session_id"] == "restart-fixture" and session.data["prompted"] is True
        assert session.data["context_window"] == [34, 100] and session.data["synced_messages"] == 2
        assert session.data["options"] == {"mode": "plan"}
        assert session.data["pending_prompt"] is None
        assert "Undispatched restart steering" not in str(saved.harness_state.message_history)
        assert "Partial reply 1." in str(saved.harness_state.message_history)
        assert await store.threads.acp_state(thread.thread_id) is not None
    cancels = protocol(journal, "session/cancel")
    assert len(cancels) == 1 and cancels[0]["params"]["sessionId"] == "restart-fixture"

    async with open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app:
        completed = await wait_for_completion(app, thread.thread_id)
        batch = await app._store.restarts.get()
        assert batch is not None and batch.state == "consumed" and batch.results[0].state == "restored"
        saved = await checkpoint(app._store, thread.thread_id, completed.continuation)
        user_parts = [
            part
            for message in saved.harness_state.message_history
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        assert len(user_parts) == 1 and _PROMPT in str(user_parts[0].content)
        assert "host application restarted" not in str(saved.harness_state.message_history)
        transcript = await app.get_thread_transcript(thread_id=thread.thread_id)
        shown = [part.text for entry in transcript.entries for part in entry.parts if part.text]
        assert shown.count(_PROMPT) == 1
        assert "Partial reply 1." in shown and "ACP finished." in shown
    methods = [message["method"] for message in protocol(journal)]
    assert methods.count("session/new") == 1 and methods.count(f"session/{transport}") == 1
    assert methods[-3:] == [f"session/{transport}", "session/set_config_option", "session/prompt"]
    _, resumed = protocol(journal, "session/prompt")
    text = str(resumed["params"]["prompt"])
    assert "The host application restarted" in text and "retained outputs" in text and _PROMPT not in text
    assert "Undispatched restart steering" not in text


async def test_acp_natural_completion_during_drain_is_not_replayed(tmp_path):
    settings, config, journal = configure(tmp_path, behavior="natural")
    async with open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app:
        thread = await new_acp_thread(app)
        await app.submit_thread(thread_id=thread.thread_id, prompt=_PROMPT)
        await wait_for_prompt(journal)
    assert len(protocol(journal, "session/cancel")) == 1
    assert await saved_batch(settings) is None
    async with open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app:
        assert (await app.get_thread(thread.thread_id)).thread.completion is not None
        assert await app._root_runs.active(thread.thread_id) is None
    assert len(protocol(journal, "session/prompt")) == 1


@pytest.mark.parametrize("race", [False, True])
async def test_acp_user_stop_excludes_restart(tmp_path, monkeypatch, race):
    settings, config, journal = configure(tmp_path)
    watcher_entered = Event()
    original = AcpSessionRuntime.cancel
    if race:

        async def cancel(self):
            if not watcher_entered.is_set():
                watcher_entered.set()
                await Event().wait()  # Explicit Stop wins before the shutdown watcher can send cancel.
            return await original(self)

        monkeypatch.setattr(AcpSessionRuntime, "cancel", cancel)

    async with (
        create_task_group() as group,
        open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app,
    ):
        thread = await new_acp_thread(app)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=_PROMPT)
        await wait_for_prompt(journal)

        async def stop():
            with fail_after(10):
                if race:
                    await watcher_entered.wait()
                assert (await app._root_runs.cancel(receipt.receipt_id)).accepted
                assert (await app._root_runs.wait(receipt.receipt_id)).status == RootOperationStatus.cancelled

        if race:
            group.start_soon(stop)
        else:
            await stop()
    assert len(protocol(journal, "session/cancel")) == 1
    assert await saved_batch(settings) is None


@pytest.mark.parametrize("case", ["unsupported", "resume-error", "options-error"])
async def test_acp_restart_exclusion_or_failure_does_not_block_native_family(tmp_path, monkeypatch, case):
    unsupported = case == "unsupported"
    settings, config, journal = configure(tmp_path, "none" if unsupported else "resume", case)
    native_started, native_release = Event(), Event()
    requests = 0

    async def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            native_started.set()
            await native_release.wait()
            # A tool response advances the native Run to a graceful model boundary.
            from pydantic_ai.messages import ToolCallPart

            return ModelResponse(parts=[ToolCallPart("store", {"key": "native", "value": True})])
        return ModelResponse(parts=[TextPart("Native finished")])

    install_model(monkeypatch, model)
    async with (
        create_task_group() as group,
        open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app,
    ):
        acp = await new_acp_thread(app)
        native = await app.create_thread()
        await app.submit_thread(thread_id=acp.thread_id, prompt=_PROMPT)
        await app.submit_thread(thread_id=native.thread_id, prompt="Native request")
        await wait_for_prompt(journal)
        with fail_after(10):
            await native_started.wait()
        if unsupported:
            assert acp.thread_id not in app._restart.active  # Drain will not wait on the hanging peer.
        group.start_soon(release_during_shutdown, app, native_release)
    batch = await saved_batch(settings)
    assert batch is not None and batch.state == "ready"
    assert {item.thread_id for item in batch.items} == (
        {native.thread_id} if unsupported else {native.thread_id, acp.thread_id}
    )
    async with open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app:
        batch = await app._store.restarts.get()
        assert batch is not None and batch.state == "watching"
        assert {item.thread_id: item.state for item in batch.results} == (
            {native.thread_id: "restored"} if unsupported else {native.thread_id: "restored", acp.thread_id: "blocked"}
        )
        restored_native = await app._root_runs.active(native.thread_id) or await app._root_runs.latest(native.thread_id)
        assert restored_native is not None
        assert (
            await app.wait_root_operation(restored_native.receipt.receipt_id)
        ).status == RootOperationStatus.completed
        receipt = await app.submit_thread(thread_id=native.thread_id, prompt="New native work")
        assert (await app.wait_root_operation(receipt.receipt_id)).status == RootOperationStatus.completed
    assert len(protocol(journal, "session/prompt")) == 1


async def test_acp_chained_graceful_restarts_resume_and_notify_coordinator(tmp_path, monkeypatch):
    settings, config, journal = configure(tmp_path, behavior="chained")
    notices = []

    async def model(messages, info):
        notices.append(str(messages))
        return ModelResponse(parts=[TextPart("Noted the saved completion.")])

    install_model(monkeypatch, model)
    async with open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app:
        lead = await app.create_thread(coordinator=True)
        worker = await new_acp_thread(app, coordinator_thread_id=lead.thread_id)
        await app._root_runs.submit_prompt(thread_id=worker.thread_id, prompt=_PROMPT)
        await wait_for_prompt(journal)
    first = await saved_batch(settings)
    assert first is not None and first.state == "ready"
    async with open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app:
        await wait_for_prompt(journal, 2)
        consumed = await app._store.restarts.get()
        assert consumed is not None and consumed.state == "consumed"  # ACP still does not arm process-loss.
    second = await saved_batch(settings)
    assert second is not None and second.state == "ready" and second.batch_id != first.batch_id
    assert [item.thread_id for item in second.items] == [worker.thread_id]
    async with open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app:
        await wait_for_completion(app, worker.thread_id)
        await wait_for_completion(app, lead.thread_id)
        assert len(notices) == 1 and "Status: completed" in notices[0] and worker.thread_id in notices[0]
    assert len(protocol(journal, "session/resume")) == len(protocol(journal, "session/cancel")) == 2


@pytest.mark.parametrize("phase", ["session-open", "goal-boundary", "goal-interrupted", "goal-compaction"])
async def test_acp_restores_undispatched_prompt_and_goal_progress(tmp_path, monkeypatch, phase):
    goal = phase != "session-open"
    behavior = phase if goal else "early"
    settings, config, journal = configure(tmp_path, behavior=behavior)
    reached, release = Event(), Event()
    if phase == "session-open":
        open_session = AcpSessionRuntime.open_session

        async def open_at_shutdown(self, *args, **kwargs):
            result = await open_session(self, *args, **kwargs)
            if kwargs["saved_session_id"] is None:
                reached.set()
                await release.wait()
            return result

        monkeypatch.setattr(AcpSessionRuntime, "open_session", open_at_shutdown)
    elif phase == "goal-boundary":
        after_reply = GoalCapability.after_reply

        async def settle_goal(self, *args, **kwargs):
            await after_reply(self, *args, **kwargs)
            if not reached.is_set():
                reached.set()
                await release.wait()

        monkeypatch.setattr(GoalCapability, "after_reply", settle_goal)
    async with (
        create_task_group() as group,
        open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app,
    ):
        thread = await new_acp_thread(app)
        await app.submit_thread(thread_id=thread.thread_id, prompt=_PROMPT, mode="goal" if goal else "normal")
        if phase in {"goal-interrupted", "goal-compaction"}:
            await wait_for_prompt(journal)
        else:
            with fail_after(10):
                await reached.wait()
            group.start_soon(release_during_shutdown, app, release)
    assert len(protocol(journal, "session/prompt")) == int(goal)
    assert len(protocol(journal, "session/cancel")) == int(phase in {"goal-interrupted", "goal-compaction"})
    batch = await saved_batch(settings)
    assert batch is not None and batch.state == "ready"
    if goal:
        async with open_local_store(settings.storage) as store:
            paused = saved_goal((await checkpoint(store, thread.thread_id, batch.items[0].checkpoint)).harness_state)
            assert paused is not None and paused.active and paused.objective == _PROMPT
            assert paused.iteration == int(phase == "goal-boundary")
            if phase in {"goal-interrupted", "goal-compaction"}:
                assert (paused.input_tokens, paused.output_tokens) == (11, 7)
            assert paused.needs_restore_audit is (phase == "goal-compaction")
    async with open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app:
        completed = await wait_for_completion(app, thread.thread_id)
        if goal:
            result = saved_goal((await checkpoint(app._store, thread.thread_id, completed.continuation)).harness_state)
            iteration = paused.iteration + int(phase == "goal-compaction")
            assert result is not None and result.status == "verified" and result.iteration == iteration
    resumed = protocol(journal, "session/prompt")[int(goal)]
    text = str(resumed["params"]["prompt"])
    assert "The host application restarted" in text
    if phase == "session-open":
        assert text.count(_PROMPT) == 1 and text.count(_GUIDANCE) == 1
    elif phase == "goal-boundary":
        assert "[GOAL_COMPLETE]" in text and _PROMPT in text


@pytest.mark.parametrize("exit_mode", ["cli", "abnormal"])
async def test_acp_cli_and_abnormal_exit_do_not_publish_handoffs(tmp_path, exit_mode):
    settings, config, journal = configure(tmp_path)

    async def run():
        async with open_harness_ui_app(
            settings, configuration_path=config, host_mode="cli" if exit_mode == "cli" else "webui"
        ) as app:
            thread = await new_acp_thread(app)
            await app.submit_thread(thread_id=thread.thread_id, prompt=_PROMPT)
            await wait_for_prompt(journal)
            assert await app._store.restarts.get() is None
            if exit_mode == "abnormal":
                raise RuntimeError("Abnormal exit")

    if exit_mode == "abnormal":
        with pytest.RaisesGroup(pytest.RaisesExc(RuntimeError, match="Abnormal exit")):
            await run()
    else:
        await run()
    assert await saved_batch(settings) is None
    assert len(protocol(journal, "session/prompt")) == 1
