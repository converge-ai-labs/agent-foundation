from __future__ import annotations

import json
from pathlib import Path

import pytest
from a13n_harness.capabilities.working_state import WORKING_STATE_CAPABILITY_ID
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.root_execution import RootRunExecutor
from a13n_harness_ui.surfaces import RootOperationStatus
from anyio import Event, fail_after
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings
from .test_thread_collaboration import configuration, controller

pytestmark = pytest.mark.anyio


async def test_work_observes_unsaved_notes_and_restores_all_tasks_on_idle_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = configuration(tmp_path)
    checkpoint_entered, allow_checkpoint = Event(), Event()
    reply_entered, allow_reply = Event(), Event()
    calls = 0
    select = RootRunExecutor._select_state

    async def gated_checkpoint(self, **kwargs):
        state = kwargs["state"]
        entry = state.agent_context_state.entries.get(WORKING_STATE_CAPABILITY_ID) if state else None
        if entry is not None and entry.data.get("notes") and not allow_checkpoint.is_set():
            checkpoint_entered.set()
            await allow_checkpoint.wait()
        return await select(self, **kwargs)

    async def resolve(self, context, model_id):
        async def model(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield {
                    0: DeltaToolCall(
                        name="task_create",
                        json_args=json.dumps({"subject": "Retained", "description": "Keep unchanged"}),
                        tool_call_id="task",
                    ),
                    1: DeltaToolCall(
                        name="note_write",
                        json_args=json.dumps({"key": "decision", "value": "unsaved"}),
                        tool_call_id="note",
                    ),
                }
            elif calls == 3:
                reply_entered.set()
                await allow_reply.wait()
                yield {0: DeltaToolCall(name="note_delete", json_args='{"key":"decision"}', tool_call_id="delete")}
            else:
                yield "done"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    monkeypatch.setattr(RootRunExecutor, "_select_state", gated_checkpoint)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=path, host_mode="webui", instrumentation=None) as app:
        root = await app.create_thread()
        sidekick = await app.create_thread()
        async with app._summary_hub.subscribe() as hints:
            receipt = await app.submit_thread(thread_id=root.thread_id, prompt="Remember work")
            try:
                with fail_after(10):
                    await checkpoint_entered.wait()
                work = await app.thread_work(thread_id=root.thread_id, include=("tasks", "notes"))
                assert work.source == "live"
                assert work.tasks.total == 1 and work.notes.total == 1
                assert work.notes.page is not None and work.notes.page.notes[0].value == "unsaved"
                assert (await app.thread_notes(thread_id=root.thread_id)).total == 0
                assert (await app.thread_tasks(thread_id=root.thread_id)).total == 0
                assert (await app.thread_work(thread_id=sidekick.thread_id)).notes.total == 0
                with fail_after(5):
                    while True:
                        hint = await hints.receive()
                        if hint.kind == "thread_work" and hint.work_revision == work.revision:
                            assert hint.thread_id == root.thread_id
                            assert "unsaved" not in hint.model_dump_json()
                            break
                first_run = work.run_id
            finally:
                allow_checkpoint.set()
            assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        saved = await app.thread_work(thread_id=root.thread_id)
        assert saved.source == "saved" and saved.tasks.total == 1 and saved.notes.total == 1
        assert saved.tasks.page is None and saved.notes.page is None
        report = await controller(app).send_thread_message(
            source_thread_id=sidekick.thread_id,
            thread_id=root.thread_id,
            message="Sidekick findings",
        )
        try:
            assert report["ok"] and report["mode"] == "run"
            with fail_after(10):
                await reply_entered.wait()
            restored = await app.thread_work(thread_id=root.thread_id, include=("tasks",))
            assert restored.source == "live" and restored.run_id != first_run
            assert restored.revision == 1 and restored.tasks.total == 1
            assert restored.tasks.page is not None and restored.tasks.page.tasks[0].subject == "Retained"
        finally:
            allow_reply.set()
        assert (await app.wait_root_operation(report["receipt"]["receipt_id"])).status is RootOperationStatus.completed
        final = await app.thread_work(thread_id=root.thread_id)
        assert final.source == "saved" and final.notes.total == 0 and final.tasks.total == 1
    async with open_harness_ui_app(settings, configuration_path=path, instrumentation=None) as restarted:
        work = await restarted.thread_work(thread_id=root.thread_id)
        assert work.source == "saved" and work.run_id is None
        assert work.epoch != saved.epoch
        assert work.tasks.total == 1 and work.notes.total == 0


