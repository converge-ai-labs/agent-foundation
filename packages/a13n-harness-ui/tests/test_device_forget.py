"""Forgetting offline configuration preserves captures and allows explicit repair."""

import json

import httpx
import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.devices import DeviceConnections
from a13n_harness_ui.storage import ThreadConfigurationMutation, ThreadConfigurationPatch
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus
from a13n_harness_ui.webui import create_webui

from .test_app import _CompletedReconstructor, _settings
from .test_device_configuration import binding, source

pytestmark = pytest.mark.anyio


async def no_device_credentials(*args, **kwargs):
    pytest.fail("Configuration removal must not resolve credentials or connect to a Device")


async def test_offline_forget_http_checks_graph_references_and_repairs_sticky_selection(tmp_path, monkeypatch):
    await source(tmp_path)
    monkeypatch.setattr(DeviceConnections, "_credential", no_device_credentials)
    server = create_webui(
        lambda: open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=tmp_path / "a13n-harness-ui.yaml"),
        api_key="test-device-forget",
    )
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server),
            base_url="http://localhost",
            headers={"Authorization": "Bearer test-device-forget"},
        ) as client,
    ):
        projects = await client.get("/api/projects")
        assert projects.status_code == 200, projects.text
        assert projects.json()[0]["roots"] == []
        created = await client.post("/api/threads", json={"defaults": {"project_id": "project-remote"}})
        assert created.status_code == 200, created.text
        thread = created.json()
        url = f"/api/threads/{thread['thread_id']}/configuration"
        blocked = await client.delete("/api/configuration/sources/devices/build.yaml")
        assert blocked.status_code == 400, blocked.text
        assert blocked.json()["error"]["code"] == "configuration_invalid"
        assert (tmp_path / "devices/build.yaml").exists()

        # Clear the source graph reference explicitly, not the existing Thread.
        project = json.loads((tmp_path / "projects/remote.yaml").read_text())
        project["roots"] = [{"path": str(tmp_path)}]
        project["defaults"] = {}
        changed = await client.put(
            "/api/configuration/sources/projects/remote.yaml", json={"content": json.dumps(project)}
        )
        assert changed.status_code == 200, changed.text
        deleted = await client.delete("/api/configuration/sources/devices/build.yaml")
        assert deleted.status_code == 200, deleted.text
        assert not (tmp_path / "devices/build.yaml").exists()
        assert (await client.get("/api/devices")).json() == []
        inspect = await client.get(url)
        assert inspect.status_code == 200, inspect.text
        assert inspect.json()["next_run"]["configuration"] == thread["configuration"]
        provenance = inspect.json()["next_run"]["provenance"]
        assert provenance["environment_bindings"] == "thread"
        assert provenance["default_environment"] == "thread"

        invalid = await client.patch(url, json={"expected_version": 1, "patch": {"environment_bindings": []}})
        assert invalid.status_code == 400, invalid.text
        repaired = await client.patch(
            url,
            json={
                "expected_version": 1,
                "patch": {
                    "local_roots": [str(tmp_path)],
                    "environment_bindings": [],
                    "default_environment": "workspace",
                },
            },
        )
        assert repaired.status_code == 200, repaired.text
        selected = repaired.json()["configuration"]
        assert selected["version"] == 2
        assert selected["environment_bindings"] == []
        assert selected["default_environment"] == "workspace"
        assert selected["agent_source"] == thread["configuration"]["agent_source"]


