from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import ResolvedRunComposition
from a13n_harness_ui.errors import StoreConflictError, ThreadError
from a13n_harness_ui.storage import ThreadConfigurationMutation
from a13n_harness_ui.surfaces import RootOperationStatus, ThreadMetadataMutation
from anyio import create_task_group

from .test_app import _CompletedReconstructor, _settings
from .test_thread_collaboration import configuration

pytestmark = pytest.mark.anyio


def lead_configuration(tmp_path: Path) -> Path:
    root = configuration(tmp_path)
    document = yaml.safe_load(root.read_text())
    document["webui"] = {"sidekick": {"agent": "agent-worker", "model": "model-secondary"}}
    root.write_text(yaml.safe_dump(document))
    return root


async def test_project_lead_atomic_identity_defaults_and_restart(tmp_path: Path) -> None:
    root = lead_configuration(tmp_path)
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert all(project.lead_thread_id is None for project in await app.projects())
        assert (await app.list_threads()).total == 0
        results = []

        async def ensure() -> None:
            results.append(await app.ensure_project_lead("project-main"))

        async with create_task_group() as group:
            for _ in range(8):
                group.start_soon(ensure)
        lead = results[0]
        assert {item.thread_id for item in results} == {lead.thread_id}
        assert (await app.list_threads()).total == 1
        assert lead.configuration.agent_source.id == "agent-assistant"
        assert lead.configuration.default_model_id is None
        assert lead.continuation_state == "initial"
        assert await app._root_runs.active(lead.thread_id) is None
        assert (await app.projects())[0].lead_thread_id == lead.thread_id
        other = await app.ensure_project_lead("project-other")
        assert other.thread_id != lead.thread_id
        with pytest.raises(ThreadError, match="unavailable"):
            await app.ensure_project_lead("missing")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert (await app.ensure_project_lead("project-main")).thread_id == lead.thread_id
        assert (await app.list_threads()).total == 2


async def test_project_lead_archive_retains_identity_and_project_is_locked(tmp_path: Path) -> None:
    root = lead_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        lead = await app.ensure_project_lead("project-main")
        lead = await app.update_thread_metadata(
            thread_id=lead.thread_id,
            mutation=ThreadMetadataMutation.model_validate(
                {"expected_version": lead.metadata_version, "patch": {"title": "Delivery", "archived": True}}
            ),
        )
        ensured = await app.ensure_project_lead("project-main")
        assert ensured.thread_id == lead.thread_id and ensured.archived and ensured.title == "Delivery"
        with pytest.raises(StoreConflictError, match="cannot move"):
            await app.update_thread_configuration(
                thread_id=lead.thread_id,
                mutation=ThreadConfigurationMutation.model_validate(
                    {"expected_version": lead.configuration.version, "patch": {"project_id": "project-other"}}
                ),
            )
        assert (await app.get_thread(lead.thread_id)).thread.configuration.project_id == "project-main"


async def test_disabled_sidekick_keeps_existing_lead_accessible(tmp_path: Path) -> None:
    root = lead_configuration(tmp_path)
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        lead = await app.ensure_project_lead("project-main")
    document = yaml.safe_load(root.read_text())
    document["webui"]["sidekick"] = None
    root.write_text(yaml.safe_dump(document))
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert (await app.ensure_project_lead("project-main")).thread_id == lead.thread_id
        with pytest.raises(ThreadError, match="Enable Sidekick"):
            await app.ensure_project_lead("project-other")
        assert (await app.list_threads()).total == 1


