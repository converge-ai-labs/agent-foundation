from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from a13n_ui.application import open_application
from a13n_ui.configuration import (
    ConfigurationSettings,
    DefinitionRootSettings,
    LocalDirectorySettings,
)
from a13n_ui.errors import CompositionError
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
            model_adapter_keys=("a13n.test-model",),
            orphan_retention_seconds=60,
        ),
    )


def _write_composition(definitions: Path, *, instruction: str = "Be concise.") -> None:
    _write_yaml(
        definitions / "models/model-main.yaml",
        {
            "schema_version": "1",
            "model_id": "model-main",
            "display_name": "Main Model",
            "provider_key": "a13n.test-model",
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
            "instruction_blocks": [{"content": instruction}],
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

    async with open_application(settings) as application:
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

        assert agent.root_agent.resource_id == "agent-main"
        assert environment.environment_revision.resource_id == "environment-main"
        assert environment.bindings[0].normalized_parameters["root"] == {
            "path": str(workspace),
            "read_only": False,
        }
        assert compatibility.agent_snapshot_digest == agent.logical_agent_digest

        _write_composition(definitions, instruction="Be precise.")
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

    async with open_application(settings) as restarted:
        assert await restarted.agent_snapshot(agent_reference) == agent
        assert await restarted.environment_snapshot(environment_reference) == environment
        await restarted.validate_agent_executable(agent_reference)


async def test_unrelated_reload_reuses_the_logical_agent_snapshot(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_application(settings) as application:
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

    async with open_application(settings) as application:
        agent_reference = await application.resolve_agent_snapshot("agent-main")
        environment_reference = await application.resolve_environment_snapshot("environment-main")
        with pytest.raises(CompositionError) as missing_environment:
            await application.validate_agent_executable(agent_reference)
        await application.validate_agent_executable(
            agent_reference,
            environment_reference,
        )

    assert missing_environment.value.code == "agent_environment_required"


async def test_environment_snapshot_rejects_data_root_overlap(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    data_root = tmp_path / "data"
    data_root.mkdir()
    _write_composition(definitions)
    settings = _settings(data_root, definitions, data_root)

    async with open_application(settings) as application:
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

    async with open_application(settings) as application:
        agent_reference = await application.resolve_agent_snapshot("agent-main")
        environment_reference = await application.resolve_environment_snapshot("environment-main")
        with pytest.raises(CompositionError) as incompatible:
            await application.validate_agent_environment(
                agent_reference,
                environment_reference,
            )

    assert incompatible.value.code == "agent_environment_incompatible"
