from __future__ import annotations

from pathlib import Path

import pytest
from a13n_service.agent_presets.domain import (
    AgentPresetCommandRequest,
    AgentRunOverride,
    CreateAgentPresetRequest,
)
from a13n_service.agent_presets.errors import AgentPresetError
from a13n_service.agent_presets.invocation_resolution import AgentPresetInvocationResolver
from a13n_service.agent_presets.service import AgentPresetService
from a13n_service.environments.domain import (
    CreateEnvironmentRequest,
    CreateEnvironmentRevisionRequest,
    PatchEnvironmentRequest,
    PutEnvironmentProviderSelectionRequest,
)
from a13n_service.environments.service import EnvironmentManagementService
from a13n_service.skills.models import SkillRecord, SkillRevisionRecord
from a13n_service.skills.package import normalize_skill_files
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, ORG_ID, SECRET_ID, USER_ID, WORKSPACE_ID, actor, create_default_revision, preset_config

PROVIDER_KEY = "a13n.direct-local"
SKILL_ID = "sk_1234567890abcdef"
SKILL_REVISION_ID = "skr_1234567890abcdef"


async def _environment(
    service: EnvironmentManagementService,
    root: Path,
    *,
    access: str = "read_write",
) -> tuple[str, str]:
    await service.put_provider_selection(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_key=PROVIDER_KEY,
        request=PutEnvironmentProviderSelectionRequest(enabled=True),
    )
    created = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-agent-environment",
        request=CreateEnvironmentRequest.model_validate(
            {
                "name": "Agent Workspace",
                "provider": {
                    "provider_key": PROVIDER_KEY,
                    "schema_version": "1",
                    "configuration": {
                        "environment_id": "agent-workspace",
                        "root": {"path": str(root)},
                    },
                },
                "credential_bindings": [
                    {
                        "requirement_key": "token",
                        "credential": {"source": "workspace_secret", "secret_id": SECRET_ID},
                    }
                ],
                "access": access,
            }
        ),
    )
    return created.id, created.current_revision_id


async def _skill(sessions: async_sessionmaker[AsyncSession]) -> None:
    package = normalize_skill_files(
        (("SKILL.md", b"---\nname: deploy\ndescription: Deploy safely.\n---\n\n# Deploy\n"),)
    )
    async with transaction(sessions) as session:
        session.add(
            SkillRecord(
                id=SKILL_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                display_name="Deploy",
                version=1,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        session.add(
            SkillRevisionRecord(
                id=SKILL_REVISION_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                skill_id=SKILL_ID,
                revision_number=1,
                content_digest=package.manifest.content_digest,
                manifest=package.manifest.model_dump(mode="json"),
                imported_from={"kind": "zip", "archive_sha256": "0" * 64},
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
            )
        )


@pytest.mark.anyio
async def test_create_revision_freezes_exact_environment_and_invocation_can_override_inline(
    agent_environment_service: EnvironmentManagementService,
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _skill(agent_preset_sessions)
    environment_id, environment_revision_id = await _environment(agent_environment_service, tmp_path)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-with-environment",
        request=CreateAgentPresetRequest(
            name="Environment Agent",
            config=preset_config(
                skills=[{"skill_revision_id": SKILL_REVISION_ID}],
                environment={"environment_revision_id": environment_revision_id},
            ),
        ),
    )
    revision_result, _ = await create_default_revision(
        agent_preset_service,
        preset_id=preset.id,
        expected_resource_version=1,
        key="with-environment",
    )

    frozen_environment = revision_result.revision.resolved_environment
    assert frozen_environment is not None
    assert frozen_environment.source_environment_revision_id == environment_revision_id
    assert frozen_environment.provider.provider_key == PROVIDER_KEY
    assert frozen_environment.access == "read_write"
    assert frozen_environment.provider_lock["registration_digest_sha256"]
    assert tuple(item.skill_revision_id for item in revision_result.revision.resolved_skills) == (SKILL_REVISION_ID,)

    original = await agent_environment_service.get_revision(actor=actor(), revision_id=environment_revision_id)
    next_provider = original.provider.model_copy(
        update={
            "configuration": {
                **original.provider.configuration,
                "environment_id": "agent-workspace-v2",
            }
        }
    )
    newer = await agent_environment_service.create_revision(
        actor=actor(),
        environment_id=environment_id,
        idempotency_key="newer-agent-environment",
        request=CreateEnvironmentRevisionRequest(
            expected_environment_version=1,
            provider=next_provider,
            credential_bindings=original.credential_bindings,
            access=original.access,
        ),
    )
    assert newer.created

    inherited = await agent_preset_invocation_resolver.prepare(actor=actor(), agent_preset_id=preset.id)
    async with transaction(agent_preset_sessions) as session:
        inherited_frozen = await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=inherited)
    inherited_environment = inherited_frozen.effective_config.resolved_environment
    assert inherited_environment is not None
    assert inherited_environment.source_environment_revision_id == environment_revision_id

    inline_read_only = {
        "provider": {
            "provider_key": PROVIDER_KEY,
            "schema_version": "1",
            "configuration": {
                "environment_id": "one-run",
                "root": {"path": str(tmp_path), "read_only": True},
            },
        },
        "credential_bindings": [],
        "access": "read_only",
    }
    with pytest.raises(AgentPresetError) as invalid_override:
        await agent_preset_invocation_resolver.prepare(
            actor=actor(),
            agent_preset_id=preset.id,
            config_override=AgentRunOverride.model_validate({"environment": inline_read_only}),
        )
    assert invalid_override.value.code == "preset_revision_not_executable"
    assert invalid_override.value.details["reason"] == "skill_environment_not_writable"

    prepared = await agent_preset_invocation_resolver.prepare(
        actor=actor(),
        agent_preset_id=preset.id,
        config_override=AgentRunOverride.model_validate({"skills": [], "environment": inline_read_only}),
    )
    async with transaction(agent_preset_sessions) as session:
        frozen = await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=prepared)
    assert frozen.effective_config.resolved_environment is not None
    assert frozen.effective_config.resolved_environment.source_environment_revision_id is None
    assert frozen.effective_config.resolved_environment.access == "read_only"
    assert frozen.effective_config.runtime_lock_digest == revision_result.revision.runtime_lock_digest

    async with transaction(agent_preset_sessions) as session:
        skill = await session.get(SkillRecord, SKILL_ID)
        assert skill is not None
        skill.deleted_at = NOW
    retained = await agent_preset_invocation_resolver.prepare(actor=actor(), agent_preset_id=preset.id)
    async with transaction(agent_preset_sessions) as session:
        retained_frozen = await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=retained)
    assert tuple(item.skill_revision_id for item in retained_frozen.effective_config.resolved_skills) == (
        SKILL_REVISION_ID,
    )


