from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import a13n_ui.composition.reconstruction as reconstruction_module
import pytest
import yaml
from a13n_harness import ContextualMCP
from a13n_harness.environment.local.binding import _DirectLocalFilePolicy
from a13n_harness.environment.local.files import LocalFileOperator
from a13n_ui.composition.reconstruction import SnapshotSkillMaterializer, _PackageFile
from a13n_ui.configuration import (
    ConfigurationSettings,
    DefinitionRootSettings,
    LocalDirectorySettings,
    canonical_digest,
)
from a13n_ui.configuration.models import MCPSelection
from a13n_ui.errors import CompositionError, ConfigurationError
from a13n_ui.host import open_agent_ui_host
from a13n_ui.settings import AgentUiSettings, StorageSettings

pytestmark = pytest.mark.anyio


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=True))


def _settings(data_root: Path, definitions: Path, workspace: Path) -> AgentUiSettings:
    return AgentUiSettings(
        storage=StorageSettings(data_root=data_root),
        configuration=ConfigurationSettings(
            definition_roots=(
                DefinitionRootSettings(
                    root_id="root-user",
                    path=definitions,
                    writable=True,
                ),
            ),
            local_directories=(
                LocalDirectorySettings(
                    directory_id="directory-workspace",
                    path=workspace,
                ),
            ),
            model_adapter_keys=("a13n.pydantic-ai",),
            orphan_retention_seconds=60,
        ),
    )


def _write_composition(definitions: Path, *, system_prompt: str = "Be concise.") -> None:
    _write_yaml(
        definitions / "models/model-main.yaml",
        {
            "schema_version": "1",
            "model_id": "model-main",
            "display_name": "Main Model",
            "provider_key": "a13n.pydantic-ai",
            "model_name": "test-v1",
            "settings": {"temperature": 0},
        },
    )
    _write_yaml(
        definitions / "prompts/prompt-main.yaml",
        {
            "schema_version": "1",
            "prompt_id": "prompt-main",
            "display_name": "Main Prompt",
            "system_prompt_blocks": [{"content": system_prompt}],
        },
    )
    _write_yaml(
        definitions / "agents/agent-main.yaml",
        {
            "schema_version": "1",
            "agent_id": "agent-main",
            "display_name": "Main Agent",
            "model": {"kind": "model", "resource_id": "model-main"},
            "prompt": {"kind": "prompt", "resource_id": "prompt-main"},
            "async_subagents": {"tools": "disabled"},
        },
    )
    _write_yaml(
        definitions / "environments/environment-main.yaml",
        {
            "schema_version": "1",
            "environment_id": "environment-main",
            "display_name": "Main Environment",
            "bindings": [
                {
                    "binding_name": "binding-main",
                    "model_alias": "workspace",
                    "provider_key": "a13n.direct-local",
                    "provider_schema_version": "1",
                    "provider_parameters": {
                        "environment_id": "local-main",
                        "root": {
                            "directory_id": "directory-workspace",
                            "read_only": False,
                        },
                    },
                    "permission_ceiling": ["files"],
                    "required": True,
                },
            ],
            "default_binding": "binding-main",
            "lifecycle": {
                "provision": "on_first_run",
                "idle": "keep_running",
                "release": "retain",
            },
        },
    )


