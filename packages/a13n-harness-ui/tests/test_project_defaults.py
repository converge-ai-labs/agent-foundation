from __future__ import annotations

import json
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration import (
    LoadedHarnessUiConfiguration,
    ResourceMutationRequest,
    load_harness_ui_configuration,
)
from a13n_harness_ui.errors import ConfigurationError, ThreadError
from a13n_harness_ui.surfaces import (
    NewThreadDefaults,
    ProjectDefaultsApply,
    ThreadConfigurationMutationInput,
    ThreadConfigurationPatch,
)
from a13n_harness_ui.thread_service import RootThreadDefaults, resolve_thread_configuration

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


def _project(path: Path, defaults: dict) -> str:
    return json.dumps(
        {
            "schema_version": "1",
            "kind": "project",
            "id": "project-main",
            "name": "Main",
            "roots": [{"path": str(path)}],
            "defaults": defaults,
        }
    )


def _source_tree(path: Path, project: dict, agent: dict | None = None) -> Path:
    root = _write_configuration(path)
    root.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "defaults": {
                    "project": "project-main",
                    "agent": "agent-assistant",
                    "environment_profile": "environment-sandbox",
                    "harness_plugins": ["plugin-global"],
                    "environment_run_extensions": ["extension-global"],
                    "mcp_servers": ["mcp-global"],
                },
            }
        )
    )
    for layer in ("global", "agent", "project", "explicit"):
        for directory, kind, resource_id, extra in (
            ("extensions", "harness_plugin", f"plugin-{layer}", {"plugin_key": "example.plugin"}),
            ("extensions", "environment_run_extension", f"extension-{layer}", {"extension_key": "example.extension"}),
            ("mcp", "mcp_server", f"mcp-{layer}", {"transport": {"command": "unused"}}),
        ):
            target = path / directory / f"{resource_id}.yaml"
            target.parent.mkdir(exist_ok=True)
            target.write_text(
                json.dumps({"schema_version": "1", "kind": kind, "id": resource_id, "name": layer, **extra})
            )
    (path / "agents/project.yaml").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "kind": "agent",
                "id": "agent-project",
                "name": "Project Agent",
                "model": "model-primary",
                **(agent or {}),
            }
        )
    )
    (path / "projects/main.yaml").write_text(_project(path / "workspace", project))
    return root


@pytest.mark.parametrize(
    "project_values,explicit,expected",
    [
        ({}, None, "agent"),
        ({"harness_plugins": ["plugin-project"], "mcp_servers": ["mcp-project"]}, None, "project"),
        ({"harness_plugins": [], "mcp_servers": []}, None, "none"),
        ({"harness_plugins": ["plugin-project"], "mcp_servers": ["mcp-project"]}, "explicit", "explicit"),
        ({"harness_plugins": ["plugin-project"], "mcp_servers": ["mcp-project"]}, "none", "none"),
    ],
)
async def test_collection_precedence_replaces_whole_lists(tmp_path, project_values, explicit, expected):
    root = _source_tree(
        tmp_path,
        {"agent": "agent-project", **project_values},
        {"harness_plugins": ["plugin-agent"], "mcp_servers": ["mcp-agent"]},
    )
    source = await load_harness_ui_configuration(root)
    values = (
        {}
        if explicit is None
        else {
            "harness_plugin_ids": () if explicit == "none" else ("plugin-explicit",),
            "mcp_server_ids": () if explicit == "none" else ("mcp-explicit",),
        }
    )
    result = resolve_thread_configuration(source, RootThreadDefaults(**values))
    assert result.agent_source.id == "agent-project"
    assert result.harness_plugin_ids == (() if expected == "none" else (f"plugin-{expected}",))
    assert result.mcp_server_ids == (() if expected == "none" else (f"mcp-{expected}",))
    assert result.environment_run_extension_ids == ("extension-global",)
    assert (
        resolve_thread_configuration(
            LoadedHarnessUiConfiguration.model_validate_json(source.model_dump_json()), RootThreadDefaults(**values)
        )
        == result
    )


