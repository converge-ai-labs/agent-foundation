from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration.models import ProjectResource
from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.thread_capability import ThreadCollaborationCapability
from a13n_harness_ui.thread_service import RootThreadDefaults, resolve_thread_configuration
from pydantic import ValidationError

from .test_app import _settings, _write_configuration
from .test_device_configuration import device
from .test_project_defaults import _source_tree

pytestmark = pytest.mark.anyio


def project_schema(publish):
    capability = ThreadCollaborationCapability(
        controller=SimpleNamespace(create_project=publish, read_project_memory=None, bot=None),
        source_thread_id=BOT_THREAD_ID,
    )
    return capability.get_toolset().tools["create_project"].function_schema


async def call(schema, arguments):
    context = SimpleNamespace(deps=SimpleNamespace(thread_id=BOT_THREAD_ID))
    return await schema.call(schema.validator.validate_python(arguments), context)


@pytest.fixture
def project_tool():
    published: list[ProjectResource] = []

    async def publish(*, project):
        published.append(project)
        return project

    return project_schema(publish), published


def test_project_creation_schema_contains_only_work_locations_and_creation_choices(project_tool):
    schema, _ = project_tool
    assert set(schema.json_schema["properties"]) == {
        "roots",
        "name",
        "remote_roots",
        "default_environment",
        "environment_profile_id",
    }
    remote = schema.json_schema["$defs"]["RemoteProjectRoot"]
    assert set(remote["properties"]) == {"device_id", "working_directory"}
    assert remote["additionalProperties"] is False
    assert set(schema.json_schema["$defs"]) == {"RemoteProjectRoot", "ResourceId"}
    # Keep Session/permission policy graphs out of this frequently supplied tool.
    assert len(json.dumps(schema.json_schema, separators=(",", ":")).encode()) < 2000


@pytest.mark.parametrize("name", [None, "Chosen name"])
async def test_local_creation_generates_metadata_and_preserves_omitted_defaults(project_tool, tmp_path, name):
    schema, published = project_tool
    roots = [str(tmp_path / "app"), str(tmp_path / "notes")]
    result = await call(schema, {"roots": roots, "name": name})
    assert result["status"] == "created"
    project = published[0]
    assert project.id.startswith("project-")
    assert project.name == (name or "app")
    assert project.schema_version == "1" and project.kind == "project" and project.position == 0
    assert [root.path for root in project.roots] == roots
    assert project.defaults.model_dump() == {}
    assert "defaults" not in result["project"]
    assert result["project"]["id"] == project.id
    await call(schema, {"roots": roots})
    assert published[1].id != project.id


@pytest.mark.parametrize(
    "local,selected,expected",
    [
        (False, None, "remote-1"),
        (False, "remote-2", "remote-2"),
        (True, None, "workspace"),
        (True, "remote-2", "remote-2"),
        (True, "workspace-2", "workspace-2"),
    ],
)
async def test_remote_and_mixed_creation_assigns_aliases_and_resolves_defaults(
    project_tool, tmp_path, local, selected, expected
):
    schema, published = project_tool
    roots = [str(tmp_path / "app"), str(tmp_path / "notes")] if local else []
    result = await call(
        schema,
        {
            "roots": roots,
            "remote_roots": [
                {"device_id": "device-build", "working_directory": "/C:/work"},
                {"device_id": "device-build", "working_directory": "/C:/notes"},
            ],
            "default_environment": selected,
        },
    )
    assert result["status"] == "created"
    project = published[0]
    assert project.name == ("app" if local else "work")
    assert project.defaults.default_environment == expected
    bindings = project.defaults.environment_bindings
    assert bindings is not None
    assert [(item.alias, item.device_id, item.working_directory) for item in bindings] == [
        ("remote-1", "device-build", "/C:/work"),
        ("remote-2", "device-build", "/C:/notes"),
    ]
    assert all(item.session.model_dump(exclude_none=True) == {} for item in bindings)
    assert all(item.expected_boundary is None for item in bindings)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"roots": ["relative/path"]},
        {"roots": ["/work", "/work"]},
        {"roots": ["/work"], "default_environment": "missing"},
        {"roots": ["/work"], "default_environment": ""},
        {"remote_roots": [{"device_id": "agent-wrong-kind", "working_directory": "/work"}]},
    ],
)
async def test_invalid_creation_does_not_publish_a_configuration(project_tool, arguments):
    schema, published = project_tool
    result = await call(schema, arguments)
    assert result["ok"] is False
    assert result["error"]["code"] in {"project_roots_required", "project_creation_failed"}
    assert published == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"project": {"schema_version": "1", "kind": "project", "id": "project-authored"}},
        {"roots": ["/work"], "defaults": {"mcp_servers": []}},
        {"roots": ["/work"], "name": ""},
        {"roots": ["/work"] * 65},
        {"remote_roots": [{"device_id": "device-build", "working_directory": "relative"}]},
        {"remote_roots": [{"device_id": "device-build", "working_directory": "/work", "session": {}}]},
    ],
)
async def test_configuration_envelopes_and_policy_overrides_are_not_tool_inputs(project_tool, arguments):
    schema, published = project_tool
    with pytest.raises(ValidationError):
        await call(schema, arguments)
    assert published == []