async def test_snapshots_reconstruct_and_survive_reload_and_restart(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        generation_one = await application.current_configuration()
        assert generation_one is not None
        agent_reference = await application.resolve_agent_snapshot("agent-main")
        environment_reference = await application.resolve_environment_snapshot("environment-main")
        agent = await application.agent_snapshot(agent_reference)
        environment = await application.environment_snapshot(environment_reference)
        compatibility = await application.validate_agent_environment(
            agent_reference,
            environment_reference,
        )
        await application.validate_agent_executable(agent_reference)
        executable = await application._composition.executable(agent_reference)

        assert executable.definition.agent.system_prompt == ["Be concise."]
        assert executable.definition.agent.instructions is None
        assert agent.root_agent.resource_id == "agent-main"
        assert agent.adapter_locks[0].dependency_kind == "model_adapter"
        assert agent.adapter_locks[0].key == "a13n.pydantic-ai"
        assert agent.adapter_locks[0].distribution_name == "a13n-ui"
        assert agent.adapter_locks[0].distribution_version
        assert environment.environment_revision.resource_id == "environment-main"
        assert environment.bindings[0].normalized_parameters["root"] == {
            "path": str(workspace),
            "read_only": False,
        }
        assert environment.bindings[0].lifecycle_capabilities.model_dump(mode="json") == {
            "pause_modes": [],
            "resource_allocation": "single_from_spec",
            "attachment_concurrency": "shared",
        }
        assert compatibility.agent_snapshot_digest == agent.logical_agent_digest

        _write_composition(definitions, system_prompt="Be precise.")
        generation_two = await application.reload_configuration()
        changed_reference = await application.resolve_agent_snapshot("agent-main")
        assert generation_two.generation_id != generation_one.generation_id
        assert changed_reference.logical_digest != agent_reference.logical_digest
        assert await application.agent_snapshot(agent_reference) == agent
        exact_reference = await application.resolve_agent_snapshot(
            "agent-main",
            generation_id=generation_one.generation_id,
        )
        assert exact_reference == agent_reference

    async with open_agent_ui_host(settings) as restarted:
        assert await restarted.agent_snapshot(agent_reference) == agent
        assert await restarted.environment_snapshot(environment_reference) == environment
        await restarted.validate_agent_executable(agent_reference)


async def test_reconstruction_rejects_a_changed_model_adapter_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        agent_reference = await application.resolve_agent_snapshot("agent-main")
        monkeypatch.setattr(
            reconstruction_module,
            "model_adapter_registration",
            lambda _key: None,
        )
        with pytest.raises(CompositionError) as error:
            await application.validate_agent_executable(agent_reference)

    assert error.value.code == "model_adapter_lock_mismatch"


async def test_subagent_identity_policy_survives_snapshot_reconstruction(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    _write_yaml(
        definitions / "agents/agent-child.yaml",
        {
            "schema_version": "1",
            "agent_id": "agent-child",
            "display_name": "Child Agent",
            "model": {"kind": "model", "resource_id": "model-main"},
            "prompt": {"kind": "prompt", "resource_id": "prompt-main"},
            "async_subagents": {"tools": "disabled"},
        },
    )
    agent_path = definitions / "agents/agent-main.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["subagents"] = [
        {
            "name": "child-worker",
            "description": "Run child work.",
            "agent": {"kind": "agent", "resource_id": "agent-child"},
            "identity": {"inherit_agent_id": True},
        }
    ]
    _write_yaml(agent_path, agent)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        reference = await application.resolve_agent_snapshot("agent-main")
        snapshot = await application.agent_snapshot(reference)
        executable = await application._composition.executable(reference)

    root = next(node for node in snapshot.resolved_agents if node.agent_id == "agent-main")
    assert root.subagents[0].identity.inherit_agent_id is True
    assert executable.definition.subagents[0].identity.inherit_agent_id is True

    legacy_payload = snapshot.model_dump(mode="python")
    for node in legacy_payload["resolved_agents"]:
        for edge in node["subagents"]:
            edge.pop("identity")
    legacy_behavior = {
        key: value
        for key, value in legacy_payload.items()
        if key not in {"logical_agent_digest", "generation_id", "catalog_digest"}
    }
    legacy_payload["logical_agent_digest"] = canonical_digest(legacy_behavior)
    restored = type(snapshot).model_validate(legacy_payload)
    legacy_root = next(node for node in restored.resolved_agents if node.agent_id == "agent-main")
    assert legacy_root.subagents[0].identity.inherit_agent_id is False


async def test_mcp_selection_survives_snapshot_reconstruction(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    agent_path = definitions / "agents/agent-main.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["capabilities"] = [
        {
            "key": "a13n.mcp",
            "schema_version": "1",
            "id": "mcp-main",
            "url": "https://mcp.example.com/mcp",
            "execution": "auto",
            "allowed_tools": ["search"],
            "description": "Context-aware search.",
            "defer_loading": True,
            "context_headers": {
                "X-Agent": {"source": "identity.agent_id", "required": True},
                "X-Payload": {
                    "source": "context.metadata.payload",
                    "required": False,
                },
            },
        },
        {
            "key": "a13n.mcp",
            "schema_version": "1",
            "id": "mcp-secondary",
            "url": "https://secondary.example.com/mcp",
            "execution": "native",
            "allowed_tools": None,
            "description": None,
            "defer_loading": False,
            "context_headers": {},
        },
    ]
    _write_yaml(agent_path, agent)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        reference = await application.resolve_agent_snapshot("agent-main")
        snapshot = await application.agent_snapshot(reference)
        executable = await application._composition.executable(reference)

    root = next(node for node in snapshot.resolved_agents if node.agent_id == "agent-main")
    assert [selection.model_dump(mode="json") for selection in root.capabilities] == agent["capabilities"]
    capabilities = [item for item in executable.definition.capabilities if isinstance(item, ContextualMCP)]
    assert [item.id for item in capabilities] == ["mcp-main", "mcp-secondary"]
    assert capabilities[0].url == "https://mcp.example.com/mcp"
    assert capabilities[0].description == "Context-aware search."
    assert capabilities[0].defer_loading is True
    assert capabilities[1].url == "https://secondary.example.com/mcp"


def test_mcp_selection_preserves_explicit_http_url_components() -> None:
    url = "https://user:pass@mcp.example.com/mcp?signature=value#route"

    selection = MCPSelection(
        key="a13n.mcp",
        id="mcp-main",
        url=url,
        context_headers={},
    )

    assert selection.url == url


async def test_unrelated_reload_reuses_the_logical_agent_snapshot(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        original = await application.resolve_agent_snapshot("agent-main")
        environment_path = definitions / "environments/environment-main.yaml"
        environment = yaml.safe_load(environment_path.read_text())
        environment["display_name"] = "Renamed Environment"
        _write_yaml(environment_path, environment)
        await application.reload_configuration()
        unchanged = await application.resolve_agent_snapshot("agent-main")

    assert unchanged == original


async def test_skill_reconstruction_uses_the_selected_environment_alias(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    package = definitions / "managed-skills/skill-demo"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demonstration skill\n---\n\nUse the demo workflow.\n"
    )
    _write_yaml(
        definitions / "skills/skill-demo.yaml",
        {
            "schema_version": "1",
            "skill_id": "skill-demo",
            "display_name": "Demo Skill",
            "skill_name": "demo",
            "description": "Demonstration skill",
            "package": {
                "root_id": "root-user",
                "relative_path": "managed-skills/skill-demo",
            },
            "compatibility": {"harness_skill_contract": "1"},
        },
    )
    agent_path = definitions / "agents/agent-main.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["skills"] = {
        "available": [{"kind": "skill", "resource_id": "skill-demo"}],
        "materialization_binding": "binding-main",
        "default_selection": {"mode": "exact", "names": ["demo"]},
    }
    agent["environment"] = {
        "bindings": [
            {
                "binding_name": "binding-main",
                "required_operations": ["files"],
            },
        ],
    }
    _write_yaml(agent_path, agent)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        agent_reference = await application.resolve_agent_snapshot("agent-main")
        environment_reference = await application.resolve_environment_snapshot("environment-main")
        with pytest.raises(CompositionError) as missing_environment:
            await application.validate_agent_executable(agent_reference)
        await application.validate_agent_executable(
            agent_reference,
            environment_reference,
        )

    assert missing_environment.value.code == "agent_environment_required"


@pytest.mark.parametrize("relation", ["equal", "ancestor", "descendant"])
async def test_environment_snapshot_rejects_data_root_overlap(
    tmp_path: Path,
    relation: str,
) -> None:
    definitions = tmp_path / "definitions"
    data_root = tmp_path / "data"
    if relation == "equal":
        workspace = data_root
    elif relation == "ancestor":
        workspace = tmp_path
    else:
        workspace = data_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _write_composition(definitions)
    settings = _settings(data_root, definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        with pytest.raises(CompositionError) as overlap:
            await application.resolve_environment_snapshot("environment-main")

    assert overlap.value.code == "environment_data_root_overlap"


async def test_environment_snapshot_resolves_symlinks_before_overlap_check(
    tmp_path: Path,
) -> None:
    real_root = tmp_path / "real-root"
    data_root = real_root / "data"
    definitions = tmp_path / "definitions"
    real_root.mkdir()
    workspace_link = tmp_path / "workspace-link"
    try:
        workspace_link.symlink_to(real_root, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this Windows configuration")
    _write_composition(definitions)
    settings = _settings(data_root, definitions, workspace_link)

    async with open_agent_ui_host(settings) as application:
        with pytest.raises(CompositionError) as overlap:
            await application.resolve_environment_snapshot("environment-main")

    assert overlap.value.code == "environment_data_root_overlap"


async def test_compatibility_rejects_a_missing_required_binding(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    agent_path = definitions / "agents/agent-main.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["environment"] = {
        "bindings": [
            {
                "binding_name": "binding-missing",
                "required_operations": ["files"],
            },
        ],
    }
    _write_yaml(agent_path, agent)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        agent_reference = await application.resolve_agent_snapshot("agent-main")
        environment_reference = await application.resolve_environment_snapshot("environment-main")
        with pytest.raises(CompositionError) as incompatible:
            await application.validate_agent_environment(
                agent_reference,
                environment_reference,
            )

    assert incompatible.value.code == "agent_environment_incompatible"


async def test_reload_rejects_unsupported_environment_lifecycle_and_async_tools(
    tmp_path: Path,
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        accepted = await application.current_configuration()
        assert accepted is not None

        environment_path = definitions / "environments/environment-main.yaml"
        environment = yaml.safe_load(environment_path.read_text())
        environment["lifecycle"]["idle"] = "pause_full"
        _write_yaml(environment_path, environment)
        with pytest.raises(ConfigurationError) as lifecycle_error:
            await application.reload_configuration()
        assert lifecycle_error.value.code == "environment_lifecycle_incompatible"
        assert await application.current_configuration() == accepted

        _write_composition(definitions)
        agent_path = definitions / "agents/agent-main.yaml"
        agent = yaml.safe_load(agent_path.read_text())
        agent["async_subagents"] = {"tools": "standard"}
        _write_yaml(agent_path, agent)
        with pytest.raises(ConfigurationError) as capability_error:
            await application.reload_configuration()
        assert capability_error.value.code == "capability_schema_unavailable"
        assert await application.current_configuration() == accepted


@pytest.mark.parametrize(
    ("mode", "compatible"),
    [
        ("dedicated", False),
        ("shared_root", True),
        ("serialized_root", True),
    ],
)
async def test_child_environment_policy_uses_provider_lifecycle_capabilities(
    tmp_path: Path,
    mode: str,
    compatible: bool,
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    _write_yaml(
        definitions / "agents/agent-child.yaml",
        {
            "schema_version": "1",
            "agent_id": "agent-child",
            "display_name": "Child Agent",
            "model": {"kind": "model", "resource_id": "model-main"},
            "prompt": {"kind": "prompt", "resource_id": "prompt-main"},
            "async_subagents": {"tools": "disabled"},
        },
    )
    agent_path = definitions / "agents/agent-main.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["capabilities"] = [{"key": "a13n.user-interaction", "schema_version": "1"}]
    agent["subagents"] = [
        {
            "name": "child-worker",
            "description": "Run child work.",
            "agent": {"kind": "agent", "resource_id": "agent-child"},
            "environment": {"mode": mode, "bindings": ["binding-main"]},
        }
    ]
    _write_yaml(agent_path, agent)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        agent_reference = await application.resolve_agent_snapshot("agent-main")
        environment_reference = await application.resolve_environment_snapshot("environment-main")
        if compatible:
            result = await application.validate_agent_environment(
                agent_reference,
                environment_reference,
            )
            assert result.compatible is True
            await application.validate_agent_executable(
                agent_reference,
                environment_reference,
            )
        else:
            with pytest.raises(CompositionError) as error:
                await application.validate_agent_environment(
                    agent_reference,
                    environment_reference,
                )
            assert error.value.code == "agent_environment_incompatible"


async def test_skill_materializer_replaces_stale_tree_and_handles_concurrent_publish(
    tmp_path: Path,
) -> None:
    target = tmp_path / "managed"
    target.mkdir()
    (target / "stale.txt").write_text("stale")
    content = b"---\nname: demo\ndescription: Demo\n---\n"
    package = _PackageFile(
        path="SKILL.md",
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )
    materializer = SnapshotSkillMaterializer(
        "materializer-demo",
        "/managed",
        {"demo": (package,)},
    )
    files = LocalFileOperator(
        root=tmp_path,
        read_only=False,
        policy=_DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        binding_id="binding-main",
        binding_version=1,
        generation="generation-1",
    )

    await asyncio.gather(
        materializer.materialize(files=files),
        materializer.materialize(files=files),
    )
    await materializer.materialize(files=files)

    assert (target / "demo" / "SKILL.md").read_bytes() == content
    assert not (target / "stale.txt").exists()
    assert not tuple(tmp_path.glob("managed.stage-*"))