async def test_projectless_explicit_agent_and_environment_defaults(tmp_path):
    root = _source_tree(
        tmp_path,
        {"agent": "agent-project", "environment_profile": "environment-native", "environment_run_extensions": []},
    )
    source = await load_harness_ui_configuration(root)
    project = resolve_thread_configuration(source)
    assert project.agent_source.id == "agent-project"
    assert project.environment_profile_id == "environment-native"
    assert project.harness_plugin_ids == ("plugin-global",)
    assert project.environment_run_extension_ids == ()
    explicit = resolve_thread_configuration(
        source,
        RootThreadDefaults(
            agent_id="agent-assistant",
            environment_profile_id="environment-sandbox",
            environment_run_extension_ids=("extension-explicit",),
        ),
    )
    assert explicit.agent_source.id == "agent-assistant"
    assert explicit.environment_profile_id == "environment-sandbox"
    assert explicit.environment_run_extension_ids == ("extension-explicit",)
    projectless = resolve_thread_configuration(source, RootThreadDefaults(project_id=None))
    assert projectless.project_id is None
    assert projectless.agent_source.id == "agent-assistant"
    assert projectless.environment_profile_id == "environment-sandbox"
    assert projectless.environment_run_extension_ids == ("extension-global",)


@pytest.mark.parametrize(
    "defaults",
    [
        {"agent": "agent-missing"},
        {"agent": "model-primary"},
        {"environment_profile": "environment-missing"},
        {"harness_plugins": ["plugin-missing"]},
        {"mcp_servers": ["mcp-global", "mcp-global"]},
        {"environment_run_extensions": ["extension-missing"]},
        {"project": "project-main"},
    ],
)
async def test_invalid_project_defaults_reject_the_complete_generation(tmp_path, defaults):
    root = _source_tree(tmp_path, defaults)
    with pytest.raises(ConfigurationError):
        await load_harness_ui_configuration(root)


async def test_old_project_files_still_resolve_and_new_normalization_changes_generation(tmp_path, monkeypatch):
    from a13n_harness_ui.configuration import loader

    root = _write_configuration(tmp_path)
    current = await load_harness_ui_configuration(root)
    assert current.projects["project-main"].defaults.model_dump() == {}
    assert resolve_thread_configuration(current).environment_profile_id == "environment-native"
    monkeypatch.setattr(loader, "_NORMALIZATION_VERSION", "2")
    old_digest = (await load_harness_ui_configuration(root)).source_digest
    assert old_digest != current.source_digest
    # Historical normalized Project objects remain decodable without defaults.
    legacy = current.model_dump(mode="json")
    assert "defaults" not in legacy["projects"]["project-main"]
    assert (
        LoadedHarnessUiConfiguration.model_validate_json(json.dumps(legacy))
        .projects["project-main"]
        .defaults.model_dump()
        == {}
    )


async def test_project_preview_apply_exactness_conflicts_and_restart(tmp_path):
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        before = await app.status()
        preview = await app.preview_thread_configuration()
        assert (await app.status()).object_count == before.object_count
        thread = await app.create_thread()
        assert thread.configuration.model_dump() == preview.model_dump()
        content = _project(tmp_path / "workspace", {"environment_profile": "environment-sandbox", "mcp_servers": []})
        await app.mutate_configuration(
            relative_path="projects/main.yaml", request=ResourceMutationRequest(content=content)
        )
        assert (
            await app.get_thread(thread.thread_id)
        ).thread.configuration.environment_profile_id == "environment-native"
        new_thread = await app.create_thread()
        assert new_thread.configuration.environment_profile_id == "environment-sandbox"
        preview = await app.preview_project_defaults(thread_id=thread.thread_id)
        assert preview.patch.model_fields_set == {"environment_profile_id", "mcp_server_ids"}
        assert preview.current.agent_source == preview.replacement.agent_source
        request = ProjectDefaultsApply(
            expected_version=preview.expected_version, defaults_digest=preview.defaults_digest
        )
        changed = await app.apply_project_defaults(thread_id=thread.thread_id, request=request)
        assert changed.configuration.version == 2
        assert changed.configuration.environment_profile_id == "environment-sandbox"
        with pytest.raises(ThreadError, match="preview again"):
            await app.apply_project_defaults(thread_id=thread.thread_id, request=request)
        fresh = await app.preview_project_defaults(thread_id=thread.thread_id)
        content = _project(tmp_path / "workspace", {"environment_profile": "environment-native"})
        await app.mutate_configuration(
            relative_path="projects/main.yaml", request=ResourceMutationRequest(content=content)
        )
        with pytest.raises(ThreadError, match="preview again"):
            await app.apply_project_defaults(
                thread_id=thread.thread_id,
                request=ProjectDefaultsApply(
                    expected_version=fresh.expected_version, defaults_digest=fresh.defaults_digest
                ),
            )
        cleared = await app.patch_thread_configuration(
            thread_id=thread.thread_id,
            mutation=ThreadConfigurationMutationInput(
                expected_version=2, patch=ThreadConfigurationPatch(project_id=None)
            ),
        )
        assert cleared.configuration.project_id is None
        with pytest.raises(ThreadError, match="no available Project"):
            await app.preview_project_defaults(thread_id=thread.thread_id)
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        restored = (await app.get_thread(thread.thread_id)).thread.configuration
        assert restored.project_id is None
        assert restored.environment_profile_id == "environment-sandbox"
        assert restored.version == 3


