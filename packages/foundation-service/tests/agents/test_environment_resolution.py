from __future__ import annotations

from pathlib import Path

import pytest
from a13n_service.agents.domain import (
    AgentRunOverride,
    CreateAgentRequest,
)
from a13n_service.agents.errors import AgentError
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.service import AgentService
from a13n_service.environments.domain import (
    CreateEnvironmentRequest,
    CreateEnvironmentRevisionRequest,
    PutEnvironmentProviderSelectionRequest,
    UpdateEnvironmentRequest,
)
from a13n_service.environments.service import EnvironmentManagementService
from a13n_service.etags import resource_etag
from a13n_service.skills.models import SkillRecord, SkillRevisionRecord
from a13n_service.skills.package import normalize_skill_files
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import (
    NOW,
    ORG_ID,
    SECRET_ID,
    USER_ID,
    WORKSPACE_ID,
    actor,
    agent_config,
    create_current_revision,
)

PROVIDER_KEY = "a13n.direct-local"
SKILL_ID = "sk_1234567890abcdef"
SKILL_REVISION_ID = "skr_1234567890abcdef"
SKILL_REVISION_V2_ID = "skr_abcdef1234567890"


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
                key="deploy",
                name="Deploy",
                version=1,
                current_revision_id=SKILL_REVISION_ID,
                created_by_type="user",
                created_by_id=USER_ID,
                updated_by_type="user",
                updated_by_id=USER_ID,
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
                version=1,
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
    agent_service: AgentService,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _skill(agent_sessions)
    environment_id, environment_revision_id = await _environment(agent_environment_service, tmp_path)
    created = await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-with-environment",
        request=CreateAgentRequest(
            name="Environment Agent",
            config=agent_config(
                skills=[{"skill_key": "deploy"}],
                environment={"environment_revision_id": environment_revision_id},
            ),
        ),
    )
    revision_result, _ = await create_current_revision(
        agent_service,
        agent_id=created.agent.id,
        expected_version=1,
        key="with-environment",
    )

    frozen_environment = revision_result.revision.resolved_environment
    assert frozen_environment is not None
    assert frozen_environment.source_environment_revision_id == environment_revision_id
    assert frozen_environment.provider.provider_key == PROVIDER_KEY
    assert frozen_environment.access == "read_write"
    assert frozen_environment.provider_lock["registration_digest_sha256"]
    assert tuple(item.skill_id for item in revision_result.revision.resolved_skills) == (SKILL_ID,)
    assert revision_result.revision.resolved_skills[0].skill_key == "deploy"
    assert revision_result.revision.resolved_skills[0].version is None

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
            expected_version=1,
            provider=next_provider,
            credential_bindings=original.credential_bindings,
            access=original.access,
        ),
    )
    assert newer.created

    inherited = await agent_invocation_resolver.prepare(actor=actor(), agent_id=created.agent.id)
    async with transaction(agent_sessions) as session:
        inherited_frozen = await agent_invocation_resolver.freeze_in_transaction(session, prepared=inherited)
    inherited_environment = inherited_frozen.effective_config.resolved_environment
    assert inherited_environment is not None
    assert inherited_environment.source_environment_revision_id == environment_revision_id
    assert inherited_frozen.effective_config.skills[0].skill_id == SKILL_ID
    assert inherited_frozen.effective_config.skills[0].skill_revision_id == SKILL_REVISION_ID
    assert inherited_frozen.effective_config.skills[0].skill_key == "deploy"
    assert inherited_frozen.effective_config.skills[0].version == 1

    updated_package = normalize_skill_files(
        (("SKILL.md", b"---\nname: deploy\ndescription: Deploy safely.\n---\n\n# Deploy v2\n"),)
    )
    async with transaction(agent_sessions) as session:
        skill = await session.get(SkillRecord, SKILL_ID)
        assert skill is not None
        session.add(
            SkillRevisionRecord(
                id=SKILL_REVISION_V2_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                skill_id=SKILL_ID,
                version=2,
                content_digest=updated_package.manifest.content_digest,
                manifest=updated_package.manifest.model_dump(mode="json"),
                imported_from={"kind": "zip", "archive_sha256": "1" * 64},
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
            )
        )
        skill.current_revision_id = SKILL_REVISION_V2_ID
        skill.version = 2
    advanced = await agent_invocation_resolver.prepare(actor=actor(), agent_id=created.agent.id)
    async with transaction(agent_sessions) as session:
        advanced_frozen = await agent_invocation_resolver.freeze_in_transaction(session, prepared=advanced)
    assert advanced_frozen.effective_config.skills[0].skill_revision_id == SKILL_REVISION_V2_ID
    assert advanced_frozen.effective_config.skills[0].version == 2
    pinned_override = await agent_invocation_resolver.prepare(
        actor=actor(),
        agent_id=created.agent.id,
        config_override=AgentRunOverride.model_validate({"skills": [{"skill_key": "deploy", "version": 1}]}),
    )
    async with transaction(agent_sessions) as session:
        pinned_override_frozen = await agent_invocation_resolver.freeze_in_transaction(
            session,
            prepared=pinned_override,
        )
    assert pinned_override_frozen.effective_config.skills[0].skill_revision_id == SKILL_REVISION_ID
    assert pinned_override_frozen.effective_config.skills[0].version == 1

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
    with pytest.raises(AgentError) as invalid_override:
        await agent_invocation_resolver.prepare(
            actor=actor(),
            agent_id=created.agent.id,
            config_override=AgentRunOverride.model_validate({"environment": inline_read_only}),
        )
    assert invalid_override.value.code == "agent_revision_not_executable"
    assert invalid_override.value.details["reason"] == "skill_environment_not_writable"

    prepared = await agent_invocation_resolver.prepare(
        actor=actor(),
        agent_id=created.agent.id,
        config_override=AgentRunOverride.model_validate({"skills": [], "environment": inline_read_only}),
    )
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freeze_in_transaction(session, prepared=prepared)
    assert frozen.effective_config.resolved_environment is not None
    assert frozen.effective_config.resolved_environment.source_environment_revision_id is None
    assert frozen.effective_config.resolved_environment.access == "read_only"
    assert frozen.effective_config.runtime_lock_digest == revision_result.revision.runtime_lock_digest

    async with transaction(agent_sessions) as session:
        skill = await session.get(SkillRecord, SKILL_ID)
        assert skill is not None
        skill.deleted_at = NOW
    with pytest.raises(AgentError) as deleted:
        await agent_invocation_resolver.prepare(actor=actor(), agent_id=created.agent.id)
    assert deleted.value.code == "agent_revision_not_executable"
    assert deleted.value.details["reason"] == "skill_selection_invalid"


