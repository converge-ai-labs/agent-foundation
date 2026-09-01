from __future__ import annotations

import pytest
from a13n_service.agent_presets.domain import (
    AgentPresetCommandRequest,
    AgentRunOverride,
    CreateAgentPresetRequest,
    PluginRuntimeMode,
)
from a13n_service.agent_presets.errors import AgentPresetError
from a13n_service.agent_presets.invocation_resolution import AgentPresetInvocationResolver
from a13n_service.agent_presets.plugin_resolution import AgentPluginSelectionResolver, PluginSelectionError
from a13n_service.agent_presets.service import AgentPresetService
from a13n_service.plugins.models import PluginRecord, PluginVersionRecord
from a13n_service.storage import short_session, transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, USER_ID, WORKSPACE_ID, actor, preset_config

PLUGIN_ID = "plg_1234567890abcdef"
PLUGIN_VERSION_ID = "plgv_1234567890abcdef"
PLUGIN_DIGEST = "a" * 64


async def _add_plugin(
    sessions: async_sessionmaker[AsyncSession],
    *,
    lifecycle_state: str = "available",
    requires_dist: tuple[str, ...] = (),
) -> None:
    async with transaction(sessions) as session:
        session.add(
            PluginRecord(
                id=PLUGIN_ID,
                source="uploaded",
                plugin_key="acme.audit",
                distribution_name="acme-audit",
                top_level_package="acme_audit",
                active_version_id=None,
                lifecycle_state=lifecycle_state,
                required=False,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            PluginVersionRecord(
                id=PLUGIN_VERSION_ID,
                plugin_id=PLUGIN_ID,
                version="1.2.3",
                content_digest=PLUGIN_DIGEST,
                artifact_ref=f"plugins/artifacts/v1/sha256/{PLUGIN_DIGEST}.whl",
                size_bytes=1024,
                requires_dist=list(requires_dist),
                requires_python=">=3.13",
                wheel_tags=["py3-none-any"],
                root_is_purelib=True,
                entry_point_target="acme_audit.factory:Factory",
                status="ready",
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
            )
        )


def _selection(*, instance_name: str = "audit") -> dict[str, object]:
    return {
        "mode": "on_demand",
        "instance_name": instance_name,
        "plugin_version_id": PLUGIN_VERSION_ID,
        "config": {"level": "strict"},
    }


@pytest.mark.anyio
async def test_publish_freezes_exact_plugin_version(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-plugin-preset",
        request=CreateAgentPresetRequest(
            name="Plugin Preset",
            config=preset_config(plugins=[_selection()]),
        ),
    )

    published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-plugin-preset",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )

    assert published.revision.plugin_runtime_mode is PluginRuntimeMode.on_demand
    assert published.revision.resolved_plugin_versions[0].model_dump(mode="json") == {
        "instance_name": "audit",
        "plugin_id": PLUGIN_ID,
        "plugin_version_id": PLUGIN_VERSION_ID,
        "plugin_key": "acme.audit",
        "distribution_name": "acme-audit",
        "distribution_version": "1.2.3",
        "top_level_package": "acme_audit",
        "wheel_digest": PLUGIN_DIGEST,
        "config": {"level": "strict"},
    }
    assert len(published.revision.runtime_lock_digest) == 64


@pytest.mark.anyio
async def test_run_override_resolves_plugin_and_changes_runtime_lock(
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-plugin-override",
        request=CreateAgentPresetRequest(name="Plugin Override", config=preset_config()),
    )
    published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-plugin-override",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )

    prepared = await agent_preset_invocation_resolver.prepare(
        actor=actor(),
        agent_preset_id=preset.id,
        config_override=AgentRunOverride.model_validate({"plugins": [_selection()]}),
    )
    async with transaction(agent_preset_sessions) as session:
        frozen = await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=prepared)

    assert frozen.effective_config.resolved_plugin_versions[0].plugin_version_id == PLUGIN_VERSION_ID
    assert frozen.effective_config.runtime_lock_digest != published.revision.runtime_lock_digest


@pytest.mark.anyio
async def test_retained_revision_keeps_archived_plugin_executable(
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-retained-plugin",
        request=CreateAgentPresetRequest(
            name="Retained Plugin",
            config=preset_config(plugins=[_selection()]),
        ),
    )
    published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-retained-plugin",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    async with transaction(agent_preset_sessions) as session:
        plugin = await session.get(PluginRecord, PLUGIN_ID)
        assert plugin is not None
        plugin.lifecycle_state = "archived"

    prepared = await agent_preset_invocation_resolver.prepare(actor=actor(), agent_preset_id=preset.id)
    async with transaction(agent_preset_sessions) as session:
        frozen = await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=prepared)

    assert frozen.effective_config.resolved_plugin_versions == published.revision.resolved_plugin_versions
    assert frozen.effective_config.runtime_lock_digest == published.revision.runtime_lock_digest


@pytest.mark.anyio
async def test_publish_rejects_mode_and_worker_dependency_mismatches(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions, requires_dist=("foundation-plugin-missing>=1",))
    missing_dependency = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-missing-plugin-dependency",
        request=CreateAgentPresetRequest(
            name="Missing Plugin Dependency",
            config=preset_config(plugins=[_selection()]),
        ),
    )
    with pytest.raises(AgentPresetError) as rejected:
        await agent_preset_service.publish(
            actor=actor(),
            preset_id=missing_dependency.id,
            idempotency_key="publish-missing-plugin-dependency",
            request=AgentPresetCommandRequest(expected_resource_version=1),
        )
    assert rejected.value.code == "preset_publish_failed"
    assert rejected.value.details == {"reason": "plugin_worker_dependency_missing", "path": "plugins.0"}

    wrong_mode = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-wrong-plugin-mode",
        request=CreateAgentPresetRequest(
            name="Wrong Plugin Mode",
            config=preset_config(
                plugins=[
                    {
                        "mode": "runner",
                        "instance_name": "audit",
                        "plugin_key": "acme.audit",
                        "config": {},
                    }
                ]
            ),
        ),
    )
    with pytest.raises(AgentPresetError) as mode_rejected:
        await agent_preset_service.publish(
            actor=actor(),
            preset_id=wrong_mode.id,
            idempotency_key="publish-wrong-plugin-mode",
            request=AgentPresetCommandRequest(expected_resource_version=1),
        )
    assert mode_rejected.value.details == {"reason": "plugin_runtime_mode_mismatch", "path": "plugins.0"}


@pytest.mark.anyio
async def test_plugin_state_is_rechecked_in_commit_transaction(
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    resolver = AgentPluginSelectionResolver(
        agent_preset_sessions,
        runtime_mode=PluginRuntimeMode.on_demand,
        installed_distributions={},
        installed_top_level_packages=frozenset(),
    )
    selections = preset_config(plugins=[_selection()]).plugins
    async with short_session(agent_preset_sessions) as session:
        prepared = await resolver.prepare(
            session,
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            selections=selections,
        )
    async with transaction(agent_preset_sessions) as session:
        plugin = await session.get(PluginRecord, PLUGIN_ID)
        assert plugin is not None
        plugin.lifecycle_state = "archived"

    with pytest.raises(PluginSelectionError) as changed:
        async with transaction(agent_preset_sessions) as session:
            await resolver.freeze_in_transaction(
                session,
                actor=actor(),
                workspace_id=WORKSPACE_ID,
                prepared=prepared,
            )
    assert changed.value.reason == "plugin_version_unavailable"