@pytest.mark.parametrize("completed_run", [False, True])
async def test_saved_work_and_details_never_load_continuations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, completed_run: bool
) -> None:
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {0: DeltaToolCall(name="note_write", json_args='{"key":"saved","value":"work"}')}
        else:
            yield "Saved work"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    path = configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path, instrumentation=None) as app:
        root = await app.create_thread()
        if completed_run:
            receipt = await app.submit_thread(thread_id=root.thread_id, prompt="Save work")
            assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed

        async def forbidden(*args, **kwargs):
            pytest.fail("Small work queries must not load immutable objects")

        with monkeypatch.context() as patch:
            patch.setattr(app._store.objects, "read_model", forbidden)
            patch.setattr(app._store.objects, "read", forbidden)
            work = await app.thread_work(thread_id=root.thread_id)
            assert await app.thread_work(thread_id=root.thread_id) == work
            detailed = await app.thread_work(thread_id=root.thread_id, include=("tasks", "notes"))
            assert detailed.notes.total == int(completed_run)
            assert (await app.thread_notes(thread_id=root.thread_id)).total == int(completed_run)
            assert (await app.thread_tasks(thread_id=root.thread_id)).total == 0
        assert work.source == ("saved" if completed_run else "unavailable")
        assert work.notes.total == int(completed_run)
        if completed_run:
            from a13n_harness_ui.errors import ThreadError
            from a13n_harness_ui.storage.database import transaction
            from a13n_harness_ui.storage.models import ThreadWorkRecord
            from sqlalchemy import delete

            async with transaction(app._store.database.sessions) as session:
                await session.execute(delete(ThreadWorkRecord).where(ThreadWorkRecord.thread_id == root.thread_id))
            with monkeypatch.context() as patch:
                patch.setattr(app._store.objects, "read_model", forbidden)
                patch.setattr(app._store.objects, "read", forbidden)
                missing = await app.thread_work(thread_id=root.thread_id, include=("tasks", "notes"))
                assert missing.source == "unavailable" and not missing.notes.available
                with pytest.raises(ThreadError) as error:
                    await app.thread_notes(thread_id=root.thread_id)
                assert error.value.code == "thread_work_unavailable"
            assert await app._store.repair_read_models() == (root.thread_id,)
            assert (await app.thread_work(thread_id=root.thread_id)).notes.total == 1
    async with open_harness_ui_app(
        _settings(tmp_path / "data"), configuration_path=path, instrumentation=None
    ) as restarted:
        with monkeypatch.context() as patch:
            patch.setattr(restarted._store.objects, "read_model", forbidden)
            patch.setattr(restarted._store.objects, "read", forbidden)
            saved = await restarted.thread_work(thread_id=root.thread_id, include=("tasks", "notes"))
            assert saved.notes.total == int(completed_run)
            assert (await restarted.thread_notes(thread_id=root.thread_id)).total == int(completed_run)


