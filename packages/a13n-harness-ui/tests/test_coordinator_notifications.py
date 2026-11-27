from __future__ import annotations

from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import StoreConflictError
from a13n_harness_ui.live import InboxNotice, RootOperationNotice
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.storage import ThreadConfigurationMutation, open_local_store
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus, ThreadMetadataMutation
from a13n_harness_ui.web_push import WebPush
from anyio import Event, create_task_group, fail_after
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings
from .test_coordinators import coordinator_configuration, create_coordinator
from .test_restart_recovery import release_during_shutdown
from .test_thread_collaboration import controller

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


async def test_followup_setting_persists_without_running(tmp_path: Path) -> None:
    root = coordinator_configuration(tmp_path)
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        lead = await create_coordinator(app)
        for enabled in (False, True, False):
            changed = await app.set_auto_followup(lead.thread_id, enabled)
            assert changed.auto_followup is enabled and changed.role == "coordinator"
            assert changed.continuation_state == "initial"
        assert await app._root_runs.active_count() == 0
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert (await app.get_thread(lead.thread_id)).thread.auto_followup is False


@pytest.mark.parametrize("active", [False, True])
async def test_worker_completion_runs_or_steers_lead_and_notifies_only_for_lead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, active: bool
) -> None:
    root = coordinator_configuration(tmp_path)
    lead_id = ""
    started, release, notified = Event(), Event(), Event()
    notices = []
    pushed: list[tuple[str, RootOperationNotice | InboxNotice]] = []
    original_enqueue = WebPush.enqueue

    def observe_enqueue(self: WebPush, thread_id: str, notice: RootOperationNotice | InboxNotice) -> None:
        pushed.append((thread_id, notice))
        original_enqueue(self, thread_id, notice)

    monkeypatch.setattr(WebPush, "enqueue", observe_enqueue)

    async def resolve(self, context, model_id):
        async def model(messages, info):
            if context.deps.thread_id == lead_id:
                if "Host notification:" in str(messages):
                    notices.append(str(messages))
                    notified.set()
                    yield "Checked the saved outcome."
                else:
                    yield "Coordinating."
                    started.set()
                    await release.wait()
            else:
                yield "Work completed."

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with (
        open_harness_ui_app(settings, configuration_path=root, host_mode="webui", instrumentation=None) as app,
        app.summary_events() as summaries,
    ):
        lead = await create_coordinator(app, "project-main")
        lead_id = lead.thread_id
        worker = await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-main"), coordinator_thread_id=lead_id
        )
        settled, lead_settled = Event(), Event()
        original = app._root_runs._on_settled
        assert original is not None

        async def observe(project_id, operation):
            await original(project_id, operation)
            if operation.receipt.thread_id == worker.thread_id:
                settled.set()
            if operation.receipt.thread_id == lead_id:
                lead_settled.set()

        app._root_runs._on_settled = observe
        with fail_after(15):
            initial = None
            if active:
                initial = await app.submit_thread(thread_id=lead_id, prompt="Coordinate")
                await started.wait()
            receipt = await app._root_runs.submit_prompt(thread_id=worker.thread_id, prompt="Work")
            assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
            await settled.wait()
            release.set()
            await notified.wait()
            target = await app._root_runs.active(lead_id) or await app._root_runs.latest(lead_id)
            assert target is not None
            if initial is not None:
                assert target.receipt.receipt_id == initial.receipt_id
            assert (await app.wait_root_operation(target.receipt.receipt_id)).status is RootOperationStatus.completed
            await lead_settled.wait()
            assert await app._root_runs.active(lead_id) is None
            async for summary in summaries:
                if summary.thread_id == worker.thread_id:
                    assert summary.notice is None
                if summary.notice is not None and summary.notice.receipt_id == target.receipt.receipt_id:
                    assert summary.thread_id == lead_id and summary.notice.status == "completed"
                    assert pushed == [(lead_id, summary.notice)]
                    break
            else:
                pytest.fail("Coordinator completion notice was not published")
        assert len(notices) == 1
        assert receipt.receipt_id in notices[0] and worker.thread_id in notices[0]
        assert "Status: completed" in notices[0]
        history = await app.get_thread_transcript(thread_id=lead_id)
        notice_parts = [
            part for entry in history.entries for part in entry.parts if "Host notification:" in (part.text or "")
        ]
        assert len(notice_parts) == 1 and notice_parts[0].metadata.display is False


