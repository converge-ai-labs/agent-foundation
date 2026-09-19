"""Device recipes and exact working-directory selections remain inert and durable."""

import json

import pytest
from a13n_harness_ui.composition import (
    AgentCompositionResolver,
    CompositionAcceptanceService,
    ThreadCompositionSelection,
)
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration.models import DeviceResource
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection, validate_environment_selection
from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import ThreadConfigurationMutation, ThreadConfigurationPatch, open_local_store
from a13n_harness_ui.thread_service import RootThreadDefaults, ThreadService
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


def device():
    return DeviceResource.model_validate(
        {
            "schema_version": "1",
            "kind": "device",
            "id": "device-build",
            "name": "Build",
            "device_id": "native-build",
            "transport": {"kind": "http", "configuration": {"endpoint": "https://device.example"}},
            "authentication": {"kind": "api_key", "env": "DEVICE_TOKEN"},
        }
    )


def binding(path="/C:/work"):
    return EnvironmentBindingSelection(device_id="device-build", working_directory=path, alias="build")


async def source(tmp_path):
    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text('schema_version: "1"\ndefaults:\n  agent: agent-main\n')
    resources = {
        "devices/build.yaml": device().model_dump(mode="json"),
        "models/main.yaml": {
            "schema_version": "1",
            "kind": "model",
            "id": "model-main",
            "name": "Main",
            "route": "openai:gpt-5",
            "authentication": {"kind": "api_key", "env": "MODEL_KEY"},
        },
        "agents/main.yaml": {
            "schema_version": "1",
            "kind": "agent",
            "id": "agent-main",
            "name": "Main",
            "model": "model-main",
        },
        "projects/remote.yaml": {
            "schema_version": "1",
            "kind": "project",
            "id": "project-remote",
            "name": "Remote",
            "roots": [],
            "defaults": {"environment_bindings": [binding().model_dump(mode="json")], "default_environment": "build"},
        },
    }
    for relative, value in resources.items():
        path = tmp_path / relative
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(value))
    return await load_harness_ui_configuration(root)


