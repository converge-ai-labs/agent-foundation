from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import StoreConflictError, ThreadError
from a13n_harness_ui.storage import ThreadConfigurationMutation
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus, ThreadMetadataMutation
from anyio import create_task_group

from .test_app import _CompletedReconstructor, _settings
from .test_thread_collaboration import configuration, controller

pytestmark = pytest.mark.anyio


def coordinator_configuration(tmp_path: Path) -> Path:
    root = configuration(tmp_path)
    document = yaml.safe_load(root.read_text())
    document["webui"] = {"sidekick": {"agent": "agent-worker", "model": "model-secondary"}}
    root.write_text(yaml.safe_dump(document))
    return root


async def create_coordinator(app, project_id="project-main"):
    thread = await app.create_thread(defaults=NewThreadDefaults(project_id=project_id))
    return await app.promote_coordinator(thread.thread_id)


async def test_promotion_preserves_conversation_and_allows_multiple_coordinators(tmp_path: Path) -> None:
    root = coordinator_configuration(tmp_path)
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        ordinary = await app.create_thread(title="Authentication redesign")
        receipt = await app.submit_thread(thread_id=ordinary.thread_id, prompt="Plan work")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        original = await app._threads.get(ordinary.thread_id)
        results = []

        async def promote():
            results.append(await app.promote_coordinator(ordinary.thread_id))

        async with create_task_group() as group:
            for _ in range(8):
                group.start_soon(promote)
        lead = results[0]
        assert {item.thread_id for item in results} == {ordinary.thread_id}
        assert lead.role == "coordinator" and lead.auto_followup is True
        assert lead.title == ordinary.title
        assert lead.configuration == ordinary.configuration
        assert (await app._threads.get(lead.thread_id)).continuation == original.continuation
        assert (await app._threads.get(lead.thread_id)).initial_state == original.initial_state
        assert await app._root_runs.active_count() == 0
        other = await create_coordinator(app)
        assert other.thread_id != lead.thread_id
        assert other.configuration.project_id == lead.configuration.project_id
        await app.set_auto_followup(lead.thread_id, False)
        assert (await app.promote_coordinator(lead.thread_id)).auto_followup is False
        assert (await app.list_threads()).total == 2
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert (await app.get_thread(lead.thread_id)).thread.role == "coordinator"
        assert (await app.get_thread(lead.thread_id)).thread.auto_followup is False
        assert (await app.get_thread(other.thread_id)).thread.auto_followup is True


async def test_promotion_never_adopts_previous_sidekicks_and_worker_is_terminal_role(
    tmp_path: Path, monkeypatch
) -> None:
    root = coordinator_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        ordinary = await app.create_thread()

        async def reject(**kwargs):
            raise ThreadError("Admission rejected", code="test_rejected")

        monkeypatch.setattr(app._root_runs, "submit_prompt", reject)
        sidekick = await controller(app).create_thread(
            source_thread_id=ordinary.thread_id, prompt="Before", title=None, agent_id=None
        )
        await app.promote_coordinator(ordinary.thread_id)
        worker = await controller(app).create_thread(
            source_thread_id=ordinary.thread_id, prompt="After", title=None, agent_id=None
        )
        assert (await app.get_thread(sidekick["thread_id"])).thread.role == "ordinary"
        assert (await app.get_thread(worker["thread_id"])).thread.coordinator_thread_id == ordinary.thread_id
        with pytest.raises(StoreConflictError, match="independent"):
            await app.promote_coordinator(worker["thread_id"])
        assert (await app.get_thread(worker["thread_id"])).thread.role == "worker"
        star = ThreadMetadataMutation.model_validate({"expected_version": 1, "patch": {"starred": True}})
        with pytest.raises(ThreadError, match="Star the Coordinator"):
            await app.update_thread_metadata(thread_id=worker["thread_id"], mutation=star)
        assert (await app.update_thread_metadata(thread_id=ordinary.thread_id, mutation=star)).starred