@pytest.mark.anyio
async def test_skill_selection_requires_a_writable_primary_environment(
    agent_environment_service: EnvironmentManagementService,
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    without_environment = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-skill-without-environment",
        request=CreateAgentPresetRequest(
            name="Missing Skill Environment",
            config=preset_config(skills=[{"skill_revision_id": SKILL_REVISION_ID}]),
        ),
    )
    with pytest.raises(AgentPresetError) as missing:
        await agent_preset_service.create_revision(
            actor=actor(),
            preset_id=without_environment.id,
            idempotency_key="revision-skill-without-environment",
            request=AgentPresetCommandRequest(expected_resource_version=without_environment.resource_version),
        )
    assert missing.value.code == "preset_revision_create_failed"
    assert missing.value.details == {"path": "environment", "reason": "skill_environment_required"}

    await _skill(agent_preset_sessions)
    _, environment_revision_id = await _environment(agent_environment_service, tmp_path, access="read_only")
    read_only = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-skill-read-only-environment",
        request=CreateAgentPresetRequest(
            name="Read-only Skill Environment",
            config=preset_config(
                skills=[{"skill_revision_id": SKILL_REVISION_ID}],
                environment={"environment_revision_id": environment_revision_id},
            ),
        ),
    )
    with pytest.raises(AgentPresetError) as read_only_error:
        await agent_preset_service.create_revision(
            actor=actor(),
            preset_id=read_only.id,
            idempotency_key="revision-skill-read-only-environment",
            request=AgentPresetCommandRequest(expected_resource_version=read_only.resource_version),
        )
    assert read_only_error.value.code == "preset_revision_create_failed"
    assert read_only_error.value.details == {
        "path": "environment",
        "reason": "skill_environment_not_writable",
    }


@pytest.mark.anyio
async def test_invocation_rejects_provider_selection_race(
    agent_environment_service: EnvironmentManagementService,
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    _, environment_revision_id = await _environment(agent_environment_service, tmp_path)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-environment-race",
        request=CreateAgentPresetRequest(
            name="Environment Race",
            config=preset_config(environment={"environment_revision_id": environment_revision_id}),
        ),
    )
    await create_default_revision(
        agent_preset_service,
        preset_id=preset.id,
        expected_resource_version=1,
        key="environment-race",
    )
    prepared = await agent_preset_invocation_resolver.prepare(actor=actor(), agent_preset_id=preset.id)
    selected = await agent_environment_service.get_provider_selection(
        actor=actor(), workspace_id=WORKSPACE_ID, provider_key=PROVIDER_KEY
    )
    await agent_environment_service.put_provider_selection(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_key=PROVIDER_KEY,
        request=PutEnvironmentProviderSelectionRequest(enabled=False, expected_version=selected.version),
    )

    with pytest.raises(AgentPresetError) as raced:
        async with transaction(agent_preset_sessions) as session:
            await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=prepared)
    assert raced.value.code == "preset_revision_not_executable"
    assert raced.value.details["reason"] == "environment_provider_disabled"


@pytest.mark.anyio
async def test_invocation_rejects_environment_archived_during_acceptance(
    agent_environment_service: EnvironmentManagementService,
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    environment_id, environment_revision_id = await _environment(agent_environment_service, tmp_path)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-environment-archive-race",
        request=CreateAgentPresetRequest(
            name="Environment Archive Race",
            config=preset_config(environment={"environment_revision_id": environment_revision_id}),
        ),
    )
    await create_default_revision(
        agent_preset_service,
        preset_id=preset.id,
        expected_resource_version=1,
        key="environment-archive-race",
    )
    prepared = await agent_preset_invocation_resolver.prepare(actor=actor(), agent_preset_id=preset.id)
    environment = await agent_environment_service.get(actor=actor(), environment_id=environment_id)
    await agent_environment_service.patch(
        actor=actor(),
        environment_id=environment_id,
        request=PatchEnvironmentRequest(expected_version=environment.version, archived=True),
    )

    with pytest.raises(AgentPresetError) as raced:
        async with transaction(agent_preset_sessions) as session:
            await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=prepared)
    assert raced.value.code == "preset_revision_not_executable"
    assert raced.value.details["reason"] == "environment_revision_changed"