@pytest.mark.anyio
async def test_skill_selection_requires_a_writable_primary_environment(
    agent_environment_service: EnvironmentManagementService,
    agent_service: AgentService,
    agent_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    with pytest.raises(AgentError) as missing:
        await agent_service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="create-skill-without-environment",
            request=CreateAgentRequest(
                name="Missing Skill Environment",
                config=agent_config(skills=[{"skill_key": "deploy"}]),
            ),
        )
    assert missing.value.code == "agent_revision_create_failed"
    assert missing.value.details == {"path": "environment", "reason": "skill_environment_required"}

    await _skill(agent_sessions)
    _, environment_revision_id = await _environment(agent_environment_service, tmp_path, access="read_only")
    with pytest.raises(AgentError) as read_only_error:
        await agent_service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="create-skill-read-only-environment",
            request=CreateAgentRequest(
                name="Read-only Skill Environment",
                config=agent_config(
                    skills=[{"skill_key": "deploy"}],
                    environment={"environment_revision_id": environment_revision_id},
                ),
            ),
        )
    assert read_only_error.value.code == "agent_revision_create_failed"
    assert read_only_error.value.details == {
        "path": "environment",
        "reason": "skill_environment_not_writable",
    }


@pytest.mark.anyio
async def test_pinned_skill_version_stays_exact_when_current_advances(
    agent_environment_service: EnvironmentManagementService,
    agent_service: AgentService,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _skill(agent_sessions)
    _, environment_revision_id = await _environment(agent_environment_service, tmp_path)
    created = await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-pinned-skill-agent",
        request=CreateAgentRequest(
            name="Pinned Skill Agent",
            config=agent_config(
                skills=[{"skill_key": "deploy", "version": 1}],
                environment={"environment_revision_id": environment_revision_id},
            ),
        ),
    )
    assert created.revision.resolved_skills[0].version == 1

    updated_package = normalize_skill_files(
        (("SKILL.md", b"---\nname: deploy\ndescription: Deploy safely.\n---\n\n# Deploy v2\n"),)
    )
    async with transaction(agent_sessions) as session:
        skill = await session.get(SkillRecord, SKILL_ID)
        assert skill is not None
        session.add(
            SkillRevisionRecord(
                id=SKILL_REVISION_V2_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                skill_id=SKILL_ID,
                version=2,
                content_digest=updated_package.manifest.content_digest,
                manifest=updated_package.manifest.model_dump(mode="json"),
                imported_from={"kind": "zip", "archive_sha256": "1" * 64},
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
            )
        )
        skill.current_revision_id = SKILL_REVISION_V2_ID
        skill.version = 2

    prepared = await agent_invocation_resolver.prepare(actor=actor(), agent_id=created.agent.id)
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freeze_in_transaction(session, prepared=prepared)
    assert frozen.effective_config.skills[0].skill_revision_id == SKILL_REVISION_ID
    assert frozen.effective_config.skills[0].version == 1


