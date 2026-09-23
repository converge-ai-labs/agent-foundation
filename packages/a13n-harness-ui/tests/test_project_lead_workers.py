from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import StoreConflictError, StoreIntegrityError, ThreadError
from a13n_harness_ui.storage import ThreadConfigurationMutation
from a13n_harness_ui.surfaces import NewThreadDefaults
from a13n_harness_ui.thread_capability import ThreadCollaborationCapability

from .test_app import _CompletedReconstructor, _settings
from .test_project_lead import lead_configuration
from .test_thread_collaboration import controller

pytestmark = pytest.mark.anyio


async def test_creation_owns_only_new_lead_workers_and_retains_failed_admission(tmp_path: Path, monkeypatch) -> None:
    root = lead_configuration(tmp_path)
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        lead = await app.ensure_project_lead("project-main")
        ordinary = await app.create_thread(defaults=NewThreadDefaults(project_id="project-main"))
        composition = (await app._root_runs._executor.capture(thread_id=lead.thread_id, prompt="Plan")).published.value
        observed = []

        async def reject(**kwargs):
            created = (await app.get_thread(kwargs["thread_id"])).thread
            observed.append(created)
            raise ThreadError("Admission rejected", code="test_rejected")

        monkeypatch.setattr(app._root_runs, "submit_prompt", reject)
        managed = await controller(app).create_thread(
            source_thread_id=lead.thread_id, prompt="Work", title=None, agent_id=None, source_composition=composition
        )
        independent = await controller(app).create_thread(
            source_thread_id=ordinary.thread_id, prompt="Sidekick", title=None, agent_id=None
        )
        assert not managed["ok"] and not independent["ok"]
        worker = observed[0]
        assert worker.thread_id == managed["thread_id"]
        assert worker.lead_thread_id == lead.thread_id and worker.parent_thread_id is None
        assert worker.configuration.agent_source.id == "agent-worker"
        assert worker.configuration.default_model_id == "model-secondary"
        assert observed[1].lead_thread_id is None
        assert (await app.get_thread(ordinary.thread_id)).thread.lead_thread_id is None
        assert (await app.list_threads()).total == 4
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert (await app.get_thread(worker.thread_id)).thread.lead_thread_id == lead.thread_id
        assert (await app.get_thread(independent["thread_id"])).thread.lead_thread_id is None


async def test_lead_shared_tools_enforce_scope_even_with_automatic_mode_disabled(tmp_path: Path) -> None:
    root = lead_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        lead = await app.ensure_project_lead("project-main")
        other_lead = await app.ensure_project_lead("project-other")
        ordinary = await app.create_thread(defaults=NewThreadDefaults(project_id="project-main"))
        worker = await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-main"), lead_thread_id=lead.thread_id
        )
        other_worker = await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-other"), lead_thread_id=other_lead.thread_id
        )
        capability = ThreadCollaborationCapability(controller=controller(app), source_thread_id=lead.thread_id)
        ctx = SimpleNamespace(deps=SimpleNamespace(thread_id=lead.thread_id))
        listed = await capability.list_threads(ctx, limit=20)
        assert [item["thread_id"] for item in listed["threads"]] == [worker.thread_id]
        assert (await capability.get_thread(ctx, history_limit=20))["thread"]["thread"]["thread_id"] == lead.thread_id
        assert (await capability.get_thread(ctx, thread_id=worker.thread_id, history_limit=20))["ok"]
        for target in (ordinary, other_lead, other_worker):
            for action in (
                capability.get_thread(ctx, thread_id=target.thread_id, history_limit=20),
                capability.run_thread(ctx, thread_id=target.thread_id, prompt="Do not run"),
                capability.steer_thread(ctx, thread_id=target.thread_id, message="Do not steer"),
                capability.send_thread_message(ctx, thread_id=target.thread_id, message="Do not send"),
            ):
                result = await action
                assert not result["ok"] and result["error"]["code"] == "project_lead_thread_scoped"
            assert await app._root_runs.latest(target.thread_id) is None
        # The human App boundary is deliberately not restricted by the Coordinator's model-tool scope.
        receipt = await app.submit_thread(thread_id=worker.thread_id, prompt="Direct user input")
        await app.wait_root_operation(receipt.receipt_id)
        result = await capability.send_thread_message(ctx, thread_id=worker.thread_id, message="Continue")
        assert result["ok"]
        await app.wait_root_operation(result["receipt"]["receipt_id"])
        # Ordinary Sidekick messaging remains available across independent roots.
        result = await controller(app).send_thread_message(
            source_thread_id=ordinary.thread_id, thread_id=other_worker.thread_id, message="Explicit request"
        )
        assert result["ok"]
        await app.wait_root_operation(result["receipt"]["receipt_id"])