@pytest.mark.parametrize("case", ["mode_off", "other_project", "archived"])
async def test_terminal_notice_respects_project_and_current_host_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    root = coordinator_configuration(tmp_path)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        lead = await create_coordinator(app, "project-main")
        if case == "mode_off":
            await app.set_auto_followup(lead.thread_id, False)
    calls = []

    async def resolve(self, context, model_id):
        async def model(messages, info):
            calls.append(context.deps.thread_id)
            yield "Done."

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(
        settings, configuration_path=root, host_mode="local" if case == "terminal" else "webui", instrumentation=None
    ) as app:
        project_id = "project-other" if case == "other_project" else None if case == "no_project" else "project-main"
        worker = await app.create_thread(
            defaults=NewThreadDefaults(project_id=project_id),
            coordinator_thread_id=lead.thread_id if project_id == "project-main" and case != "independent" else None,
        )
        if case == "archived":
            await app.update_thread_metadata(
                thread_id=lead.thread_id,
                mutation=ThreadMetadataMutation.model_validate(
                    {
                        "expected_version": lead.metadata_version,
                        "patch": {"archived": True},
                    }
                ),
            )
        settled = Event()
        original = app._root_runs._on_settled
        if original is not None:

            async def observe(project_id, operation):
                try:
                    await original(project_id, operation)
                finally:
                    settled.set()

            app._root_runs._on_settled = observe
        with fail_after(15):
            receipt = await app._root_runs.submit_prompt(thread_id=worker.thread_id, prompt="Work")
            assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
            if original is not None:
                await settled.wait()
        if case == "owned_worker":
            with fail_after(15):
                operation = await app._root_runs.active(lead.thread_id)
                if operation is not None:
                    await app.wait_root_operation(operation.receipt.receipt_id)
            assert calls == [worker.thread_id, lead.thread_id]
        else:
            assert calls == [worker.thread_id]


async def test_explicit_report_and_terminal_notice_are_distinct_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = coordinator_configuration(tmp_path)
    lead_id = ""
    seen = []
    notified = Event()

    async def resolve(self, context, model_id):
        async def model(messages, info):
            if context.deps.thread_id == lead_id:
                seen.append(str(messages))
                if "Host notification:" in str(messages):
                    notified.set()
            yield "Saved result checked."

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui", instrumentation=None) as app:
        lead_id = (await create_coordinator(app, "project-main")).thread_id
        worker = await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-main"), coordinator_thread_id=lead_id
        )
        with fail_after(15):
            report = await controller(app).send_thread_message(
                source_thread_id=worker.thread_id, thread_id=lead_id, message="Tests passed; here is my report."
            )
            await app.wait_root_operation(report["receipt"]["receipt_id"])
            receipt = await app._root_runs.submit_prompt(thread_id=worker.thread_id, prompt="Finish work")
            await app.wait_root_operation(receipt.receipt_id)
            await notified.wait()
        assert len(seen) == 2
        assert "Tests passed; here is my report." in seen[0]
        assert "Host notification:" not in seen[0]
        assert "Host notification:" in seen[1]


async def test_restarted_worker_keeps_owner_and_rejects_configuration_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = coordinator_configuration(tmp_path)
    # Exercise successful recovery, not the shared test helper's one-second drain budget.
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    started, release, notified = Event(), Event(), Event()
    worker_id = ""
    calls = []
    notified_threads = []

    async def resolve(self, context, model_id):
        async def model(messages, info):
            calls.append(context.deps.thread_id)
            if context.deps.thread_id != worker_id:
                notified_threads.append(context.deps.thread_id)
                notified.set()
                yield "Checked the saved outcome."
            elif calls.count(worker_id) == 1:
                started.set()
                await release.wait()
                yield {
                    0: DeltaToolCall(
                        name="store",
                        tool_call_id="save-before-restart",
                        json_args='{"key":"result","value":"Saved work"}',
                    )
                }
            else:
                yield "Finished after restart."

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with (
        create_task_group() as group,
        open_harness_ui_app(settings, configuration_path=root, host_mode="webui", instrumentation=None) as app,
    ):
        lead = await create_coordinator(app, "project-main")
        other = await create_coordinator(app, "project-other")
        worker = await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-main"), coordinator_thread_id=lead.thread_id
        )
        worker_id = worker.thread_id
        await app._root_runs.submit_prompt(thread_id=worker_id, prompt="Work across restart")
        with fail_after(15):
            await started.wait()
        with pytest.raises(StoreConflictError, match="cannot move"):
            await app.update_thread_configuration(
                thread_id=worker_id,
                mutation=ThreadConfigurationMutation.model_validate(
                    {"expected_version": worker.configuration.version, "patch": {"project_id": "project-other"}}
                ),
            )
        group.start_soon(release_during_shutdown, app, release)
    assert not notified_threads
    async with open_local_store(settings.storage) as store:
        batch = await store.restarts.get()
        assert batch is not None and batch.state == "ready", batch
        assert [item.thread_id for item in batch.items] == [worker_id]
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui", instrumentation=None) as app:
        with fail_after(15):
            await notified.wait()
            target = await app._root_runs.active(lead.thread_id) or await app._root_runs.latest(lead.thread_id)
            assert target is not None
            assert (await app.wait_root_operation(target.receipt.receipt_id)).status is RootOperationStatus.completed
        assert notified_threads == [lead.thread_id]
        assert await app._root_runs.latest(other.thread_id) is None
        assert calls.count(worker_id) == 2