@pytest.mark.anyio
async def test_unarchive_revalidates_current_skill_bindings(
    agent_environment_service: EnvironmentManagementService,
    agent_service: AgentService,
    agent_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _skill(agent_sessions)
    _, environment_revision_id = await _environment(agent_environment_service, tmp_path)
    created = await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-agent-for-unarchive",
        request=CreateAgentRequest(
            name="Archived Skill Agent",
            config=agent_config(
                skills=[{"skill_key": "deploy"}],
                environment={"environment_revision_id": environment_revision_id},
            ),
        ),
    )
    disabled = await agent_service.change_lifecycle(
        actor=actor(),
        agent_id=created.agent.id,
        action="disable",
        idempotency_key="disable-agent-for-unarchive",
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
    )
    archived = await agent_service.change_lifecycle(
        actor=actor(),
        agent_id=created.agent.id,
        action="archive",
        idempotency_key="archive-agent-for-unarchive",
        if_match=resource_etag(disabled.id, disabled.updated_at),
    )
    async with transaction(agent_sessions) as session:
        skill = await session.get(SkillRecord, SKILL_ID)
        assert skill is not None
        skill.deleted_at = NOW

    with pytest.raises(AgentError) as unavailable:
        await agent_service.change_lifecycle(
            actor=actor(),
            agent_id=archived.id,
            action="unarchive",
            idempotency_key="unarchive-agent-with-deleted-skill",
            if_match=resource_etag(archived.id, archived.updated_at),
        )
    assert unavailable.value.code == "agent_revision_not_executable"
    assert unavailable.value.details["reason"] == "skill_selection_invalid"


@pytest.mark.anyio
async def test_invocation_rejects_provider_selection_race(
    agent_environment_service: EnvironmentManagementService,
    agent_service: AgentService,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    _, environment_revision_id = await _environment(agent_environment_service, tmp_path)
    created = await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-environment-race",
        request=CreateAgentRequest(
            name="Environment Race",
            config=agent_config(environment={"environment_revision_id": environment_revision_id}),
        ),
    )
    await create_current_revision(
        agent_service,
        agent_id=created.agent.id,
        expected_version=1,
        key="environment-race",
    )
    prepared = await agent_invocation_resolver.prepare(actor=actor(), agent_id=created.agent.id)
    selected = await agent_environment_service.get_provider_selection(
        actor=actor(), workspace_id=WORKSPACE_ID, provider_key=PROVIDER_KEY
    )
    await agent_environment_service.put_provider_selection(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_key=PROVIDER_KEY,
        request=PutEnvironmentProviderSelectionRequest(enabled=False),
        if_match=resource_etag(
            f"{WORKSPACE_ID}:{PROVIDER_KEY}",
            selected.updated_at,
        ),
    )

    with pytest.raises(AgentError) as raced:
        async with transaction(agent_sessions) as session:
            await agent_invocation_resolver.freeze_in_transaction(session, prepared=prepared)
    assert raced.value.code == "agent_revision_not_executable"
    assert raced.value.details["reason"] == "environment_provider_disabled"


@pytest.mark.anyio
async def test_invocation_rejects_environment_archived_during_acceptance(
    agent_environment_service: EnvironmentManagementService,
    agent_service: AgentService,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    environment_id, environment_revision_id = await _environment(agent_environment_service, tmp_path)
    created = await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-environment-archive-race",
        request=CreateAgentRequest(
            name="Environment Archive Race",
            config=agent_config(environment={"environment_revision_id": environment_revision_id}),
        ),
    )
    await create_current_revision(
        agent_service,
        agent_id=created.agent.id,
        expected_version=1,
        key="environment-archive-race",
    )
    prepared = await agent_invocation_resolver.prepare(actor=actor(), agent_id=created.agent.id)
    environment = await agent_environment_service.get(actor=actor(), environment_id=environment_id)
    await agent_environment_service.patch(
        actor=actor(),
        environment_id=environment_id,
        if_match=resource_etag(environment.id, environment.updated_at),
        request=UpdateEnvironmentRequest(archived=True),
    )

    with pytest.raises(AgentError) as raced:
        async with transaction(agent_sessions) as session:
            await agent_invocation_resolver.freeze_in_transaction(session, prepared=prepared)
    assert raced.value.code == "agent_revision_not_executable"
    assert raced.value.details["reason"] == "environment_revision_changed"
