"""Resource platform tool contracts preserve deltas, package sources and observed versions."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from a13n_service.infra.errors import ServiceError
from a13n_service.platform_tools import ToolContext
from a13n_service.resources.agents import platform_tools as agents
from a13n_service.resources.agents.schemas import AgentConfig, AgentConfigurationChanges
from a13n_service.resources.environment_templates import platform_tools as templates
from a13n_service.resources.environment_templates.tool_inputs import TemplateCreation, TemplateEdit
from a13n_service.resources.models import platform_tools as models
from a13n_service.resources.skills import platform_tools as skills
from a13n_service.resources.skills.schemas import SkillCreate, SkillRevisionCreate
from fastapi import Request
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from .test_agent_configuration_changes import actor
from .test_agents import create_model
from .test_skills import DOCUMENT, archive, stage

pytestmark = pytest.mark.anyio


def context(service: SimpleNamespace) -> ToolContext:
    return ToolContext(
        service.runtime,
        actor(service),
        service.tenant.workspace_id,
        Request({"type": "http", "app": service.app, "headers": [], "client": ("127.0.0.1", 1234)}),
        None,
    )


def test_resource_schemas_are_explicit_and_versions_required():
    contracts = agents.TOOLS + models.TOOLS + templates.TOOLS + skills.TOOLS
    assert len(contracts) == 37
    for contract in contracts:
        schema = contract.inputs.model_json_schema()
        Draft202012Validator.check_schema(schema)
        assert "request_body" not in schema["properties"]
        if "expected_version" in schema["properties"]:
            assert "expected_version" in schema["required"]
    names = {contract.name for contract in contracts}
    assert "create_agent_revision" not in names
    assert "update_agent" not in names
    assert "create_skill_revision" not in names
    assert set(AgentConfigurationChanges.model_fields) == set(AgentConfig.model_fields)
    with pytest.raises(ValidationError):
        TemplateEdit.model_validate({"recipe": {"type": "docker", "configuration": {"privileged": True}}})


async def test_agent_platform_creation_and_atomic_delta(service):
    model = await create_model(service)
    ctx = context(service)
    head = await agents.create_agent(
        ctx,
        body=agents.AgentCreation.model_validate(
            {
                "name": "Platform Agent",
                "model": model,
                "instructions": "Preserve",
                "user_questions": True,
                "summarize": True,
                "model_settings": {"temperature": 0.4},
            }
        ),
    )
    result = await agents.update_agent_configuration(
        ctx,
        agent_id=head.id,
        expected_version=f'"{head.id}:{head.version}"',
        body=AgentConfigurationChanges.model_validate({"instructions": "Changed", "lazy_environment": False}),
    )
    assert result.changed_fields == ("instructions", "lazy_environment")
    assert result.revision.config.user_questions and result.revision.config.summarize
    assert result.revision.config.model_settings == {"temperature": 0.4}
    assert result.agent.default_revision_id == result.revision.id
    assert result.agent.version > head.version


async def test_model_delta_preserves_unmodified_native_config(service):
    key = await create_model(service, max_tokens=4096, temperature=0.6, settings={"openai_store": False})
    ctx = context(service)
    before = await models.get_model(ctx, key=key)
    changed = await models.update_model(
        ctx,
        key=key,
        expected_version=f'"{key}:{before.version}"',
        body=models.ModelEdit.model_validate({"temperature": 0.2, "name": "Edited"}),
    )
    assert changed.name == "Edited"
    assert changed.config.temperature == 0.2
    assert changed.config.max_tokens == 4096
    assert changed.config.settings == before.config.settings
    assert changed.config.model_api == before.config.model_api
    assert changed.config.model_name == before.config.model_name
    with pytest.raises(ServiceError) as stale:
        await models.update_model(
            ctx, key=key, expected_version=f'"{key}:{before.version}"', body=models.ModelEdit(enabled=False)
        )
    assert stale.value.code == "precondition_failed"


async def test_template_lifecycle_delta_and_provider_recipe_fence(service):
    response = await service.client.post(
        f"{service.api}/environment-providers", json={"type": "docker", "name": "Docker"}
    )
    assert response.status_code == 201, response.text
    provider_id = response.json()["id"]
    ctx = context(service)
    before = await templates.create_environment_template(
        ctx,
        body=TemplateCreation.model_validate(
            {
                "name": "Build",
                "provider_id": provider_id,
                "recipe": {"type": "docker", "configuration": {"image": "example/build:1"}},
                "stop_after_seconds": 600,
                "delete_after_seconds": 86400,
            }
        ),
    )
    changed = await templates.update_environment_template(
        ctx,
        template_id=before.id,
        expected_version=f'"{before.id}:{before.version}"',
        body=TemplateEdit(stop_after_seconds=None),
    )
    assert changed.config.stop_after_seconds is None
    assert changed.config.delete_after_seconds == 86400
    assert changed.config.recipe == before.config.recipe
    other = await service.client.post(
        f"{service.api}/environment-providers", json={"type": "docker", "name": "Another"}
    )
    assert other.status_code == 201, other.text
    with pytest.raises(ServiceError) as missing_recipe:
        await templates.update_environment_template(
            ctx,
            template_id=before.id,
            expected_version=f'"{changed.id}:{changed.version}"',
            body=TemplateEdit(provider_id=other.json()["id"]),
        )
    assert missing_recipe.value.code == "invalid_argument"
    assert missing_recipe.value.details["field"] == "recipe"


async def test_skill_source_publication_returns_version_with_exact_package(service):
    upload_id = await stage(service, archive({"SKILL.md": DOCUMENT}), uuid4().hex)
    ctx = context(service)
    head = await skills.create_skill(
        ctx, body=SkillCreate.model_validate({"source": {"kind": "upload", "upload_id": upload_id}})
    )
    before = await skills.get_skill_revision(ctx, skill_id=head.id, revision_id=head.default_revision_id)
    next_upload = await stage(service, archive({"SKILL.md": DOCUMENT + b"\nNew package version\n"}), uuid4().hex)
    result = await skills.update_skill_source(
        ctx,
        skill_id=head.id,
        expected_version=f'"{head.id}:{head.version}"',
        body=SkillRevisionCreate.model_validate(
            {"source": {"kind": "upload", "upload_id": next_upload}, "make_default": False}
        ),
    )
    assert result.status == "created"
    assert result.skill.version > head.version
    assert result.skill.default_revision_id == head.default_revision_id
    assert result.revision.digest != before.digest
    assert result.revision.config.source.kind == "upload"
    historical = await skills.get_skill_revision(ctx, skill_id=head.id, revision_id=before.id)
    assert historical.digest == before.digest


def test_immutable_configuration_reads_preserve_removed_tool_names_without_admitting_them():
    stored = {
        "model": "historical",
        "toolsets": {"agents": {"enabled": True, "tools": {"create_agent_revision": {"enabled": True}}}},
        "tool_search": {"groups": {"authoring": {"description": "Authoring tools", "platform": True}}},
    }
    read = AgentConfig.model_validate(stored, context={"preserve_toolsets": True})
    assert set(read.toolsets) == {"agents"}
    assert set(read.toolsets["agents"].tools) == {"create_agent_revision"}
    with pytest.raises(ValidationError, match="unknown agents tool"):
        AgentConfig.model_validate(stored)