@pytest.mark.parametrize("delivery", ["accepted", "error"])
async def test_direct_human_work_notifies_owner_without_changing_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delivery: str
) -> None:
    from a13n_harness_ui.thread_capability import ThreadToolController

    from .test_app import _CompletedReconstructor

    root = coordinator_configuration(tmp_path)
    notices = []

    async def deliver(self, *, thread_id, prompt):
        notices.append((thread_id, str(prompt)))
        if delivery == "error":
            raise RuntimeError("Notification unavailable")
        return {"ok": delivery != "rejected"}

    monkeypatch.setattr(ThreadToolController, "_run_or_steer", deliver)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        owner = await create_coordinator(app)
        await app.set_auto_followup(owner.thread_id, delivery != "paused")
        worker = await app.create_thread(coordinator_thread_id=owner.thread_id)
        assert notices == []  # Empty creation does not wake a Coordinator.
        observed = Event()
        original = app._root_runs._on_human_admitted
        assert original is not None

        async def observe(admission, operation):
            try:
                await original(admission, operation)
            finally:
                observed.set()

        app._root_runs._on_human_admitted = observe
        with fail_after(15):
            receipt = await app.submit_thread(thread_id=worker.thread_id, prompt="User's direct task")
            await observed.wait()
            assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        started = [notice for notice in notices if "directly started work" in notice[1]]
        assert len(started) == (0 if delivery == "paused" else 1)
        if started:
            assert started[0][0] == owner.thread_id
            assert worker.thread_id in started[0][1] and receipt.receipt_id in started[0][1]
            assert "do not recreate or redispatch" in started[0][1]
        transcript = await app.get_thread_transcript(thread_id=worker.thread_id)
        parts = [part for entry in transcript.entries for part in entry.parts if part.text == "User's direct task"]
        assert parts and all("thread_message" not in part.metadata.model_dump().get("harness_ui", {}) for part in parts)
        observed = Event()
        receipt = await app.submit_thread(thread_id=worker.thread_id, prompt="A follow-up")
        with fail_after(15):
            await observed.wait()
            await app.wait_root_operation(receipt.receipt_id)
        assert len([notice for notice in notices if "directly started work" in notice[1]]) == len(started)