async def test_skill_preview_uses_the_same_project_agent_as_creation(tmp_path):
    root = _write_configuration(tmp_path)
    (tmp_path / "agents/project.yaml").write_text(
        'schema_version: "1"\nkind: agent\nid: agent-project\nname: Project\nmodel: model-primary\ncapabilities: [{capability: skills}]\n'
    )
    (tmp_path / "projects/main.yaml").write_text(_project(tmp_path / "workspace", {"agent": "agent-project"}))
    skill = tmp_path / "workspace/.agents/skills/example/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: example\ndescription: Example skill.\n---\nUse examples.\n")
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        catalog = await app.skill_catalog(defaults=NewThreadDefaults())
        thread = await app.create_thread()
        after = await app.skill_catalog(thread_id=thread.thread_id)
        assert catalog.items == after.items
        assert catalog.items
        assert thread.configuration.agent_source.id == "agent-project"


async def test_apply_defaults_leaves_captured_run_unchanged(tmp_path, monkeypatch):
    from anyio import Event, fail_after

    from .test_app import _CompletedReconstructor, _SlowReconstructor

    root = _write_configuration(tmp_path)
    (tmp_path / "agents/other.yaml").write_text(
        'schema_version: "1"\nkind: agent\nid: agent-other\nname: Other\nmodel: model-primary\n'
    )
    started = Event()
    slow = _SlowReconstructor(started)
    captured = []
    reconstruct = slow.reconstruct

    def capture(composition, **kwargs):
        captured.append(composition)
        return reconstruct(composition, **kwargs)

    monkeypatch.setattr(slow, "reconstruct", capture)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        app._root_runs._executor._agents = slow
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="first")
        with fail_after(5):
            await started.wait()
        before = captured[0].model_dump_json()
        await app.mutate_configuration(
            relative_path="projects/main.yaml",
            request=ResourceMutationRequest(content=_project(tmp_path / "workspace", {"agent": "agent-other"})),
        )
        preview = await app.preview_project_defaults(thread_id=thread.thread_id)
        changed = await app.apply_project_defaults(
            thread_id=thread.thread_id,
            request=ProjectDefaultsApply(
                expected_version=preview.expected_version, defaults_digest=preview.defaults_digest
            ),
        )
        assert changed.configuration.agent_source.id == "agent-other"
        assert captured[0].root.source_id == "agent-assistant"
        assert captured[0].model_dump_json() == before
        assert (await app.cancel_root_operation(receipt.receipt_id)).accepted
        await app.wait_root_operation(receipt.receipt_id)
        completed = _CompletedReconstructor()
        reconstruct_next = completed.reconstruct

        def capture_next(composition, **kwargs):
            captured.append(composition)
            return reconstruct_next(composition, **kwargs)

        monkeypatch.setattr(completed, "reconstruct", capture_next)
        app._root_runs._executor._agents = completed
        next_receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="second")
        operation = await app.wait_root_operation(next_receipt.receipt_id)
        assert operation.status.value == "completed"
        assert captured[-1].root.source_id == "agent-other"
        assert captured[-1].thread_configuration_version == 2


async def test_empty_defaults_and_concurrent_apply(tmp_path):
    from anyio import create_task_group

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        thread = await app.create_thread()
        empty = await app.preview_project_defaults(thread_id=thread.thread_id)
        assert empty.patch.is_empty and empty.current == empty.replacement
        with pytest.raises(ThreadError) as rejected:
            await app.apply_project_defaults(
                thread_id=thread.thread_id,
                request=ProjectDefaultsApply(expected_version=1, defaults_digest=empty.defaults_digest),
            )
        assert rejected.value.code == "project_defaults_empty"
        await app.mutate_configuration(
            relative_path="projects/main.yaml",
            request=ResourceMutationRequest(
                content=_project(tmp_path / "workspace", {"environment_profile": "environment-sandbox"})
            ),
        )
        preview = await app.preview_project_defaults(thread_id=thread.thread_id)
        request = ProjectDefaultsApply(expected_version=1, defaults_digest=preview.defaults_digest)
        outcomes = []

        async def apply():
            try:
                await app.apply_project_defaults(thread_id=thread.thread_id, request=request)
                outcomes.append("applied")
            except ThreadError as exc:
                outcomes.append(exc.code)

        async with create_task_group() as tasks:
            tasks.start_soon(apply)
            tasks.start_soon(apply)
        assert sorted(outcomes) == ["applied", "project_defaults_stale"]