async def test_work_counts_complete_state_without_expanding_bounded_pages(tmp_path: Path) -> None:
    from a13n_harness.capabilities.working_state import Task, TaskState, WorkingState, WorkingStateObservation

    path = configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path, instrumentation=None) as app:
        root = await app.create_thread()
        tasks = TaskState(
            next_task_sequence=301,
            tasks={
                f"task-{index}": Task(
                    id=f"task-{index}",
                    version=1,
                    subject=f"Task {index}",
                    description="Bounded detail",
                    status="in_progress" if index == 300 else "completed",
                )
                for index in range(1, 301)
            },
        )
        state = WorkingState(tasks=tasks, notes={f"note-{index}": "retained" for index in range(300)})
        app._work.observe(
            root.thread_id,
            WorkingStateObservation(run_id="run-large", revision=1, notes_version=1, state=state, tasks=tasks),
            base_continuation_id=None,
        )
        summary = await app.thread_work(thread_id=root.thread_id)
        assert summary.tasks.total == summary.notes.total == 300
        assert summary.tasks.completed == 299
        assert summary.tasks.active is not None and summary.tasks.active.task_id == "task-300"
        assert summary.tasks.page is summary.notes.page is None
        detail = await app.thread_work(thread_id=root.thread_id, include=("tasks", "notes"))
        assert detail.tasks.page is not None and len(detail.tasks.page.tasks) == 256
        assert detail.tasks.page.omitted == 44
        assert detail.notes.page is not None and len(detail.notes.page.notes) == 256
        assert detail.notes.page.omitted == 44
        await app._work.finish(root.thread_id, "older-run")
        assert (await app.thread_work(thread_id=root.thread_id)).source == "live"
        await app._work.finish(root.thread_id, "run-large")
        assert (await app.thread_work(thread_id=root.thread_id)).source != "live"


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "cancelled"])
async def test_child_work_counts_and_cleanup_hint_do_not_leak_private_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    from anyio import sleep
    from pydantic_ai.exceptions import UsageLimitExceeded

    path = configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "subagents:\n  - agent: agent-worker\n")
    entered, finish = Event(), Event()
    root_calls = child_calls = 0

    async def resolve(self, context, model_id):
        async def model(messages, info):
            nonlocal root_calls, child_calls
            if self._recipes[model_id].model_id == "model-secondary":
                child_calls += 1
                if child_calls == 1:
                    yield {
                        0: DeltaToolCall(
                            name="note_write",
                            tool_call_id="private-note",
                            json_args='{"key":"private","value":"child only"}',
                        ),
                        1: DeltaToolCall(
                            name="task_create",
                            tool_call_id="private-task",
                            json_args='{"subject":"Private child task","description":"Not root work"}',
                        ),
                    }
                    return
                entered.set()
                await finish.wait()
                if outcome == "failed":
                    raise UsageLimitExceeded("Fixture child failure")
                yield "child complete"
            else:
                root_calls += 1
                if root_calls == 1:
                    yield {
                        0: DeltaToolCall(
                            name="delegate",
                            tool_call_id="child",
                            json_args='{"subagent_name":"agent-worker","prompt":"Private work"}',
                        )
                    }
                else:
                    yield "root complete"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(
        _settings(tmp_path / "data"), configuration_path=path, host_mode="webui", instrumentation=None
    ) as app:
        root = await app.create_thread()
        receipt = await app.submit_thread(thread_id=root.thread_id, prompt="Delegate")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed, operation
        page = await app.query_child_executions(parent_thread_id=root.thread_id)
        assert page.executions, await app.thread_transcript(thread_id=root.thread_id)
        try:
            with fail_after(10):
                await entered.wait()
            work = await app.thread_work(thread_id=root.thread_id)
            assert work.children.running == work.children.active == 1
            assert work.tasks.total == work.notes.total == 0
            page = await app.query_child_executions(parent_thread_id=root.thread_id)
            child = page.executions[0]
            async with app._summary_hub.subscribe() as hints:
                if outcome == "cancelled":
                    assert (
                        await app.cancel_child_execution(
                            parent_thread_id=root.thread_id, execution_id=child.execution_id
                        )
                    ).accepted
                else:
                    finish.set()
                with fail_after(10):
                    while child.execution_id in await app._subagent_operator.active_execution_ids():
                        await sleep(0.01)
                    while True:
                        hint = await hints.receive()
                        if hint.kind == "child_execution" and hint.execution_id == child.execution_id:
                            observed = await app.thread_work(thread_id=root.thread_id)
                            if observed.children.active == 0 and observed.children.running == 0:
                                break
                assert observed.children.model_dump()[outcome] == 1
                assert observed.tasks.total == observed.notes.total == 0
        finally:
            finish.set()
