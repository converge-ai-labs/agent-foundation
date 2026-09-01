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
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import SECRET_ID, WORKSPACE_ID, actor, preset_config

PROVIDER_KEY = "a13n.direct-local"


async def _environment(
    service: EnvironmentManagementService,
    root: Path,
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
                "access": "read_write",
            }
        ),
    )
    return created.id, created.current_revision_id


@pytest.mark.anyio
async def test_publish_freezes_exact_environment_and_invocation_can_override_inline(
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
        idempotency_key="create-with-environment",
        request=CreateAgentPresetRequest(
            name="Environment Agent",
            config=preset_config(environment={"environment_revision_id": environment_revision_id}),
        ),
    )
    published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-with-environment",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )

    frozen_environment = published.revision.resolved_environment
    assert frozen_environment is not None
    assert frozen_environment.source_environment_revision_id == environment_revision_id
    assert frozen_environment.provider.provider_key == PROVIDER_KEY
    assert frozen_environment.access == "read_write"
    assert frozen_environment.provider_lock["registration_digest_sha256"]

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

    prepared = await agent_preset_invocation_resolver.prepare(
        actor=actor(),
        agent_preset_id=preset.id,
        config_override=AgentRunOverride.model_validate(
            {
                "environment": {
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
            }
        ),
    )
    async with transaction(agent_preset_sessions) as session:
        frozen = await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=prepared)
    assert frozen.effective_config.resolved_environment is not None
    assert frozen.effective_config.resolved_environment.source_environment_revision_id is None
    assert frozen.effective_config.resolved_environment.access == "read_only"
    assert frozen.effective_config.runtime_lock_digest != published.revision.runtime_lock_digest


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
    await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-environment-race",
        request=AgentPresetCommandRequest(expected_resource_version=1),
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
    await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-environment-archive-race",
        request=AgentPresetCommandRequest(expected_resource_version=1),
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