async def test_role_is_captured_each_run_and_old_compositions_default_false(tmp_path: Path) -> None:
    root = lead_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        lead = await app.set_project_lead_enabled("project-main", True)
        ordinary = await app.create_thread()
        for thread, expected in ((lead, True), (lead, True), (ordinary, False)):
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Work")
            assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
            ref = await app._root_runs.composition_reference(receipt.receipt_id)
            assert ref is not None
            composition = await app._store.objects.read_model(ref, ResolvedRunComposition)
            assert composition.is_project_lead is expected
            if expected:
                from a13n_harness_ui.thread_capability import ThreadCollaborationCapability

                from .test_thread_collaboration import controller

                capability = ThreadCollaborationCapability(
                    controller=controller(app), source_thread_id=thread.thread_id, composition=composition
                )
                assert "You are this Project's Lead" in capability.get_instructions()
                capability.composition = composition.model_copy(update={"webui_sidekick": None})
                assert "You are this Project's Lead" not in capability.get_instructions()
                assert "compact coordination note" not in capability.get_instructions()
                assert capability.composition.is_project_lead
            historical = composition.model_dump(mode="json", exclude={"is_project_lead"})
            assert not ResolvedRunComposition.model_validate(historical).is_project_lead


async def test_http_ensure_is_authenticated_and_does_not_run(tmp_path: Path) -> None:
    from a13n_harness_ui.webui import create_webui
    from httpx import ASGITransport, AsyncClient

    root = lead_configuration(tmp_path)
    server = create_webui(
        lambda: open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui"),
        api_key="lead-test-key",
    )
    async with (
        server.router.lifespan_context(server),
        AsyncClient(transport=ASGITransport(app=server), base_url="http://localhost") as client,
    ):
        assert (await client.post("/api/projects/project-main/lead")).status_code == 401
        assert (await client.patch("/api/projects/project-main/lead", json={"enabled": True})).status_code == 401
        client.headers["Authorization"] = "Bearer lead-test-key"
        first = await client.post("/api/projects/project-main/lead")
        assert first.status_code == 200, first.text
        second = await client.post("/api/projects/project-main/lead")
        assert second.json()["thread_id"] == first.json()["thread_id"]
        assert first.json()["continuation_state"] == "initial"
        assert first.json()["root_activity"]["state"] == "inactive"
        assert (await client.get("/api/projects")).json()[0]["lead_thread_id"] == first.json()["thread_id"]
        assert (await client.get("/api/selectors")).json()["sidekick_enabled"] is True
        for enabled in (True, False, True):
            changed = await client.patch("/api/projects/project-main/lead", json={"enabled": enabled})
            assert changed.status_code == 200
            assert changed.json()["thread_id"] == first.json()["thread_id"]
            assert changed.json()["root_activity"]["state"] == "inactive"
            assert (await client.get("/api/projects")).json()[0]["lead_enabled"] is enabled
        assert (await client.patch("/api/projects/project-main/lead", json={})).status_code == 422


async def test_independent_database_writers_ensure_one_lead(tmp_path: Path) -> None:
    from a13n_harness_ui.storage.database import open_database
    from a13n_harness_ui.storage.repositories import ThreadRepository

    root = lead_configuration(tmp_path)
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        # Independent engines have independent process-local write locks. The
        # database transaction, not a Python lock, must arbitrate the winner.
        original = await app._threads.create()
        path = app._store.layout.database
        async with open_database(path, settings.storage) as other:
            repository = ThreadRepository(other.sessions)
            results = []

            async def create(repo: ThreadRepository, suffix: str) -> None:
                results.append(
                    await repo.create(
                        thread_id=f"thread-lead-{suffix}",
                        configuration=original.configuration,
                        initial_state=original.initial_state,
                        project_lead=True,
                    )
                )

            async with create_task_group() as group:
                group.start_soon(create, app._store.threads, "first")
                group.start_soon(create, repository, "second")
            assert results[0].thread_id == results[1].thread_id
            assert len(await repository.list()) == 2


async def test_ensure_invalidates_projects_for_other_clients(tmp_path: Path) -> None:
    from anyio import fail_after

    root = lead_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        async with app.summary_events() as events:
            lead = await app.ensure_project_lead("project-main")
            with fail_after(2):
                project_event = await events.receive()
                thread_event = await events.receive()
            assert project_event.kind == "project"
            assert thread_event.kind == "thread" and thread_event.thread_id == lead.thread_id
            assert (await app.projects())[0].lead_thread_id == lead.thread_id