async def test_forget_and_repair_leave_completed_run_capture_and_history_unchanged(tmp_path, monkeypatch):
    await source(tmp_path)
    monkeypatch.setattr(DeviceConnections, "_credential", no_device_credentials)
    async with open_harness_ui_app(
        _settings(tmp_path / "data"), configuration_path=tmp_path / "a13n-harness-ui.yaml"
    ) as app:
        executor = app._root_runs._executor
        executor._agents = _CompletedReconstructor()
        prepare = executor._environments.prepare

        async def local_execution(composition):
            # Test durable capture/removal independently of actual remote execution.
            return await prepare(
                composition.model_copy(update={"environment_bindings": (), "default_environment": None})
            )

        monkeypatch.setattr(executor._environments, "prepare", local_execution)
        thread = await app.create_thread(defaults=NewThreadDefaults(project_id="project-remote"))
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Keep this history")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        capture = await app.inspect_operation_configuration(receipt.receipt_id)
        detail = await app.get_thread(thread.thread_id)
        transcript = await app.get_thread_transcript(thread_id=thread.thread_id)
        inspection = await app.inspect_thread_configuration(thread.thread_id)
        assert capture is not None
        assert capture.environment_bindings == (binding(),)

        project = json.loads((tmp_path / "projects/remote.yaml").read_text())
        project["roots"] = [{"path": str(tmp_path)}]
        project["defaults"] = {}
        await app.mutate_configuration(
            relative_path="projects/remote.yaml", request=ResourceMutationRequest(content=json.dumps(project))
        )
        await app.delete_configuration(relative_path="devices/build.yaml")
        forgotten = await app.inspect_thread_configuration(thread.thread_id)
        assert forgotten.captured == inspection.captured
        assert forgotten.next_run.configuration.environment_bindings == (binding(),)
        await app.update_thread_configuration(
            thread_id=thread.thread_id,
            mutation=ThreadConfigurationMutation(
                expected_version=1,
                patch=ThreadConfigurationPatch(
                    local_roots=(str(tmp_path),), environment_bindings=(), default_environment="workspace"
                ),
            ),
        )
        assert await app.inspect_operation_configuration(receipt.receipt_id) == capture
        repaired = await app.get_thread(thread.thread_id)
        assert repaired.continuation_id == detail.continuation_id
        assert await app.get_thread_transcript(thread_id=thread.thread_id) == transcript
        assert (await app.inspect_thread_configuration(thread.thread_id)).captured == inspection.captured


async def test_forget_local_profile_requires_default_repairs_but_does_not_cascade_into_threads(tmp_path):
    from a13n_harness_ui.errors import ConfigurationError

    await source(tmp_path)
    (tmp_path / "extensions").mkdir()
    (tmp_path / "extensions/local.yaml").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "kind": "environment_profile",
                "id": "environment-custom",
                "name": "Custom local",
                "provider_key": "direct_local",
                "provider_configuration": {},
                "adapter_key": "a13n.native-project-root",
                "adapter_configuration": {},
            }
        )
    )
    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text('schema_version: "1"\ndefaults:\n  agent: agent-main\n  environment_profile: environment-custom\n')
    project_path = tmp_path / "projects/remote.yaml"
    project = json.loads(project_path.read_text())
    project["defaults"]["environment_profile"] = "environment-custom"
    project_path.write_text(json.dumps(project))
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        thread = await app.create_thread(defaults=NewThreadDefaults(project_id="project-remote"))
        assert thread.configuration.environment_profile_id == "environment-custom"
        with pytest.raises(ConfigurationError):
            await app.delete_configuration(relative_path="extensions/local.yaml")
        await app.mutate_configuration(
            relative_path=root.name,
            request=ResourceMutationRequest(
                content='schema_version: "1"\ndefaults:\n  agent: agent-main\n',
            ),
        )
        with pytest.raises(ConfigurationError):
            await app.delete_configuration(relative_path="extensions/local.yaml")
        del project["defaults"]["environment_profile"]
        await app.mutate_configuration(
            relative_path="projects/remote.yaml", request=ResourceMutationRequest(content=json.dumps(project))
        )
        await app.delete_configuration(relative_path="extensions/local.yaml")
        inspect = await app.inspect_thread_configuration(thread.thread_id)
        assert inspect.next_run.configuration.model_dump() == thread.configuration.model_dump()
        repaired = await app.update_thread_configuration(
            thread_id=thread.thread_id,
            mutation=ThreadConfigurationMutation(
                expected_version=1, patch=ThreadConfigurationPatch(environment_profile_id="environment-native")
            ),
        )
        assert repaired.configuration.environment_profile_id == "environment-native"
        assert repaired.configuration.environment_bindings == thread.configuration.environment_bindings
        assert repaired.configuration.default_environment == "build"