async def test_project_creation_remains_bot_only(project_tool):
    _, published = project_tool
    context = SimpleNamespace(deps=SimpleNamespace(thread_id="thread-ordinary"))
    capability = ThreadCollaborationCapability(
        controller=SimpleNamespace(create_project=lambda **kwargs: None), source_thread_id="thread-ordinary"
    )
    assert "create_project" not in capability.get_toolset().tools
    with pytest.raises(ThreadError, match="Only Bot"):
        await capability.create_project(context, roots=["/work"])
    assert published == []


async def test_created_project_does_not_override_global_plugin_and_mcp_defaults(project_tool, tmp_path):
    schema, published = project_tool
    source = await load_harness_ui_configuration(_source_tree(tmp_path, {}))
    await call(schema, {"roots": [str(tmp_path / "new-app")]})
    project = published[0]
    source = source.model_copy(update={"projects": {**source.projects, project.id: project}})
    configuration = resolve_thread_configuration(source, RootThreadDefaults(project_id=project.id))
    assert configuration.harness_plugin_ids == ("plugin-global",)
    assert configuration.environment_run_extension_ids == ("extension-global",)
    assert configuration.mcp_server_ids == ("mcp-global",)


@pytest.mark.usefixtures("migrated_databases")
@pytest.mark.parametrize("remote", [False, True])
async def test_created_project_is_accepted_without_starting_work_and_inherits_creation_defaults(tmp_path, remote):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "  environment_profile: environment-sandbox\n  mcp_servers: [mcp-global]\n")
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp/global.yaml").write_text(
        'schema_version: "1"\nkind: mcp_server\nid: mcp-global\nname: Global\ntransport: {command: unused}\n'
    )
    (tmp_path / "devices").mkdir()
    (tmp_path / "devices/build.yaml").write_text(device().model_dump_json())
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        schema = project_schema(app.create_project)
        arguments = (
            {"remote_roots": [{"device_id": "device-build", "working_directory": "/C:/work"}]}
            if remote
            else {"roots": [str(tmp_path / "new-app")]}
        )
        result = await call(schema, arguments)
        assert result["status"] == "created"
        project_id = result["project"]["id"]
        source = await app.current_configuration()
        assert source is not None
        project = source.projects[project_id]
        assert (tmp_path / f"projects/{project_id}.yaml").exists()
        assert (await app.list_threads()).total == 0
        assert project.defaults.agent is None
        assert project.defaults.harness_plugins is None
        assert project.defaults.environment_run_extensions is None
        assert project.defaults.mcp_servers is None
        configuration = resolve_thread_configuration(source, RootThreadDefaults(project_id=project_id))
        assert configuration.agent_source.id == "agent-assistant"
        assert configuration.environment_profile_id == "environment-sandbox"
        assert configuration.mcp_server_ids == ("mcp-global",)
        assert configuration.environment_bindings == (project.defaults.environment_bindings or ())
        assert configuration.local_roots == (() if remote else (str(tmp_path / "new-app"),))
        explicit = await call(
            schema, {"roots": [str(tmp_path / "other")], "environment_profile_id": "environment-native"}
        )
        assert explicit["status"] == "created"
        assert explicit["project"]["defaults"]["environment_profile"] == "environment-native"
        before = set((await app.current_configuration()).projects)
        for invalid in (
            {"remote_roots": [{"device_id": "device-missing", "working_directory": "/work"}]},
            {"roots": [str(tmp_path / "other")], "environment_profile_id": "environment-missing"},
        ):
            failure = await call(schema, invalid)
            assert failure["ok"] is False
            assert set((await app.current_configuration()).projects) == before