async def test_worker_identity_is_captured_each_run_without_inheriting_lead_role(tmp_path: Path) -> None:
    root = lead_configuration(tmp_path)
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        lead = await app.ensure_project_lead("project-main")
        worker = await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-main"), lead_thread_id=lead.thread_id
        )
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        for _ in range(2):
            composition = (
                await app._root_runs._executor.capture(thread_id=worker.thread_id, prompt="Continue")
            ).published.value
            assert composition.lead_thread_id == lead.thread_id
            assert not composition.is_project_lead
            capability = ThreadCollaborationCapability(
                controller=controller(app), source_thread_id=worker.thread_id, composition=composition
            )
            instructions = capability.get_instructions()
            assert f"managed worker of Coordinator {lead.thread_id}" in instructions
            assert "Do not create another root" in instructions
            assert "Create a separate Thread only" not in instructions
        with pytest.raises(ThreadError, match="Ask your Coordinator"):
            await controller(app).create_thread(
                source_thread_id=worker.thread_id, prompt="Nested", title=None, agent_id=None
            )
        with pytest.raises(ThreadError, match="own Project"):
            await controller(app).create_thread(
                source_thread_id=lead.thread_id, prompt="Outside", title=None, agent_id=None, project_id="project-other"
            )
        with pytest.raises(StoreConflictError, match="cannot move"):
            await app.update_thread_configuration(
                thread_id=worker.thread_id,
                mutation=ThreadConfigurationMutation.model_validate(
                    {"expected_version": worker.configuration.version, "patch": {"project_id": None}}
                ),
            )
        before = (await app.list_threads()).total
        with pytest.raises(StoreIntegrityError, match="Coordinator's Project"):
            await app.create_thread(
                defaults=NewThreadDefaults(project_id="project-other"), lead_thread_id=lead.thread_id
            )
        assert (await app.list_threads()).total == before


@pytest.mark.parametrize("lead_is_newest", [False, True])
async def test_ownership_filters_before_pagination_and_binds_cursors(tmp_path: Path, lead_is_newest: bool) -> None:
    root = lead_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        lead = await app.ensure_project_lead("project-main")
        normal = [
            await app.create_thread(title=f"Normal {index}", defaults=NewThreadDefaults(project_id="project-main"))
            for index in range(5)
        ]
        workers = [
            await app.create_thread(
                title=f"Worker {index}",
                defaults=NewThreadDefaults(project_id="project-main"),
                lead_thread_id=lead.thread_id,
            )
            for index in range(7)
        ]
        if lead_is_newest:
            await app._store.threads.touch(lead.thread_id)
        page = await app.thread_activity(project_id="project-main", independent_only=True, limit=5, include_active=True)
        assert len(page.rows) == 5 and page.total == 5 and page.next_cursor is None
        assert {row.thread.thread_id for row in page.rows} == {thread.thread_id for thread in normal}
        children = await app.thread_activity(
            project_id="project-main", lead_thread_id=lead.thread_id, limit=5, include_active=True
        )
        assert children.total == 7 and len(children.rows) == 5
        more = await app.thread_activity(
            project_id="project-main",
            lead_thread_id=lead.thread_id,
            cursor=children.next_cursor,
            limit=5,
            include_active=True,
        )
        assert len(more.rows) == 2 and more.next_cursor is None
        assert {row.thread.thread_id for row in (*children.rows, *more.rows)} == {
            thread.thread_id for thread in workers
        }
        assert all(row.thread.lead_thread_id == lead.thread_id for row in more.rows)
        with pytest.raises(ThreadError, match="another query"):
            await app.thread_activity(
                project_id="project-main", independent_only=True, cursor=children.next_cursor, include_active=True
            )
        with pytest.raises(ThreadError, match="not both"):
            await app.thread_activity(project_id="project-main", independent_only=True, lead_thread_id=lead.thread_id)
        # Search keeps managed roots discoverable, independently of sidebar membership.
        search = await app.thread_activity(project_id=None, query="Worker")
        assert search.total == 7