@pytest.mark.parametrize("global_agent", [None, "agent-assistant"])
async def test_terminal_readiness_uses_project_defaults_without_a_usable_global_agent(tmp_path, global_agent):
    from a13n_harness_ui.cli import CliRequest
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.interactive.rendering import Status

    root = _write_configuration(tmp_path)
    root.write_text(json.dumps({"schema_version": "1", "defaults": {"agent": global_agent}}))
    (tmp_path / "agents/assistant.yaml").write_text(
        'schema_version: "1"\nkind: agent\nid: agent-assistant\nname: No model\n'
    )
    (tmp_path / "agents/project.yaml").write_text(
        'schema_version: "1"\nkind: agent\nid: agent-project\nname: Project\nmodel: model-primary\n'
    )
    (tmp_path / "projects/main.yaml").write_text(
        _project(tmp_path / "workspace", {"agent": "agent-project", "environment_profile": "environment-native"})
    )
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path / "workspace", Status())
        assert await backend.initialize()
        assert backend.status.agent == "Project"
        assert backend.status.model == "openai:gpt-5"
        assert backend.thread_id is None
        external = tmp_path / "workspace/.claude/agents/reviewer.md"
        external.parent.mkdir(parents=True)
        external.write_text("---\nname: reviewer\ndescription: Review code.\n---\nReview carefully.\n")
        await backend.import_choices("claude-code", "project")
        await backend.import_and_enroll(("reviewer",))
        accepted = await app.current_configuration()
        assert accepted.agents["agent-project"].subagents
        assert accepted.agents["agent-assistant"].subagents == ()
        thread_id = await backend.ensure_session()
        assert (await app.get_thread(thread_id)).thread.configuration.agent_source.id == "agent-project"
        assert backend.status.environment == "environment-native"
        explicit = SessionBackend(app, CliRequest(agent_id="agent-assistant"), tmp_path / "workspace", Status())
        assert not await explicit.initialize()


@pytest.mark.parametrize("layer", ["global", "agent", "project", "explicit"])
async def test_creation_provenance_tracks_the_winning_axis_even_for_empty_lists(tmp_path, layer):
    from a13n_harness_ui.thread_service import resolve_thread_configuration_details

    project = {"agent": "agent-project"}
    agent = {}
    explicit = RootThreadDefaults()
    if layer == "agent":
        agent = {"mcp_servers": [], "harness_plugins": []}
    elif layer == "project":
        project.update(mcp_servers=[], harness_plugins=[])
    elif layer == "explicit":
        explicit = RootThreadDefaults(mcp_server_ids=(), harness_plugin_ids=())
    source = await load_harness_ui_configuration(_source_tree(tmp_path, project, agent))
    value = resolve_thread_configuration_details(source, explicit)
    assert value.configuration == resolve_thread_configuration(source, explicit)
    assert value.provenance.mcp_server_ids == value.provenance.harness_plugin_ids == layer
    assert value.provenance.agent_source == "project"
    assert value.provenance.project_id == "global"
    projectless = resolve_thread_configuration_details(source, RootThreadDefaults(project_id=None))
    assert projectless.configuration.project_id is None and projectless.provenance.project_id == "explicit"


async def test_thread_local_roots_are_independent_path_references(tmp_path):
    from a13n_harness_ui.root_execution import _selection

    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    original = str(tmp_path / "workspace")
    replacement = str(tmp_path / "not-created")
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread()
        assert thread.configuration.local_roots == (original,)
        await app.mutate_configuration(
            relative_path="projects/main.yaml",
            request=ResourceMutationRequest(content=_project(tmp_path / "not-created", {})),
        )
        assert (await app.create_thread()).configuration.local_roots == (replacement,)
        stored = await app._threads.get(thread.thread_id)
        assert stored.configuration.local_roots == (original,)
        captured = await app._root_runs._executor._compositions.publish(
            await app.current_configuration(), _selection(stored)
        )
        assert captured.value.project_roots == (original,)
        cleared_project = await app.patch_thread_configuration(
            thread_id=thread.thread_id,
            mutation=ThreadConfigurationMutationInput(
                expected_version=1, patch=ThreadConfigurationPatch(project_id=None)
            ),
        )
        assert cleared_project.configuration.local_roots == (original,)
        explicit = await app.create_thread(defaults=NewThreadDefaults(project_id=None, local_roots=(replacement,)))
        assert explicit.configuration.local_roots == (replacement,)
        empty = await app.create_thread(defaults=NewThreadDefaults(local_roots=()))
        assert empty.configuration.local_roots == ()
        assert not (tmp_path / "not-created").exists()
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        restored = (await app.get_thread(thread.thread_id)).thread.configuration
        assert restored.local_roots == (original,) and restored.project_id is None
        cleared = await app.patch_thread_configuration(
            thread_id=thread.thread_id,
            mutation=ThreadConfigurationMutationInput(
                expected_version=2, patch=ThreadConfigurationPatch(local_roots=())
            ),
        )
        assert cleared.configuration.local_roots == ()