async def test_archive_and_project_constraints_do_not_change_roles(tmp_path: Path) -> None:
    root = coordinator_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        lead = await create_coordinator(app)
        await app.update_thread_metadata(
            thread_id=lead.thread_id,
            mutation=ThreadMetadataMutation.model_validate(
                {"expected_version": lead.metadata_version, "patch": {"archived": True}}
            ),
        )
        assert (await app.get_thread(lead.thread_id)).thread.role == "coordinator"
        with pytest.raises(StoreConflictError, match="cannot move"):
            await app.update_thread_configuration(
                thread_id=lead.thread_id,
                mutation=ThreadConfigurationMutation.model_validate(
                    {"expected_version": lead.configuration.version, "patch": {"project_id": "project-other"}}
                ),
            )
        ordinary = await app.create_thread()
        await app.update_thread_metadata(
            thread_id=ordinary.thread_id,
            mutation=ThreadMetadataMutation.model_validate(
                {"expected_version": ordinary.metadata_version, "patch": {"archived": True}}
            ),
        )
        with pytest.raises(StoreConflictError, match="Restore"):
            await app.promote_coordinator(ordinary.thread_id)
        projectless = await app.create_thread(defaults=NewThreadDefaults(project_id=None))
        with pytest.raises(ThreadError, match="unavailable"):
            await app.promote_coordinator(projectless.thread_id)


async def test_sidekick_disabled_does_not_gate_promotion_or_role_capture(tmp_path: Path) -> None:
    from a13n_harness_ui.thread_capability import ThreadCollaborationCapability

    root = coordinator_configuration(tmp_path)
    document = yaml.safe_load(root.read_text())
    document["webui"]["sidekick"] = None
    root.write_text(yaml.safe_dump(document))
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        lead = await create_coordinator(app)
        ordinary = await app.create_thread()
        for thread, expected in ((lead, "coordinator"), (lead, "coordinator"), (ordinary, "ordinary")):
            captured = await app._root_runs._executor.capture(thread_id=thread.thread_id, prompt="Work")
            assert captured.published.value.role == expected
            capability = ThreadCollaborationCapability(
                controller=controller(app), source_thread_id=thread.thread_id, composition=captured.published.value
            )
            assert ("You are a Coordinator" in capability.get_instructions()) == (expected == "coordinator")


async def test_http_promotion_and_followup_are_authenticated_thread_operations(tmp_path: Path) -> None:
    from a13n_harness_ui.webui import create_webui
    from httpx import ASGITransport, AsyncClient

    root = coordinator_configuration(tmp_path)
    server = create_webui(
        lambda: open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui"),
        api_key="coordinator-test-key",
    )
    async with (
        server.router.lifespan_context(server),
        AsyncClient(transport=ASGITransport(app=server), base_url="http://localhost") as client,
    ):
        assert (await client.post("/api/threads/unknown/coordinator")).status_code == 401
        client.headers["Authorization"] = "Bearer coordinator-test-key"
        created = await client.post("/api/threads", json={})
        assert created.status_code == 200, created.text
        thread_id = created.json()["thread_id"]
        path = f"/api/threads/{thread_id}/coordinator"
        first = await client.post(path)
        assert first.status_code == 200, first.text
        assert first.json()["role"] == "coordinator"
        assert first.json()["root_activity"]["state"] == "inactive"
        changed = await client.patch(path, json={"auto_followup": False})
        assert changed.status_code == 200 and changed.json()["auto_followup"] is False
        assert (await client.post(path)).json()["auto_followup"] is False
        assert (await client.patch(path, json={})).status_code == 422
        assert "lead_thread_id" not in (await client.get("/api/projects")).json()[0]
        assert (await client.post("/api/projects/project-main/lead")).status_code in (404, 405)


async def test_independent_database_writers_promote_same_thread_atomically(tmp_path: Path) -> None:
    from a13n_harness_ui.storage.database import open_database
    from a13n_harness_ui.storage.repositories import ThreadRepository

    root = coordinator_configuration(tmp_path)
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        original = await app.create_thread()
        async with open_database(app._store.layout.database, settings.storage) as other:
            repository = ThreadRepository(other.sessions)
            async with create_task_group() as group:
                group.start_soon(app._store.threads.promote_coordinator, original.thread_id)
                group.start_soon(repository.promote_coordinator, original.thread_id)
            assert len(await repository.coordinators()) == 1
            assert (await app.list_threads()).total == 1