@pytest.mark.parametrize("cancelled", [False, True])
async def test_busy_non_steering_coordinator_receives_one_consolidated_followup_after_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    from a13n_harness_ui.surfaces import RootControlResult

    root = coordinator_configuration(tmp_path)
    lead_id = ""
    started, release, followup_done = Event(), Event(), Event()
    seen_notices: list[str] = []

    async def resolve(self, context, model_id):
        async def model(messages, info):
            if context.deps.thread_id == lead_id:
                text = str(messages)
                if "Host notification:" in text:
                    seen_notices.append(text)
                    followup_done.set()
                    yield "Reconciled workers."
                else:
                    started.set()
                    await release.wait()
                    yield "Finished initial turn."
            else:
                yield "Worker done."

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui", instrumentation=None) as app:
        lead = await create_coordinator(app, "project-main")
        lead_id = lead.thread_id
        first_worker = await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-main"), coordinator_thread_id=lead_id
        )
        second_worker = await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-main"), coordinator_thread_id=lead_id
        )

        async def reject_steer(*, receipt_id: str, message):
            return RootControlResult(receipt_id=receipt_id, accepted=False)

        monkeypatch.setattr(app._root_runs, "steer", reject_steer)
        lead_settled = Event()
        original_settled = app._root_runs._on_settled
        assert original_settled is not None

        async def observe_settled(project_id, operation):
            await original_settled(project_id, operation)
            if operation.receipt.thread_id == lead_id and operation.receipt.receipt_id == initial.receipt_id:
                lead_settled.set()

        app._root_runs._on_settled = observe_settled
        with fail_after(15):
            initial = await app.submit_thread(thread_id=lead_id, prompt="Coordinate non-steering")
            await started.wait()
            w1 = await app._root_runs.submit_prompt(thread_id=first_worker.thread_id, prompt="Task 1")
            w2 = await app._root_runs.submit_prompt(thread_id=second_worker.thread_id, prompt="Task 2")
            assert (await app.wait_root_operation(w1.receipt_id)).status is RootOperationStatus.completed
            assert (await app.wait_root_operation(w2.receipt_id)).status is RootOperationStatus.completed
            while (
                "Multiple managed worker operations updated"
                not in app._thread_tools._deferred_coordinator_notices.get(lead_id, ("", ""))[1]
            ):
                from anyio import sleep

                await sleep(0.01)
            assert app._thread_tools._deferred_coordinator_notices[lead_id][0] == initial.receipt_id
            assert len(app._thread_tools._deferred_coordinator_notices) == 1
            if cancelled:
                await app.cancel_root_operation(initial.receipt_id)
                assert (await app.wait_root_operation(initial.receipt_id)).status is RootOperationStatus.cancelled
                await lead_settled.wait()
                assert lead_id not in app._thread_tools._deferred_coordinator_notices
                assert await app._root_runs.active(lead_id) is None
                assert seen_notices == []
            else:
                release.set()
                assert (await app.wait_root_operation(initial.receipt_id)).status is RootOperationStatus.completed
                await followup_done.wait()
                followup = await app._root_runs.active(lead_id) or await app._root_runs.latest(lead_id)
                assert followup is not None
                await app.wait_root_operation(followup.receipt.receipt_id)
                assert len(seen_notices) == 1
                assert "Multiple managed worker operations updated" in seen_notices[0]


async def test_deferred_coordinator_notice_binds_exact_receipt_across_interleaved_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.surfaces import RootOperationView, RootRunReceipt

    from .test_app import _CompletedReconstructor

    root = coordinator_configuration(tmp_path)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui", instrumentation=None) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        lead = await create_coordinator(app, "project-main")
        worker = await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-main"), coordinator_thread_id=lead.thread_id
        )

        # 1. Old cancelled receipt settling after a successor receipt took over must not clear the successor's notice.
        app._thread_tools._deferred_coordinator_notices[lead.thread_id] = ("receipt-new", "Successor notice")
        stale_cancelled = RootOperationView(
            receipt=RootRunReceipt(
                receipt_id="receipt-old",
                thread_id=lead.thread_id,
                submitted_at=lead.created_at,
            ),
            status=RootOperationStatus.cancelled,
        )
        await app._thread_tools._flush_coordinator_followup(stale_cancelled)
        assert app._thread_tools._deferred_coordinator_notices.get(lead.thread_id) == (
            "receipt-new",
            "Successor notice",
        )
        app._thread_tools._deferred_coordinator_notices.clear()

        # 2. A rejected steer returning after its target receipt already cancelled must not resurrect a notice.
        cancelled_run = await app.submit_thread(thread_id=lead.thread_id, prompt="Quick run")
        await app.wait_root_operation(cancelled_run.receipt_id)
        app._root_runs._operations[cancelled_run.receipt_id].status = RootOperationStatus.cancelled

        async def late_rejected_steer(*, thread_id: str, prompt, retain_run: bool = False):
            return {"ok": False, "mode": "steer", "receipt_id": cancelled_run.receipt_id, "accepted": False}

        monkeypatch.setattr(app._thread_tools, "_run_or_steer", late_rejected_steer)
        worker_done = RootOperationView(
            receipt=RootRunReceipt(
                receipt_id="receipt-worker",
                thread_id=worker.thread_id,
                submitted_at=worker.created_at,
            ),
            status=RootOperationStatus.completed,
        )
        await app._thread_tools.notify_coordinator("project-main", worker_done)
        assert lead.thread_id not in app._thread_tools._deferred_coordinator_notices