async def test_device_catalog_and_remote_only_project_require_no_connection(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVICE_TOKEN", raising=False)
    loaded = await source(tmp_path)
    assert loaded.devices["device-build"] == device()
    assert loaded.projects["project-remote"].roots == ()
    assert loaded.projects["project-remote"].defaults.environment_bindings == (binding(),)
    assert '"session_id"' not in loaded.model_dump_json()


@pytest.mark.parametrize(
    "alias", ["workspace", "workspace-2", "thread-files", "configuration", "builtin-skills", "content-plugin-1"]
)
def test_device_binding_rejects_host_aliases(alias):
    with pytest.raises(ValidationError):
        EnvironmentBindingSelection(device_id="device-build", working_directory="/work", alias=alias)


def test_binding_selection_requires_exact_default_and_unique_aliases():
    with pytest.raises(ValueError, match="explicit"):
        validate_environment_selection((binding(),), None, local_root_count=0)
    with pytest.raises(ValueError, match="unique"):
        validate_environment_selection((binding(), binding()), "build", local_root_count=0)
    with pytest.raises(ValueError, match="available"):
        validate_environment_selection((binding(),), "workspace", local_root_count=0)
    validate_environment_selection((binding(),), "workspace", local_root_count=1)
    with pytest.raises(ValidationError):
        binding("relative")
    with pytest.raises(ValidationError):
        DeviceResource.model_validate({**device().model_dump(), "token": "forbidden"})


async def test_thread_binding_roundtrip_patch_and_capture(tmp_path):
    loaded = await source(tmp_path)
    resolver = AgentCompositionResolver()
    async with open_local_store(StorageSettings(data_root=tmp_path / "data")) as store:
        accepted = CompositionAcceptanceService(store, resolver)
        await accepted.accept(loaded, expected_current_digest=None)
        service = ThreadService(store=store, configurations=accepted)
        thread = await service.create(defaults=RootThreadDefaults(project_id="project-remote"))
        assert thread.configuration.environment_bindings == (binding(),)
        assert thread.configuration.default_environment == "build"
        assert (await service.get(thread.thread_id)).configuration == thread.configuration
        selected = ThreadCompositionSelection(
            thread_id=thread.thread_id,
            version=1,
            project_id="project-remote",
            agent_source_kind="agent",
            agent_source_id="agent-main",
            environment_profile_id="environment-native",
            harness_plugin_ids=(),
            environment_run_extension_ids=(),
            mcp_server_ids=(),
            environment_bindings=thread.configuration.environment_bindings,
            default_environment="build",
        )
        capture = resolver.resolve_run(loaded, selected)
        assert capture.project_roots == ()
        assert capture.environment_bindings[0].selection.working_directory == "/C:/work"
        loaded.devices["device-build"] = device().model_copy(update={"name": "Changed"})
        assert capture.environment_bindings[0].device.name == "Build"
        updated = await service.update_configuration(
            thread_id=thread.thread_id,
            mutation=ThreadConfigurationMutation(
                expected_version=1, patch=ThreadConfigurationPatch(environment_bindings=(), default_environment=None)
            ),
        )
        assert updated.configuration.environment_bindings == ()
        assert updated.configuration.default_environment is None
        with pytest.raises(ThreadError, match="version"):
            await service.update_configuration(
                thread_id=thread.thread_id,
                mutation=ThreadConfigurationMutation(
                    expected_version=1, patch=ThreadConfigurationPatch(default_environment="build")
                ),
            )


async def test_remote_binding_state_identity_tracks_behavior_not_presentation(tmp_path, monkeypatch):
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.composition.models import ResolvedEnvironmentBinding

    from .test_app import _settings

    monkeypatch.setenv("DEVICE_TOKEN", "fixture-key")
    await source(tmp_path)
    async with open_harness_ui_app(
        _settings(tmp_path / "data"), configuration_path=tmp_path / "a13n-harness-ui.yaml"
    ) as app:
        service = app._root_runs._executor._environments
        selected = ResolvedEnvironmentBinding(device=device(), selection=binding())

        async def key(value):
            mount = await service._prepare_device_mount("thread-test", value)
            await mount.environment.close()
            return mount.key

        original = await key(selected)
        assert (
            await key(selected.model_copy(update={"device": device().model_copy(update={"name": "Renamed"})}))
            == original
        )
        for field, value in (("alias", "second"), ("working_directory", "/C:/other")):
            changed = selected.model_copy(update={"selection": binding().model_copy(update={field: value})})
            assert await key(changed) != original
        changed_device = device().model_copy(update={"device_id": "different-native-device"})
        assert await key(selected.model_copy(update={"device": changed_device})) != original
        transport = device().transport.model_copy(
            update={
                "configuration": device().transport.configuration.model_copy(
                    update={"endpoint": "https://other.example"}
                )
            }
        )
        assert (
            await key(selected.model_copy(update={"device": device().model_copy(update={"transport": transport})}))
            != original
        )


async def test_receipt_freezes_device_recipe_and_thread_binding_before_preparation(tmp_path, monkeypatch):
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus
    from anyio import Event

    from .test_app import _CompletedReconstructor, _settings

    await source(tmp_path)
    async with open_harness_ui_app(
        _settings(tmp_path / "data"), configuration_path=tmp_path / "a13n-harness-ui.yaml"
    ) as app:
        executor = app._root_runs._executor
        executor._agents = _CompletedReconstructor()
        prepare = executor._environments.prepare
        entered, release = Event(), Event()
        captures = []

        async def delayed(composition):
            captures.append(composition)
            entered.set()
            await release.wait()
            # Admission is tested without connecting to the fictional Device.
            return await prepare(
                composition.model_copy(update={"environment_bindings": (), "default_environment": None})
            )

        monkeypatch.setattr(executor._environments, "prepare", delayed)
        thread = await app.create_thread(defaults=NewThreadDefaults(project_id="project-remote"))
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Capture")
        await entered.wait()
        try:
            await app._threads.update_configuration(
                thread_id=thread.thread_id,
                mutation=ThreadConfigurationMutation(
                    expected_version=1,
                    patch=ThreadConfigurationPatch(environment_bindings=(), default_environment=None),
                ),
            )
            changed = device().model_dump(mode="json")
            changed["transport"]["configuration"]["endpoint"] = "https://changed.example"
            (tmp_path / "devices/build.yaml").write_text(json.dumps(changed))
            await app._reload_configuration_from_path()
        finally:
            release.set()
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        captured = captures[0]
        assert captured.thread_configuration_version == 1
        assert captured.environment_bindings[0].device == device()
        assert captured.environment_bindings[0].selection == binding()
        assert captured.default_environment == "build"
        assert (await app._threads.get(thread.thread_id)).configuration.environment_bindings == ()


@pytest.mark.parametrize(
    "patch", [None, {"project_id": None}, {"environment_bindings": (), "default_environment": None}]
)
async def test_deferred_response_retains_captured_bindings_unless_explicitly_patched(tmp_path, monkeypatch, patch):
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.surfaces import (
        ExternalToolResult,
        NewThreadDefaults,
        RootOperationStatus,
        ThreadDeferredResponse,
    )

    from .test_app import _DeferredReconstructor, _settings

    await source(tmp_path)
    async with open_harness_ui_app(
        _settings(tmp_path / "data"), configuration_path=tmp_path / "a13n-harness-ui.yaml"
    ) as app:
        executor = app._root_runs._executor
        executor._agents = _DeferredReconstructor()
        prepare = executor._environments.prepare
        captures = []

        async def without_remote_entry(composition):
            captures.append(composition)
            return await prepare(
                composition.model_copy(update={"environment_bindings": (), "default_environment": None})
            )

        monkeypatch.setattr(executor._environments, "prepare", without_remote_entry)
        thread = await app.create_thread(defaults=NewThreadDefaults(project_id="project-remote"))
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Ask")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.suspended
        detail = await app.get_thread(thread.thread_id)
        await app._threads.update_configuration(
            thread_id=thread.thread_id,
            mutation=ThreadConfigurationMutation(
                expected_version=1, patch=ThreadConfigurationPatch(environment_bindings=(), default_environment=None)
            ),
        )
        resumed = await app.respond_thread(
            thread_id=thread.thread_id,
            response=ThreadDeferredResponse(
                expected_continuation_id=detail.continuation_id,
                responses=(ExternalToolResult(request_id=detail.deferred_requests[0].request_id, result="Answer"),),
            ),
            mutation=None
            if patch is None
            else ThreadConfigurationMutation(expected_version=2, patch=ThreadConfigurationPatch(**patch)),
        )
        assert (await app.wait_root_operation(resumed.receipt_id)).status is RootOperationStatus.completed
        retained = patch is None or "environment_bindings" not in patch
        assert captures[-1].environment_bindings == (captures[0].environment_bindings if retained else ())
        assert captures[-1].default_environment == ("build" if retained else None)