async def test_promotion_publishes_thread_invalidation(tmp_path: Path) -> None:
    from anyio import fail_after

    root = coordinator_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        async with app.summary_events() as events:
            await app.promote_coordinator(thread.thread_id)
            with fail_after(2):
                event = await events.receive()
            assert event.kind == "thread" and event.thread_id == thread.thread_id


async def test_active_and_deferred_threads_cannot_promote(tmp_path: Path) -> None:
    from a13n_harness_ui.errors import RunCoordinationError
    from anyio import Event, fail_after

    from .test_app import _DeferredReconstructor, _SlowReconstructor

    root = coordinator_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        started = Event()
        app._root_runs._executor._agents = _SlowReconstructor(started)
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Wait")
        with fail_after(10):
            await started.wait()
        with pytest.raises(RunCoordinationError, match="Wait for"):
            await app.promote_coordinator(thread.thread_id)
        await app.cancel_root_operation(receipt.receipt_id)
        await app.wait_root_operation(receipt.receipt_id)
        app._root_runs._executor._agents = _DeferredReconstructor()
        deferred = await app.create_thread()
        receipt = await app.submit_thread(thread_id=deferred.thread_id, prompt="Ask")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.suspended
        with pytest.raises(ThreadError, match="pending decisions"):
            await app.promote_coordinator(deferred.thread_id)
        assert (await app.get_thread(deferred.thread_id)).thread.role == "ordinary"


async def test_historical_captures_remain_readable_without_role_aliases(tmp_path: Path) -> None:
    from a13n_harness_ui.composition import ResolvedRunComposition
    from a13n_harness_ui.storage.objects import ObjectKind

    root = coordinator_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        lead = await create_coordinator(app)
        capture = (await app._root_runs._executor.capture(thread_id=lead.thread_id, prompt="Work")).published
        historical = capture.value.model_dump(mode="json", exclude={"role", "coordinator_thread_id"})
        historical.update(is_project_lead=True, lead_thread_id=None)
        saved = await app._store.objects.publish(
            object_kind=ObjectKind.run_composition, object_schema_version="1", payload=historical
        )
        read = await app._store.objects.read_model(saved.ref, ResolvedRunComposition)
        assert read.role is None  # Not recorded in this historical schema, never fabricated as ordinary.
        current = (await app._root_runs._executor.capture(thread_id=lead.thread_id, prompt="Continue")).published
        assert current.value.role == "coordinator"
        assert "is_project_lead" not in current.value.model_dump()


@pytest.mark.parametrize("worker", [False, True])
async def test_restart_refreshes_historical_role_without_changing_captured_dependencies(
    tmp_path: Path, worker: bool
) -> None:
    from a13n_harness_ui.composition import ResolvedRunComposition
    from a13n_harness_ui.restart_models import RestartItem
    from a13n_harness_ui.storage.objects import ObjectKind

    root = coordinator_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        owner = await create_coordinator(app)
        thread = (
            await app.create_thread(
                defaults=NewThreadDefaults(project_id="project-main"), coordinator_thread_id=owner.thread_id
            )
            if worker
            else owner
        )
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Saved work")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        saved_thread = await app._threads.get(thread.thread_id)
        assert saved_thread.continuation is not None
        capture = (await app._root_runs._executor.capture(thread_id=thread.thread_id, prompt="Capture")).published
        historical = capture.value.model_dump(mode="json", exclude={"role", "coordinator_thread_id"})
        envelope = await app._store.objects.publish(
            object_kind=ObjectKind.run_composition, object_schema_version="1", payload=historical
        )
        restart = RestartItem(
            thread_id=thread.thread_id,
            root_thread_id=thread.thread_id,
            run_id="run-before-upgrade",
            checkpoint=saved_thread.continuation,
            composition=envelope.ref,
        )
        restored = await app._root_runs._executor.capture(thread_id=thread.thread_id, restart=restart)
        assert restored.published.value.role == ("worker" if worker else "coordinator")
        assert restored.published.value.coordinator_thread_id == (owner.thread_id if worker else None)
        assert restored.published.value.model_dump(mode="json", exclude={"role", "coordinator_thread_id"}) == historical
        assert (await app._store.objects.read_model(envelope.ref, ResolvedRunComposition)).role is None
        assert (await app._threads.get(thread.thread_id)).continuation == saved_thread.continuation
