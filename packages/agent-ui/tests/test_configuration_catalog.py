from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import a13n_ui.configuration.service as configuration_service_module
import pytest
import yaml
from a13n_ui.application import open_application
from a13n_ui.configuration import (
    AgentDefinitionDocument,
    ConfigurationSettings,
    DefinitionRootSettings,
    EnvironmentBindingDefinition,
    LocalDirectorySettings,
    LocalSkillDiscoverySettings,
    LocalSkillSourceDefinition,
    ModelDefinition,
    PluginInstanceDefinition,
    ResourceKind,
    ResourceRef,
    SourceTransactionEntry,
    SourceTransactionManifest,
    load_envd_runtime_manifest,
)
from a13n_ui.errors import ConfigurationError
from a13n_ui.settings import AgentUiSettings, StorageSettings
from a13n_ui.storage import ObjectKind
from a13n_ui.storage.database import transaction
from a13n_ui.storage.models import ImmutableObjectRecord
from anyio import fail_after, sleep
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


def _write_yaml(path: Path, value: object) -> bytes:
    content = yaml.safe_dump(value, allow_unicode=True, sort_keys=True).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def _full_settings(data_root: Path, definition_root: Path, discovery_root: Path) -> AgentUiSettings:
    return AgentUiSettings(
        storage=StorageSettings(data_root=data_root),
        configuration=ConfigurationSettings(
            definition_roots=(DefinitionRootSettings(root_id="root-user", path=definition_root, writable=True),),
            local_directories=(LocalDirectorySettings(directory_id="directory-skills", path=discovery_root),),
            model_adapter_keys=("a13n.test-model",),
            orphan_retention_seconds=60,
        ),
    )


def _write_complete_tree(root: Path) -> dict[str, bytes]:
    package = root / "managed-skills" / "skill-demo"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demonstration skill\n---\n\nUse the demo workflow.\n"
    )
    documents = {
        "models/model-main.yaml": {
            "schema_version": "1",
            "model_id": "model-main",
            "display_name": "Main Model",
            "provider_key": "a13n.test-model",
            "model_name": "test-v1",
            "endpoint": None,
            "settings": {"temperature": 0},
            "credential_ref": None,
        },
        "prompts/prompt-main.yaml": {
            "schema_version": "1",
            "prompt_id": "prompt-main",
            "display_name": "Main Prompt",
            "description": None,
            "instruction_blocks": [{"content": "Be concise.", "source": None}],
        },
        "skill-sources/skill-source-local.yaml": {
            "schema_version": "1",
            "skill_source_id": "skill-source-local",
            "display_name": "Local Skills",
            "directory_id": "directory-skills",
            "roots": ["/"],
            "required": True,
            "max_entries_per_root": 32,
        },
        "skills/skill-demo.yaml": {
            "schema_version": "1",
            "skill_id": "skill-demo",
            "display_name": "Demo Skill",
            "skill_name": "demo",
            "description": "Demonstration skill",
            "package": {"root_id": "root-user", "relative_path": "managed-skills/skill-demo"},
            "imported_from": None,
            "compatibility": {"harness_skill_contract": "1"},
        },
        "agents/agent-main.yaml": {
            "schema_version": "1",
            "agent_id": "agent-main",
            "display_name": "Main Agent",
            "description": None,
            "model": {"kind": "model", "resource_id": "model-main"},
            "prompt": {"kind": "prompt", "resource_id": "prompt-main"},
            "plugins": [],
            "skills": {"available": [{"kind": "skill", "resource_id": "skill-demo"}]},
            "capabilities": [],
            "environment": {},
            "subagents": [],
            "async_subagents": {},
            "output": {},
            "model_recovery": {},
        },
        "environments/environment-main.yaml": {
            "schema_version": "1",
            "environment_id": "environment-main",
            "display_name": "Empty Environment",
            "description": None,
            "bindings": [],
            "default_binding": None,
            "lifecycle": {
                "provision": "on_first_run",
                "idle": "keep_running",
                "release": "retain",
            },
        },
    }
    return {relative: _write_yaml(root / relative, value) for relative, value in documents.items()}