async def test_apply_project_environments_replaces_only_environment_axes(tmp_path):
    from .test_device_configuration import binding, source

    await source(tmp_path)
    root = tmp_path / "a13n-harness-ui.yaml"
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread(
            defaults=NewThreadDefaults(
                project_id="project-remote",
                local_roots=(str(tmp_path / "old"),),
                default_model_id="model-main",
            )
        )
        assert thread.configuration.environment_bindings == (binding(),)
        project = {
            "schema_version": "1",
            "kind": "project",
            "id": "project-remote",
            "name": "Local now",
            "roots": [{"path": str(tmp_path / "new")}],
            "defaults": {"environment_profile": "environment-sandbox"},
        }
        await app.mutate_configuration(
            relative_path="projects/remote.yaml", request=ResourceMutationRequest(content=json.dumps(project))
        )
        preview = await app.preview_project_defaults(thread_id=thread.thread_id, environments_only=True)
        assert preview.patch.model_fields_set == {
            "local_roots",
            "environment_profile_id",
            "environment_bindings",
            "default_environment",
        }
        assert preview.replacement.environment_bindings == ()
        assert preview.replacement.default_environment is None
        assert preview.replacement.local_roots == (str(tmp_path / "new"),)
        assert preview.replacement.default_model_id == "model-main"
        project["defaults"]["mcp_servers"] = []
        await app.mutate_configuration(
            relative_path="projects/remote.yaml", request=ResourceMutationRequest(content=json.dumps(project))
        )
        applied = await app.apply_project_defaults(
            thread_id=thread.thread_id,
            environments_only=True,
            request=ProjectDefaultsApply(
                expected_version=preview.expected_version,
                defaults_digest=preview.defaults_digest,
            ),
        )
        assert applied.configuration.environment_bindings == ()
        assert applied.configuration.default_model_id == "model-main"
        assert applied.configuration.agent_source == thread.configuration.agent_source
        preview = await app.preview_project_defaults(thread_id=thread.thread_id, environments_only=True)
        project["roots"] = [{"path": str(tmp_path / "changed")}]
        await app.mutate_configuration(
            relative_path="projects/remote.yaml", request=ResourceMutationRequest(content=json.dumps(project))
        )
        with pytest.raises(ThreadError, match="preview again"):
            await app.apply_project_defaults(
                thread_id=thread.thread_id,
                environments_only=True,
                request=ProjectDefaultsApply(
                    expected_version=preview.expected_version,
                    defaults_digest=preview.defaults_digest,
                ),
            )


async def test_explicit_null_default_does_not_reinherit_project_mount(tmp_path):
    root = _write_configuration(tmp_path)
    document = json.loads(_project(tmp_path / "workspace", {"default_environment": "workspace-2"}))
    document["roots"].append({"path": str(tmp_path / "extra")})
    (tmp_path / "projects/main.yaml").write_text(json.dumps(document))
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        inherited = await app.preview_thread_configuration()
        assert inherited.default_environment == "workspace-2"
        exact = await app.create_thread(
            defaults=NewThreadDefaults(
                local_roots=(str(tmp_path / "workspace"),),
                environment_bindings=(),
                default_environment=None,
            )
        )
        assert exact.configuration.default_environment is None
        assert exact.configuration.local_roots == (str(tmp_path / "workspace"),)


async def test_cross_directory_resume_uses_saved_agent_without_creation_default(tmp_path):
    from a13n_harness_ui.cli import CliRequest
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.interactive.rendering import Status

    root = _write_configuration(tmp_path)
    root.write_text('schema_version: "1"\n')
    directory = tmp_path / "other"
    directory.mkdir()
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        thread = await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-main", agent_id="agent-assistant")
        )
        backend = SessionBackend(app, CliRequest(), directory, Status())
        await backend.resume(thread.thread_id)
        selected = (await app.get_thread(thread.thread_id)).thread.configuration
        assert selected.agent_source == thread.configuration.agent_source
        assert selected.local_roots == (str(directory),)
        assert selected.default_environment == "workspace"
