from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import StoreConflictError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.storage import ThreadConfigurationMutation, open_local_store
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus, ThreadMetadataMutation
from anyio import Event, create_task_group, fail_after
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings
from .test_coordinators import coordinator_configuration, create_coordinator
from .test_restart_recovery import release_during_shutdown
from .test_thread_collaboration import controller
from .test_thread_inbox_dispatch import drained

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
async def test_terminal_notice_runs_or_steers_lead_and_never_notifies_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, active: bool
) -> None:
    root = coordinator_configuration(tmp_path)
    lead_id = ""
    started, release, notified = Event(), Event(), Event()
    notices = []

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
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui", instrumentation=None) as app:
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
            # Queued steering may be selected by this Run or its eligible successor.
            if initial is not None:
                await app.wait_root_operation(initial.receipt_id)
            assert (await app.wait_root_operation(target.receipt.receipt_id)).status is RootOperationStatus.completed
            await lead_settled.wait()
            assert await app._root_runs.active(lead_id) is None
        assert len(notices) == 1
        assert receipt.receipt_id in notices[0] and worker.thread_id in notices[0]
        assert "Status: completed" in notices[0]
        history = await app.get_thread_transcript(thread_id=lead_id)
        notice_parts = [
            part for entry in history.entries for part in entry.parts if "Host notification:" in (part.text or "")
        ]
        assert len(notice_parts) == 1 and notice_parts[0].metadata.display is False


@pytest.mark.parametrize(
    "case", ["mode_off", "sidekick_off", "other_project", "no_project", "archived", "terminal", "independent"]
)
async def test_terminal_notice_respects_project_and_current_host_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    root = coordinator_configuration(tmp_path)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        lead = await create_coordinator(app, "project-main")
        if case == "mode_off":
            await app.set_auto_followup(lead.thread_id, False)
    if case == "sidekick_off":
        document = yaml.safe_load(root.read_text())
        document["webui"]["sidekick"] = None
        root.write_text(yaml.safe_dump(document))
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
        if case == "sidekick_off":
            with fail_after(15):
                await drained(app, lead.thread_id)
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
            report = await controller(app).send(
                SimpleNamespace(deps=SimpleNamespace(thread_id=worker.thread_id)),
                to=lead_id,
                message="Tests passed; here is my report.",
            )
            assert report["accepted"]
            await drained(app, lead_id)
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


@pytest.mark.parametrize("enabled", [False, True])
async def test_direct_human_work_commits_notice_once_with_admission(tmp_path, enabled):
    from .test_app import _CompletedReconstructor

    root = coordinator_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        app._inbox.stop()
        owner = await create_coordinator(app)
        await app.set_auto_followup(owner.thread_id, enabled)
        worker = await app.create_thread(coordinator_thread_id=owner.thread_id)
        assert await app._store.thread_inbox.pending(owner.thread_id) == ()
        receipt = await app.submit_thread(thread_id=worker.thread_id, prompt="User's direct task")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        pending = await app._store.thread_inbox.pending(owner.thread_id)
        started = [row.envelope.text for row in pending if "directly started work" in row.envelope.text]
        assert len(started) == int(enabled)
        if started:
            assert worker.thread_id in started[0]
            assert "do not recreate or redispatch" in started[0]
        transcript = await app.get_thread_transcript(thread_id=worker.thread_id)
        parts = [part for entry in transcript.entries for part in entry.parts if part.text == "User's direct task"]
        assert parts and all("thread_message" not in part.metadata.model_dump().get("harness_ui", {}) for part in parts)
        receipt = await app.submit_thread(thread_id=worker.thread_id, prompt="A follow-up")
        await app.wait_root_operation(receipt.receipt_id)
        pending = await app._store.thread_inbox.pending(owner.thread_id)
        assert len([row for row in pending if "directly started work" in row.envelope.text]) == len(started)