async def test_catalog_accepts_restarts_rejects_invalid_and_applies_batch(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    source = _write_complete_tree(definitions)
    settings = _full_settings(tmp_path / "data", definitions, discovery)

    async with open_application(settings) as application:
        generation_one = await application.current_configuration()
        assert generation_one is not None, await application.configuration_diagnostics()
        assert generation_one.generation_id == "config-1"
        assert len(generation_one.resources) == 6

        model_path = definitions / "models/model-main.yaml"
        model_path.write_text("schema_version: [invalid\n")
        with pytest.raises(ConfigurationError):
            await application.reload_configuration()
        assert await application.current_configuration() == generation_one
        assert (await application.configuration_diagnostics())[0].code == "configuration_document_invalid"
        model_path.write_bytes(source["models/model-main.yaml"])

        model = yaml.safe_load(source["models/model-main.yaml"])
        model["model_name"] = "test-v2"
        prompt = yaml.safe_load(source["prompts/prompt-main.yaml"])
        prompt["instruction_blocks"] = [{"content": "Be precise.", "source": None}]
        replacements = {
            "models/model-main.yaml": yaml.safe_dump(model, sort_keys=True).encode(),
            "prompts/prompt-main.yaml": yaml.safe_dump(prompt, sort_keys=True).encode(),
        }
        entries = tuple(
            SourceTransactionEntry(
                relative_path=path,
                operation="replace",
                content_digest=hashlib.sha256(content).hexdigest(),
            )
            for path, content in sorted(replacements.items())
        )
        generation_two = await application.apply_source_transaction(
            SourceTransactionManifest(
                schema_version="1",
                transaction_id="transaction-batch-1",
                root_id="root-user",
                base_catalog_digest=generation_one.catalog_digest,
                entries=entries,
            ),
            replacements,
        )
        assert generation_two.generation_id == "config-2"
        assert generation_two.catalog_digest != generation_one.catalog_digest

    async with open_application(settings) as restarted:
        current = await restarted.current_configuration()
        assert current is not None
        assert current.generation_id == "config-2"
        retained = await restarted.retained_configurations()
        assert [item.generation_id for item in retained] == ["config-2", "config-1"]
        model_ref = next(item for item in current.resources if item.kind is ResourceKind.model)
        model_revision = await restarted.configuration_resource(model_ref)
        assert model_revision.normalized_content["model_name"] == "test-v2"  # type: ignore[index]


async def test_skill_scan_import_survives_source_removal_and_restart(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    skill = discovery / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example imported workflow\n---\n\nFollow the example.\n"
    )
    _write_yaml(
        definitions / "skill-sources/skill-source-local.yaml",
        {
            "schema_version": "1",
            "skill_source_id": "skill-source-local",
            "display_name": "Local Skills",
            "directory_id": "directory-skills",
            "roots": ["/"],
            "required": True,
            "max_entries_per_root": 32,
        },
    )
    settings = _full_settings(tmp_path / "data", definitions, discovery)
    discovery_settings = LocalSkillDiscoverySettings(
        ordered_sources=(ResourceRef(kind=ResourceKind.skill_source, resource_id="skill-source-local"),),
        conflict="error",
        max_skills=32,
    )

    async with open_application(settings) as application:
        preview = await application.scan_skills(discovery_settings)
        assert [(item.name, item.description) for item in preview.items] == [("example", "Example imported workflow")]
        change = await application.preview_skill_import(
            scan_id=preview.scan_id,
            skill_name="example",
            skill_id="skill-example",
            display_name="Example Skill",
            target_root_id="root-user",
        )
        assert change.operation == "import"
        assert [(item.path, item.change) for item in change.files] == [("SKILL.md", "added")]
        imported = await application.accept_skill_import(change.change_id)
        skill_ref = next(item for item in imported.resources if item.resource_id == "skill-example")
        package_ref = await application._configuration._repository.skill_package_reference(skill_ref)
        assert (await application._store.read_object(package_ref)).object_kind is ObjectKind.skill_package

    (definitions / "skill-sources/skill-source-local.yaml").unlink()
    for path in sorted(discovery.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()

    async with open_application(settings) as restarted:
        generation = await restarted.current_configuration()
        assert generation is not None
        skill_ref = next(item for item in generation.resources if item.resource_id == "skill-example")
        revision = await restarted.configuration_resource(skill_ref)
        assert revision.normalized_content["skill_name"] == "example"  # type: ignore[index]


async def test_skill_import_rejects_a_stale_bound_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    skill = discovery / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example imported workflow\n---\n\nFollow the example.\n"
    )
    _write_yaml(
        definitions / "skill-sources/skill-source-local.yaml",
        {
            "schema_version": "1",
            "skill_source_id": "skill-source-local",
            "display_name": "Local Skills",
            "directory_id": "directory-skills",
            "roots": ["/"],
            "required": True,
            "max_entries_per_root": 32,
        },
    )
    settings = _full_settings(tmp_path / "data", definitions, discovery)
    selected = LocalSkillDiscoverySettings(
        ordered_sources=(ResourceRef(kind=ResourceKind.skill_source, resource_id="skill-source-local"),),
        conflict="error",
        max_skills=32,
    )

    async with open_application(settings) as application:
        preview = await application.scan_skills(selected)
        lease = application._configuration.skills._scans[preview.scan_id]
        original_select = lease.environment.select_files

        def stale_select(path: str):
            selection = original_select(path)
            return selection.__class__(
                logical_path=selection.logical_path,
                resolved_path=selection.resolved_path,
                observed_generation=f"{selection.observed_generation}-changed",
            )

        monkeypatch.setattr(lease.environment, "select_files", stale_select)
        with pytest.raises(ConfigurationError) as stale:
            await application.preview_skill_import(
                scan_id=preview.scan_id,
                skill_name="example",
                skill_id="skill-example",
                display_name="Example Skill",
                target_root_id="root-user",
            )
        assert stale.value.code == "skill_catalog_stale"
        assert not (definitions / "skills/skill-example.yaml").exists()


async def test_startup_removes_only_expired_unreferenced_objects(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    _write_complete_tree(definitions)
    settings = _full_settings(tmp_path / "data", definitions, discovery)

    async with open_application(settings) as application:
        recent = await application._store.publish_object(
            object_kind=ObjectKind.provider_state,
            object_schema_version="1",
            payload={"orphan": "recent"},
        )
        expired = await application._store.publish_object(
            object_kind=ObjectKind.provider_state,
            object_schema_version="1",
            payload={"orphan": "expired"},
        )
        generation = await application.current_configuration()
        assert generation is not None
        skill_ref = next(item for item in generation.resources if item.kind is ResourceKind.skill)
        package = await application._configuration._repository.skill_package_reference(skill_ref)
        async with transaction(application._store.database.sessions) as session:
            old = datetime(2000, 1, 1, tzinfo=UTC)
            expired_record = await session.get(ImmutableObjectRecord, expired.logical_digest)
            package_record = await session.get(ImmutableObjectRecord, package.logical_digest)
            assert expired_record is not None and package_record is not None
            expired_record.registered_at = old
            package_record.registered_at = old

    async with open_application(settings) as restarted:
        assert (await restarted._store.read_object(recent)).payload == {"orphan": "recent"}
        assert (await restarted._store.read_object(package)).object_kind is ObjectKind.skill_package
        async with transaction(restarted._store.database.sessions) as session:
            assert await session.get(ImmutableObjectRecord, expired.logical_digest) is None


async def test_invalid_source_transaction_does_not_select_its_overlay(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    source = _write_complete_tree(definitions)
    settings = _full_settings(tmp_path / "data", definitions, discovery)

    agent = yaml.safe_load(source["agents/agent-main.yaml"])
    agent["model"] = {"kind": "model", "resource_id": "model-missing"}
    content = yaml.safe_dump(agent, sort_keys=True).encode()
    manifest = SourceTransactionManifest(
        schema_version="1",
        transaction_id="transaction-invalid-1",
        root_id="root-user",
        base_catalog_digest="0" * 64,
        entries=(
            SourceTransactionEntry(
                relative_path="agents/agent-main.yaml",
                operation="replace",
                content_digest=hashlib.sha256(content).hexdigest(),
            ),
        ),
    )
    async with open_application(settings) as application:
        generation = await application.current_configuration()
        assert generation is not None
        manifest = manifest.model_copy(update={"base_catalog_digest": generation.catalog_digest})
        with pytest.raises(ConfigurationError) as rejected:
            await application.apply_source_transaction(
                manifest,
                {"agents/agent-main.yaml": content},
            )
        assert rejected.value.code == "configuration_reference_missing"
        assert await application.current_configuration() == generation
        assert not (definitions / ".a13n-transactions/active.json").exists()
        assert await application.reload_configuration() == generation


async def test_skill_package_content_changes_skill_revision(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    _write_complete_tree(definitions)
    settings = _full_settings(tmp_path / "data", definitions, discovery)

    async with open_application(settings) as application:
        first = await application.current_configuration()
        assert first is not None
        first_skill = next(item for item in first.resources if item.kind is ResourceKind.skill)
        first_package = await application._configuration._repository.skill_package_reference(first_skill)

        (definitions / "managed-skills/skill-demo/SKILL.md").write_text(
            "---\nname: demo\ndescription: Demonstration skill\n---\n\nUse the changed workflow.\n"
        )
        second = await application.reload_configuration()
        second_skill = next(item for item in second.resources if item.kind is ResourceKind.skill)
        second_package = await application._configuration._repository.skill_package_reference(second_skill)

        assert second.generation_id == "config-2"
        assert second_skill.content_digest != first_skill.content_digest
        assert second_package.logical_digest != first_package.logical_digest


async def test_startup_lkg_recovers_its_accepted_process_settings(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    _write_complete_tree(definitions)
    configured = _full_settings(tmp_path / "data", definitions, discovery)
    accepted_configuration = configured.configuration.model_copy(
        update={
            "skill_discovery": LocalSkillDiscoverySettings(
                ordered_sources=(
                    ResourceRef(
                        kind=ResourceKind.skill_source,
                        resource_id="skill-source-local",
                    ),
                )
            )
        }
    )
    process_path = tmp_path / "process-settings.yaml"
    _write_yaml(process_path, accepted_configuration.model_dump(mode="json"))
    settings = AgentUiSettings(
        storage=configured.storage,
        configuration=ConfigurationSettings(),
        process_settings_path=process_path,
    )

    async with open_application(settings) as application:
        accepted = await application.current_configuration()
        assert accepted is not None
        assert (await application.skill_source_statuses())[0].selected_ordinal == 0

    process_path.write_text("schema_version: [invalid\n")
    async with open_application(settings) as restarted:
        assert await restarted.current_configuration() == accepted
        assert restarted._configuration.settings == accepted_configuration
        status = (await restarted.skill_source_statuses())[0]
        assert status.selected_ordinal == 0
        assert status.directory_status == "available"


async def test_file_backed_process_settings_report_restart_requirement(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    _write_complete_tree(definitions)
    configured = _full_settings(tmp_path / "data", definitions, discovery)
    process_path = tmp_path / "process-settings.yaml"
    desired = configured.configuration.model_dump(mode="json")
    _write_yaml(process_path, desired)
    settings = AgentUiSettings(
        storage=configured.storage,
        configuration=ConfigurationSettings(),
        process_settings_path=process_path,
    )

    async with open_application(settings) as application:
        first = await application.current_configuration()
        assert first is not None
        assert first.restart_required is False

        desired["credential_backends"] = ["a13n.keyring"]
        _write_yaml(process_path, desired)
        second = await application.reload_configuration()
        assert second.restart_required is True
        assert second.process_settings_digest != first.process_settings_digest

    async with open_application(settings) as restarted:
        current = await restarted.current_configuration()
        assert current is not None
        assert current.restart_required is False

        desired["envd_executable_override"] = "agent-envd"
        _write_yaml(process_path, desired)
        with pytest.raises(ConfigurationError) as invalid:
            await restarted.reload_configuration()
        assert invalid.value.code == "process_settings_invalid"
        assert await restarted.current_configuration() == current


def test_literal_credentials_are_rejected_from_generic_resource_configuration() -> None:
    with pytest.raises(ValidationError):
        PluginInstanceDefinition.model_validate(
            {
                "schema_version": "1",
                "plugin_resource_id": "plugin-example",
                "display_name": "Example Plugin",
                "plugin_key": "a13n.example",
                "plugin_id": "plugin-instance",
                "enabled": True,
                "configuration": {"clientSecret": "literal-secret"},
            },
            strict=True,
        )
    with pytest.raises(ValidationError):
        EnvironmentBindingDefinition.model_validate(
            {
                "binding_name": "binding-main",
                "model_alias": "workspace",
                "provider_key": "a13n.direct-local",
                "provider_schema_version": "1",
                "provider_parameters": {"accessToken": "literal-secret"},
                "permission_ceiling": [],
                "required": True,
            },
            strict=True,
        )
    with pytest.raises(ValidationError):
        AgentDefinitionDocument.model_validate(
            {
                "schema_version": "1",
                "agent_id": "agent-main",
                "display_name": "Main Agent",
                "model": {"kind": "model", "resource_id": "model-main"},
                "prompt": {"kind": "prompt", "resource_id": "prompt-main"},
                "capabilities": [{"apiKey": "literal-secret"}],
            },
            strict=True,
        )


def test_literal_credentials_are_rejected_from_model_content() -> None:
    base = {
        "schema_version": "1",
        "model_id": "model-main",
        "display_name": "Main Model",
        "provider_key": "a13n.test-model",
        "model_name": "test-v1",
        "endpoint": None,
        "settings": {},
        "credential_ref": None,
    }
    with pytest.raises(ValidationError):
        ModelDefinition.model_validate(
            {**base, "settings": {"headers": {"value": "Bearer literal-secret"}}},
            strict=True,
        )
    with pytest.raises(ValidationError):
        ModelDefinition.model_validate(
            {**base, "endpoint": "https://example.test/model?api%5Fkey=literal-secret"},
            strict=True,
        )
    with pytest.raises(ValidationError):
        ModelDefinition.model_validate(
            {**base, "settings": {"clientSecret": "literal-secret"}},
            strict=True,
        )


async def test_managed_skill_frontmatter_must_match_definition(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    _write_complete_tree(definitions)
    settings = _full_settings(tmp_path / "data", definitions, discovery)

    async with open_application(settings) as application:
        current = await application.current_configuration()
        assert current is not None
        (definitions / "managed-skills/skill-demo/SKILL.md").write_text(
            "---\nname: renamed\ndescription: Demonstration skill\n---\n\nUse the demo workflow.\n"
        )
        with pytest.raises(ConfigurationError) as invalid:
            await application.reload_configuration()
        assert invalid.value.code == "skill_package_invalid"
        assert await application.current_configuration() == current


async def test_oversized_resolved_prompt_keeps_last_known_good(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    source = _write_complete_tree(definitions)
    prompt = yaml.safe_load(source["prompts/prompt-main.yaml"])
    prompt["instruction_blocks"] = [{"content": None, "source": "prompt-main.md"}]
    _write_yaml(definitions / "prompts/prompt-main.yaml", prompt)
    (definitions / "prompts/prompt-main.md").write_text("Valid prompt.\n")
    settings = _full_settings(tmp_path / "data", definitions, discovery)

    async with open_application(settings) as application:
        current = await application.current_configuration()
        assert current is not None
        (definitions / "prompts/prompt-main.md").write_text("x" * (1024 * 1024 + 1))
        with pytest.raises(ConfigurationError) as invalid:
            await application.reload_configuration()
        assert invalid.value.code == "configuration_document_invalid"
        assert await application.current_configuration() == current


async def test_malformed_managed_skill_keeps_last_known_good(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    _write_complete_tree(definitions)
    settings = _full_settings(tmp_path / "data", definitions, discovery)

    async with open_application(settings) as application:
        current = await application.current_configuration()
        assert current is not None
        (definitions / "managed-skills/skill-demo/SKILL.md").write_text("No frontmatter.\n")
        with pytest.raises(ConfigurationError) as invalid:
            await application.reload_configuration()
        assert invalid.value.code == "skill_catalog_invalid"
        assert await application.current_configuration() == current


async def test_refresh_requires_recorded_source_and_skill_name(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    skill = discovery / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: example\ndescription: Example workflow\n---\n\nFollow it.\n")
    for source_id in ("skill-source-first", "skill-source-second"):
        _write_yaml(
            definitions / f"skill-sources/{source_id}.yaml",
            {
                "schema_version": "1",
                "skill_source_id": source_id,
                "display_name": source_id,
                "directory_id": "directory-skills",
                "roots": ["/"],
                "required": True,
                "max_entries_per_root": 32,
            },
        )
    settings = _full_settings(tmp_path / "data", definitions, discovery)

    async with open_application(settings) as application:
        first = await application.scan_skill_source("skill-source-first")
        imported = await application.preview_skill_import(
            scan_id=first.scan_id,
            skill_name="example",
            skill_id="skill-example",
            display_name="Example Skill",
            target_root_id="root-user",
        )
        await application.accept_skill_import(imported.change_id)

        second = await application.scan_skill_source("skill-source-second")
        with pytest.raises(ConfigurationError) as mismatch:
            await application.preview_skill_refresh(
                scan_id=second.scan_id,
                skill_name="example",
                skill_id="skill-example",
            )
        assert mismatch.value.code == "skill_refresh_source_mismatch"


async def test_refresh_replaces_removed_skill_package_files(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    skill = discovery / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: example\ndescription: Example workflow\n---\n\nFollow it.\n")
    (skill / "old.txt").write_text("old")
    _write_yaml(
        definitions / "skill-sources/skill-source-local.yaml",
        {
            "schema_version": "1",
            "skill_source_id": "skill-source-local",
            "display_name": "Local Skills",
            "directory_id": "directory-skills",
            "roots": ["/"],
            "required": True,
            "max_entries_per_root": 32,
        },
    )
    settings = _full_settings(tmp_path / "data", definitions, discovery)
    selected = LocalSkillDiscoverySettings(
        ordered_sources=(ResourceRef(kind=ResourceKind.skill_source, resource_id="skill-source-local"),),
    )

    async with open_application(settings) as application:
        first_scan = await application.scan_skills(selected)
        first_change = await application.preview_skill_import(
            scan_id=first_scan.scan_id,
            skill_name="example",
            skill_id="skill-example",
            display_name="Example Skill",
            target_root_id="root-user",
        )
        imported = await application.accept_skill_import(first_change.change_id)
        first_ref = next(item for item in imported.resources if item.resource_id == "skill-example")
        first_package = await application._configuration._repository.skill_package_reference(first_ref)

        (skill / "old.txt").unlink()
        (skill / "new.txt").write_text("new")
        second_scan = await application.scan_skills(selected)
        refresh = await application.preview_skill_refresh(
            scan_id=second_scan.scan_id,
            skill_name="example",
            skill_id="skill-example",
        )
        assert refresh.operation == "refresh"
        assert [(item.path, item.change) for item in refresh.files] == [
            ("new.txt", "added"),
            ("old.txt", "removed"),
        ]
        assert refresh.definition_changed is True
        assert await application.current_configuration() == imported
        refreshed = await application.accept_skill_refresh(refresh.change_id)
        second_ref = next(item for item in refreshed.resources if item.resource_id == "skill-example")
        payload = (
            await application._store.read_object(
                await application._configuration._repository.skill_package_reference(second_ref)
            )
        ).payload
        assert isinstance(payload, dict)
        files = payload["files"]
        assert isinstance(files, list)
        assert {item["path"] for item in files if isinstance(item, dict)} == {"SKILL.md", "new.txt"}
        assert (await application._store.read_object(first_package)).object_kind is ObjectKind.skill_package


async def test_source_transaction_rolls_back_when_sources_race_manifest_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    source = _write_complete_tree(definitions)
    settings = _full_settings(tmp_path / "data", definitions, discovery)

    async with open_application(settings) as application:
        current = await application.current_configuration()
        assert current is not None
        prompt = yaml.safe_load(source["prompts/prompt-main.yaml"])
        prompt["instruction_blocks"] = [{"content": "Transaction edit.", "source": None}]
        content = yaml.safe_dump(prompt, sort_keys=True).encode()
        manifest = SourceTransactionManifest(
            schema_version="1",
            transaction_id="transaction-selection-race",
            root_id="root-user",
            base_catalog_digest=current.catalog_digest,
            entries=(
                SourceTransactionEntry(
                    relative_path="prompts/prompt-main.yaml",
                    operation="replace",
                    content_digest=hashlib.sha256(content).hexdigest(),
                ),
            ),
        )
        original_commit = configuration_service_module.commit_source_transaction

        async def commit_then_race(prepared) -> None:
            await original_commit(prepared)
            model = yaml.safe_load(source["models/model-main.yaml"])
            model["model_name"] = "external-race"
            _write_yaml(definitions / "models/model-main.yaml", model)

        monkeypatch.setattr(
            configuration_service_module,
            "commit_source_transaction",
            commit_then_race,
        )
        with pytest.raises(ConfigurationError) as stale:
            await application.apply_source_transaction(
                manifest,
                {"prompts/prompt-main.yaml": content},
            )
        assert stale.value.code == "source_transaction_stale"
        assert await application.current_configuration() == current
        assert not (definitions / ".a13n-transactions/active.json").exists()

        reconciled = await application.reload_configuration()
        prompt_ref = next(item for item in reconciled.resources if item.kind is ResourceKind.prompt)
        prompt_revision = await application.configuration_resource(prompt_ref)
        assert prompt_revision.normalized_content["instruction_blocks"] == [  # type: ignore[index]
            {"content": "Be concise.", "source": None}
        ]


async def test_source_transaction_rejects_unreconciled_external_edits(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    source = _write_complete_tree(definitions)
    settings = _full_settings(tmp_path / "data", definitions, discovery)

    async with open_application(settings) as application:
        current = await application.current_configuration()
        assert current is not None
        model = yaml.safe_load(source["models/model-main.yaml"])
        model["model_name"] = "externally-edited"
        _write_yaml(definitions / "models/model-main.yaml", model)
        prompt = yaml.safe_load(source["prompts/prompt-main.yaml"])
        prompt["instruction_blocks"] = [{"content": "Transaction edit.", "source": None}]
        content = yaml.safe_dump(prompt, sort_keys=True).encode()
        manifest = SourceTransactionManifest(
            schema_version="1",
            transaction_id="transaction-stale-base",
            root_id="root-user",
            base_catalog_digest=current.catalog_digest,
            entries=(
                SourceTransactionEntry(
                    relative_path="prompts/prompt-main.yaml",
                    operation="replace",
                    content_digest=hashlib.sha256(content).hexdigest(),
                ),
            ),
        )
        with pytest.raises(ConfigurationError) as stale:
            await application.apply_source_transaction(
                manifest,
                {"prompts/prompt-main.yaml": content},
            )
        assert stale.value.code == "source_transaction_stale"
        assert not (definitions / ".a13n-transactions/active.json").exists()


async def test_json_shapes_are_not_guessed_as_resource_references(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    source = _write_complete_tree(definitions)
    settings = _full_settings(tmp_path / "data", definitions, discovery)

    async with open_application(settings) as application:
        model = yaml.safe_load(source["models/model-main.yaml"])
        model["settings"] = {"example": {"kind": "agent", "resource_id": "agent-missing"}}
        _write_yaml(definitions / "models/model-main.yaml", model)
        generation = await application.reload_configuration()
        assert generation.generation_id == "config-2"


async def test_startup_removes_expired_unregistered_final_object(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    _write_complete_tree(definitions)
    settings = _full_settings(tmp_path / "data", definitions, discovery)
    settings = settings.model_copy(
        update={"configuration": settings.configuration.model_copy(update={"orphan_retention_seconds": 1})}
    )

    async with open_application(settings) as application:
        envelope = await application._store.objects.publish(
            object_kind=ObjectKind.provider_state,
            object_schema_version="1",
            payload={"orphan": "unregistered"},
        )
        path = application._store.objects._path_for(envelope.ref)
    os.utime(path, (0, 0))

    async with open_application(settings):
        assert not path.exists()


async def test_optional_missing_skill_source_and_scan_lease_bound(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "missing-discovery"
    _write_yaml(
        definitions / "skill-sources/skill-source-local.yaml",
        {
            "schema_version": "1",
            "skill_source_id": "skill-source-local",
            "display_name": "Optional Skills",
            "directory_id": "directory-skills",
            "roots": ["/"],
            "required": False,
            "max_entries_per_root": 32,
        },
    )
    settings = _full_settings(tmp_path / "data", definitions, discovery)
    settings = settings.model_copy(
        update={"configuration": settings.configuration.model_copy(update={"max_skill_scan_leases": 1})}
    )
    selected = LocalSkillDiscoverySettings(
        ordered_sources=(ResourceRef(kind=ResourceKind.skill_source, resource_id="skill-source-local"),),
    )

    async with open_application(settings) as application:
        preview = await application.scan_skills(selected)
        assert preview.items == ()
        await application.discard_skill_scan(preview.scan_id)


async def test_composed_skill_scan_reports_conflict_metadata(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    for root, description in (("first", "First workflow"), ("second", "Second workflow")):
        skill = discovery / root / "example"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"---\nname: example\ndescription: {description}\n---\n\nFollow it.\n")
        _write_yaml(
            definitions / f"skill-sources/skill-source-{root}.yaml",
            {
                "schema_version": "1",
                "skill_source_id": f"skill-source-{root}",
                "display_name": f"{root.title()} Skills",
                "directory_id": "directory-skills",
                "roots": [f"/{root}"],
                "required": True,
                "max_entries_per_root": 32,
            },
        )
    settings = _full_settings(tmp_path / "data", definitions, discovery)
    selected = LocalSkillDiscoverySettings(
        ordered_sources=(
            ResourceRef(kind=ResourceKind.skill_source, resource_id="skill-source-first"),
            ResourceRef(kind=ResourceKind.skill_source, resource_id="skill-source-second"),
        ),
        conflict="error",
    )

    async with open_application(settings) as application:
        conflicts = await application.skill_conflicts(selected)
        assert [item.model_dump(mode="json") for item in conflicts.conflicts] == [
            {
                "skill_name": "example",
                "source_ids": ["skill-source-first", "skill-source-second"],
                "selected_source_id": None,
            }
        ]
        resolved = await application.skill_conflicts(selected.model_copy(update={"conflict": "prefer_later"}))
        assert resolved.conflicts[0].selected_source_id == "skill-source-second"

        with pytest.raises(ConfigurationError) as conflict:
            await application.scan_skills(selected)
        assert conflict.value.code == "skill_catalog_ambiguous"
        assert conflict.value.details == {"skill": "example"}


async def test_skill_source_commands_manage_documents_and_enabled_order(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    skill = discovery / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: example\ndescription: Example workflow\n---\n\nFollow it.\n")
    configured = _full_settings(tmp_path / "data", definitions, discovery)
    process_path = tmp_path / "process-settings.yaml"
    _write_yaml(process_path, configured.configuration.model_dump(mode="json"))
    settings = AgentUiSettings(
        storage=configured.storage,
        configuration=ConfigurationSettings(),
        process_settings_path=process_path,
    )
    source = LocalSkillSourceDefinition(
        schema_version="1",
        skill_source_id="skill-source-local",
        display_name="Local Skills",
        directory_id="directory-skills",
        roots=("/",),
        required=True,
        max_entries_per_root=32,
    )

    async with open_application(settings) as application:
        created = await application.upsert_skill_source(source, target_root_id="root-user")
        assert created.generation_id == "config-2"
        statuses = await application.skill_source_statuses()
        assert [(item.source.display_name, item.selected, item.directory_status) for item in statuses] == [
            ("Local Skills", False, "available")
        ]

        edited = await application.upsert_skill_source(
            source.model_copy(update={"display_name": "Edited Skills"}),
            target_root_id="root-user",
        )
        assert edited.generation_id == "config-3"

        ordered = await application.reorder_skill_sources(("skill-source-local",))
        assert ordered.generation_id == "config-4"
        selected_status = (await application.skill_source_statuses())[0]
        assert selected_status.selected is True
        assert selected_status.selected_ordinal == 0
        preview = await application.scan_skill_source("skill-source-local")
        assert [item.name for item in preview.items] == ["example"]
        await application.discard_skill_scan(preview.scan_id)

        with pytest.raises(ConfigurationError) as selected:
            await application.delete_skill_source("skill-source-local")
        assert selected.value.code == "skill_source_selected"

        await application.reorder_skill_sources(())
        deleted = await application.delete_skill_source("skill-source-local")
        assert all(item.resource_id != "skill-source-local" for item in deleted.resources)

    async with open_application(settings) as restarted:
        assert await restarted.skill_source_statuses() == ()


async def test_skill_scan_lease_deadline_is_independent_of_reconciliation(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    skill = discovery / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: example\ndescription: Example workflow\n---\n\nFollow it.\n")
    _write_yaml(
        definitions / "skill-sources/skill-source-local.yaml",
        {
            "schema_version": "1",
            "skill_source_id": "skill-source-local",
            "display_name": "Local Skills",
            "directory_id": "directory-skills",
            "roots": ["/"],
            "required": True,
            "max_entries_per_root": 32,
        },
    )
    settings = _full_settings(tmp_path / "data", definitions, discovery)
    settings = settings.model_copy(
        update={
            "configuration": settings.configuration.model_copy(
                update={
                    "max_skill_scan_leases": 1,
                    "skill_scan_lease_seconds": 0.05,
                    "reconciliation_interval_seconds": 3600,
                }
            )
        }
    )
    selected = LocalSkillDiscoverySettings(
        ordered_sources=(ResourceRef(kind=ResourceKind.skill_source, resource_id="skill-source-local"),),
    )

    async with open_application(settings) as application:
        first = await application.scan_skills(selected)
        with fail_after(2):
            while first.scan_id in application._configuration.skills._scans:
                await sleep(0.01)
        replacement = await application.scan_skills(selected)
        await application.discard_skill_scan(replacement.scan_id)


async def test_skill_package_directory_entries_are_bounded(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    skill = discovery / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: example\ndescription: Example workflow\n---\n\nFollow it.\n")
    for name in ("one", "two", "three"):
        (skill / name).mkdir()
    _write_yaml(
        definitions / "skill-sources/skill-source-local.yaml",
        {
            "schema_version": "1",
            "skill_source_id": "skill-source-local",
            "display_name": "Local Skills",
            "directory_id": "directory-skills",
            "roots": ["/"],
            "required": True,
            "max_entries_per_root": 32,
        },
    )
    settings = _full_settings(tmp_path / "data", definitions, discovery)
    settings = settings.model_copy(
        update={"configuration": settings.configuration.model_copy(update={"max_skill_package_files": 2})}
    )

    async with open_application(settings) as application:
        scan = await application.scan_skill_source("skill-source-local")
        with pytest.raises(ConfigurationError) as limited:
            await application.preview_skill_import(
                scan_id=scan.scan_id,
                skill_name="example",
                skill_id="skill-example",
                display_name="Example Skill",
                target_root_id="root-user",
            )
        assert limited.value.code == "skill_package_limit"


async def test_prepared_skill_changes_have_an_aggregate_byte_limit(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    skill = discovery / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: example\ndescription: Example workflow\n---\n\nFollow it.\n")
    (skill / "large.bin").write_bytes(b"x" * 2048)
    _write_yaml(
        definitions / "skill-sources/skill-source-local.yaml",
        {
            "schema_version": "1",
            "skill_source_id": "skill-source-local",
            "display_name": "Local Skills",
            "directory_id": "directory-skills",
            "roots": ["/"],
            "required": True,
            "max_entries_per_root": 32,
        },
    )
    settings = _full_settings(tmp_path / "data", definitions, discovery)
    settings = settings.model_copy(
        update={"configuration": settings.configuration.model_copy(update={"max_prepared_skill_bytes": 1024})}
    )
    selected = LocalSkillDiscoverySettings(
        ordered_sources=(ResourceRef(kind=ResourceKind.skill_source, resource_id="skill-source-local"),),
    )

    async with open_application(settings) as application:
        scan = await application.scan_skills(selected)
        with pytest.raises(ConfigurationError) as limited:
            await application.preview_skill_import(
                scan_id=scan.scan_id,
                skill_name="example",
                skill_id="skill-example",
                display_name="Example Skill",
                target_root_id="root-user",
            )
        assert limited.value.code == "skill_change_limit"
        replacement = await application.scan_skills(selected)
        await application.discard_skill_scan(replacement.scan_id)


async def test_skill_scan_leases_are_bounded_and_discardable(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    discovery = tmp_path / "discovery"
    skill = discovery / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: example\ndescription: Example workflow\n---\n\nFollow it.\n")
    _write_yaml(
        definitions / "skill-sources/skill-source-local.yaml",
        {
            "schema_version": "1",
            "skill_source_id": "skill-source-local",
            "display_name": "Local Skills",
            "directory_id": "directory-skills",
            "roots": ["/"],
            "required": True,
            "max_entries_per_root": 32,
        },
    )
    settings = _full_settings(tmp_path / "data", definitions, discovery)
    settings = settings.model_copy(
        update={"configuration": settings.configuration.model_copy(update={"max_skill_scan_leases": 1})}
    )
    selected = LocalSkillDiscoverySettings(
        ordered_sources=(ResourceRef(kind=ResourceKind.skill_source, resource_id="skill-source-local"),),
    )

    async with open_application(settings) as application:
        first = await application.scan_skills(selected)
        with pytest.raises(ConfigurationError) as limited:
            await application.scan_skills(selected)
        assert limited.value.code == "skill_scan_limit"
        await application.discard_skill_scan(first.scan_id)
        replacement = await application.scan_skills(selected)
        change = await application.preview_skill_import(
            scan_id=replacement.scan_id,
            skill_name="example",
            skill_id="skill-example",
            display_name="Example Skill",
            target_root_id="root-user",
        )
        with pytest.raises(ConfigurationError) as prepared_limit:
            await application.scan_skills(selected)
        assert prepared_limit.value.code == "skill_scan_limit"
        await application.discard_skill_change(change.change_id)
        final = await application.scan_skills(selected)
        await application.discard_skill_scan(final.scan_id)


def test_runtime_manifest_and_envd_override_are_strict(tmp_path: Path) -> None:
    assets = []
    targets = (
        "darwin-aarch64",
        "darwin-x86_64",
        "linux-aarch64",
        "linux-x86_64",
        "windows-aarch64",
        "windows-x86_64",
    )
    for target in targets:
        executable = "agent-envd.exe" if target.startswith("windows-") else "agent-envd"
        archive = f"agent-envd-1.2.3-{target}.tar.zst"
        assets.append(
            {
                "target": target,
                "archive_name": archive,
                "archive_url": f"https://releases.example.invalid/1.2.3/{archive}",
                "archive_sha256": "1" * 64,
                "executable_name": executable,
                "executable_sha256": "2" * 64,
            }
        )
    manifest = load_envd_runtime_manifest(
        json.dumps({"schema_version": "1", "envd_release": "1.2.3", "assets": assets}).encode()
    )
    assert manifest is not None
    assert len(manifest.assets) == 6

    with pytest.raises(ConfigurationError) as incomplete:
        load_envd_runtime_manifest(
            json.dumps({"schema_version": "1", "envd_release": "1.2.3", "assets": assets[:-1]}).encode()
        )
    assert incomplete.value.code == "envd_runtime_manifest_invalid"

    with pytest.raises(ValidationError):
        ConfigurationSettings(envd_executable_override=Path("agent-envd"))
    with pytest.raises(ValidationError):
        ConfigurationSettings(envd_executable_override=tmp_path / "custom-envd")
