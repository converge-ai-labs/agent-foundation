from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version as distribution_version

import pytest
from a13n_service.agent_presets.domain import (
    AgentPresetCommandRequest,
    AgentRunOverride,
    CreateAgentPresetRequest,
    DuplicateAgentPresetRequest,
    PluginRuntimeMode,
    RollbackAgentPresetRequest,
)
from a13n_service.agent_presets.errors import AgentPresetError
from a13n_service.agent_presets.invocation_resolution import AgentPresetInvocationResolver
from a13n_service.agent_presets.plugin_resolution import AgentPluginSelectionResolver, PluginSelectionError
from a13n_service.agent_presets.resolution import AgentPresetResolver
from a13n_service.agent_presets.service import AgentPresetService
from a13n_service.model_configs.endpoint_policy import EndpointPolicy
from a13n_service.model_configs.providers import built_in_provider_registry
from a13n_service.model_configs.runtime import AcceptedModelSelector
from a13n_service.plugins.models import (
    PluginRecord,
    PluginRuntimeLockRecord,
    PluginRuntimeStateRecord,
    PluginVersionRecord,
)
from a13n_service.plugins.runtime import PluginRuntimeLock
from a13n_service.storage import short_session, transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, USER_ID, WORKSPACE_ID, actor, preset_config

PLUGIN_ID = "plg_1234567890abcdef"
PLUGIN_VERSION_ID = "plgv_1234567890abcdef"
PLUGIN_DIGEST = "a" * 64


@dataclass(frozen=True, slots=True)
class _RuntimePlugin:
    plugin_id: str
    plugin_version_id: str
    plugin_key: str
    distribution_name: str
    distribution_version: str
    top_level_package: str
    wheel_digest: str
    artifact_ref: str
    requires_dist: tuple[str, ...]


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


def _selection(
    *,
    instance_name: str = "audit",
    plugin_version_id: str = PLUGIN_VERSION_ID,
    config: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "mode": "on_demand",
        "instance_name": instance_name,
        "plugin_version_id": plugin_version_id,
        "config": {"level": "strict"} if config is None else config,
    }


async def _add_plugin_version(
    sessions: async_sessionmaker[AsyncSession],
    *,
    plugin_version_id: str,
    version: str,
    digest: str,
) -> None:
    async with transaction(sessions) as session:
        session.add(
            PluginVersionRecord(
                id=plugin_version_id,
                plugin_id=PLUGIN_ID,
                version=version,
                content_digest=digest,
                artifact_ref=f"plugins/artifacts/v1/sha256/{digest}.whl",
                size_bytes=1024,
                requires_dist=[],
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


def _runner_selection(*, config: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "mode": "runner",
        "instance_name": "audit",
        "plugin_key": "acme.audit",
        "config": {"level": "strict"} if config is None else config,
    }


def _runner_services(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[AgentPresetService, AgentPresetInvocationResolver, AgentPresetResolver, AgentPluginSelectionResolver]:
    model_selector = AcceptedModelSelector(
        sessions,
        built_in_provider_registry(),
        EndpointPolicy.from_operator_allowlist(private_domains=(), private_cidrs=()),
    )
    plugin_resolver = AgentPluginSelectionResolver(
        sessions,
        runtime_mode=PluginRuntimeMode.runner,
        installed_distributions={},
        installed_top_level_packages=frozenset(),
        worker_release="runner-test-worker",
        harness_version="runner-test-harness",
    )
    publication = AgentPresetResolver(
        sessions,
        model_selector,
        plugin_runtime_mode=PluginRuntimeMode.runner,
        plugin_resolver=plugin_resolver,
    )
    invocation = AgentPresetInvocationResolver(
        sessions,
        model_selector,
        plugin_runtime_mode=PluginRuntimeMode.runner,
        plugin_resolver=plugin_resolver,
    )
    return (
        AgentPresetService(sessions, publication, invocation, clock=lambda: NOW),
        invocation,
        publication,
        plugin_resolver,
    )


async def _set_runner_catalog(
    sessions: async_sessionmaker[AsyncSession],
    resolver: AgentPluginSelectionResolver,
    *,
    plugin_version_id: str,
) -> str:
    async with transaction(sessions) as session:
        plugin = await session.get(PluginRecord, PLUGIN_ID)
        plugin_version = await session.get(PluginVersionRecord, plugin_version_id)
        assert plugin is not None
        assert plugin_version is not None
        runtime_lock = await resolver.runtime_locks.build_and_persist(
            session,
            mode="runner",
            plugins=(
                _RuntimePlugin(
                    plugin_id=plugin.id,
                    plugin_version_id=plugin_version.id,
                    plugin_key=plugin.plugin_key,
                    distribution_name=plugin.distribution_name,
                    distribution_version=plugin_version.version,
                    top_level_package=plugin.top_level_package,
                    wheel_digest=plugin_version.content_digest,
                    artifact_ref=plugin_version.artifact_ref,
                    requires_dist=tuple(plugin_version.requires_dist),
                ),
            ),
        )
        plugin.active_version_id = plugin_version.id
        plugin.updated_at = NOW
        state = await session.get(PluginRuntimeStateRecord, "runtime")
        if state is None:
            session.add(
                PluginRuntimeStateRecord(
                    id="runtime",
                    mode="runner",
                    active_lock_digest=runtime_lock.digest,
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        else:
            state.active_lock_digest = runtime_lock.digest
            state.version += 1
            state.updated_at = NOW
        return runtime_lock.digest


@pytest.mark.anyio
async def test_publish_freezes_exact_plugin_version(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions, requires_dist=("packaging>=25,<26",))
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
    async with short_session(agent_preset_sessions) as session:
        record = await session.get(PluginRuntimeLockRecord, published.revision.runtime_lock_digest)
    assert record is not None
    runtime_lock = PluginRuntimeLock.model_validate(record.manifest)
    assert runtime_lock.computed_digest() == runtime_lock.digest
    assert runtime_lock.mode == "on_demand"
    assert runtime_lock.worker_release == "unknown"
    assert runtime_lock.harness_version
    assert tuple(item.plugin_version_id for item in runtime_lock.plugins) == (PLUGIN_VERSION_ID,)
    assert runtime_lock.distributions[0].model_dump(mode="json") == {
        "distribution_name": "acme-audit",
        "version": "1.2.3",
        "source": "artifact",
        "artifact_digest": PLUGIN_DIGEST,
        "artifact_ref": f"plugins/artifacts/v1/sha256/{PLUGIN_DIGEST}.whl",
    }
    assert runtime_lock.distributions[1].model_dump(mode="json") == {
        "distribution_name": "packaging",
        "version": distribution_version("packaging"),
        "source": "worker_release",
        "artifact_digest": None,
        "artifact_ref": None,
    }


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
async def test_run_override_plugin_config_reuses_exact_runtime_lock(
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-plugin-config-override",
        request=CreateAgentPresetRequest(
            name="Plugin Config Override",
            config=preset_config(plugins=[_selection()]),
        ),
    )
    published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-plugin-config-override",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )

    prepared = await agent_preset_invocation_resolver.prepare(
        actor=actor(),
        agent_preset_id=preset.id,
        config_override=AgentRunOverride.model_validate(
            {"plugins": [_selection(config={"level": "lenient"})]},
        ),
    )
    async with transaction(agent_preset_sessions) as session:
        frozen = await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=prepared)

    assert frozen.effective_config.resolved_plugin_versions[0].config == {"level": "lenient"}
    assert frozen.effective_config.runtime_lock_digest == published.revision.runtime_lock_digest


@pytest.mark.anyio
async def test_publish_composes_subagent_runtime_lock_and_rejects_version_conflict(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    child = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-plugin-lock-child",
        request=CreateAgentPresetRequest(
            name="Plugin Lock Child",
            config=preset_config(plugins=[_selection()]),
        ),
    )
    child_published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=child.id,
        idempotency_key="publish-plugin-lock-child",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    parent = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-plugin-lock-parent",
        request=CreateAgentPresetRequest(
            name="Plugin Lock Parent",
            config=preset_config(subagents={"child": {"agent_preset_id": child.id, "environment": {"mode": "none"}}}),
        ),
    )
    parent_published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=parent.id,
        idempotency_key="publish-plugin-lock-parent",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    async with short_session(agent_preset_sessions) as session:
        parent_record = await session.get(PluginRuntimeLockRecord, parent_published.revision.runtime_lock_digest)
    assert parent_record is not None
    parent_lock = PluginRuntimeLock.model_validate(parent_record.manifest)
    assert tuple(item.plugin_version_id for item in parent_lock.plugins) == (PLUGIN_VERSION_ID,)
    assert parent_published.revision.runtime_lock_digest == child_published.revision.runtime_lock_digest

    conflicting_version_id = "plgv_abcdef1234567890"
    await _add_plugin_version(
        agent_preset_sessions,
        plugin_version_id=conflicting_version_id,
        version="2.0.0",
        digest="b" * 64,
    )
    conflicting = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-plugin-lock-conflict",
        request=CreateAgentPresetRequest(
            name="Plugin Lock Conflict",
            config=preset_config(
                plugins=[_selection(plugin_version_id=conflicting_version_id)],
                subagents={"child": {"agent_preset_id": child.id, "environment": {"mode": "none"}}},
            ),
        ),
    )
    with pytest.raises(AgentPresetError) as rejected:
        await agent_preset_service.publish(
            actor=actor(),
            preset_id=conflicting.id,
            idempotency_key="publish-plugin-lock-conflict",
            request=AgentPresetCommandRequest(expected_resource_version=1),
        )
    assert rejected.value.code == "preset_publish_failed"
    assert rejected.value.details == {"reason": "plugin_dependency_conflict", "path": "plugins"}


@pytest.mark.anyio
async def test_runner_publish_resolves_active_version_and_pins_catalog_lock(
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    service, _invocation, _publication, plugin_resolver = _runner_services(agent_preset_sessions)
    active_lock_digest = await _set_runner_catalog(
        agent_preset_sessions,
        plugin_resolver,
        plugin_version_id=PLUGIN_VERSION_ID,
    )
    preset = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-runner-plugin",
        request=CreateAgentPresetRequest(
            name="Runner Plugin",
            config=preset_config(plugins=[_runner_selection()]),
        ),
    )

    published = await service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-runner-plugin",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )

    assert published.revision.plugin_runtime_mode is PluginRuntimeMode.runner
    assert published.revision.resolved_plugin_versions[0].plugin_version_id == PLUGIN_VERSION_ID
    assert published.revision.runtime_lock_digest == active_lock_digest


@pytest.mark.anyio
async def test_runner_publish_requires_committed_active_catalog(
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    service, _invocation, _publication, _plugin_resolver = _runner_services(agent_preset_sessions)
    async with transaction(agent_preset_sessions) as session:
        session.add(
            PluginRuntimeStateRecord(
                id="runtime",
                mode="runner",
                active_lock_digest=None,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    preset = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-runner-without-catalog",
        request=CreateAgentPresetRequest(
            name="Runner Without Catalog",
            config=preset_config(plugins=[_runner_selection()]),
        ),
    )

    with pytest.raises(AgentPresetError) as rejected:
        await service.publish(
            actor=actor(),
            preset_id=preset.id,
            idempotency_key="publish-runner-without-catalog",
            request=AgentPresetCommandRequest(expected_resource_version=1),
        )

    assert rejected.value.code == "preset_publish_failed"
    assert rejected.value.details == {"reason": "plugin_runtime_unavailable", "path": "plugins"}


@pytest.mark.anyio
async def test_runner_historical_revision_keeps_lock_while_new_publish_uses_new_catalog(
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    service, invocation, _publication, plugin_resolver = _runner_services(agent_preset_sessions)
    first_lock = await _set_runner_catalog(
        agent_preset_sessions,
        plugin_resolver,
        plugin_version_id=PLUGIN_VERSION_ID,
    )
    preset = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-runner-history",
        request=CreateAgentPresetRequest(
            name="Runner History",
            config=preset_config(plugins=[_runner_selection()]),
        ),
    )
    first = await service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-runner-history-v1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    second_version_id = "plgv_runner2345678901"
    await _add_plugin_version(
        agent_preset_sessions,
        plugin_version_id=second_version_id,
        version="2.0.0",
        digest="c" * 64,
    )
    second_lock = await _set_runner_catalog(
        agent_preset_sessions,
        plugin_resolver,
        plugin_version_id=second_version_id,
    )

    retained = await invocation.prepare(
        actor=actor(),
        agent_preset_id=preset.id,
        agent_preset_revision_id=first.revision.id,
    )
    async with transaction(agent_preset_sessions) as session:
        frozen = await invocation.freeze_in_transaction(session, prepared=retained)
    second = await service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-runner-history-v2",
        request=AgentPresetCommandRequest(expected_resource_version=first.preset.resource_version),
    )

    assert frozen.effective_config.resolved_plugin_versions[0].plugin_version_id == PLUGIN_VERSION_ID
    assert frozen.effective_config.runtime_lock_digest == first_lock
    assert second.revision.resolved_plugin_versions[0].plugin_version_id == second_version_id
    assert second.revision.runtime_lock_digest == second_lock
    assert second_lock != first_lock


@pytest.mark.anyio
async def test_runner_plugin_override_resolves_current_catalog_and_freezes_new_lock(
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    service, invocation, _publication, plugin_resolver = _runner_services(agent_preset_sessions)
    await _set_runner_catalog(
        agent_preset_sessions,
        plugin_resolver,
        plugin_version_id=PLUGIN_VERSION_ID,
    )
    preset = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-runner-override",
        request=CreateAgentPresetRequest(
            name="Runner Override",
            config=preset_config(plugins=[_runner_selection()]),
        ),
    )
    first = await service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-runner-override",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    second_version_id = "plgv_override234567890"
    await _add_plugin_version(
        agent_preset_sessions,
        plugin_version_id=second_version_id,
        version="2.0.0",
        digest="f" * 64,
    )
    second_lock = await _set_runner_catalog(
        agent_preset_sessions,
        plugin_resolver,
        plugin_version_id=second_version_id,
    )

    prepared = await invocation.prepare(
        actor=actor(),
        agent_preset_id=preset.id,
        config_override=AgentRunOverride.model_validate(
            {"plugins": [_runner_selection(config={"level": "lenient"})]},
        ),
    )
    async with transaction(agent_preset_sessions) as session:
        frozen = await invocation.freeze_in_transaction(session, prepared=prepared)

    assert first.revision.resolved_plugin_versions[0].plugin_version_id == PLUGIN_VERSION_ID
    assert frozen.effective_config.resolved_plugin_versions[0].plugin_version_id == second_version_id
    assert frozen.effective_config.resolved_plugin_versions[0].config == {"level": "lenient"}
    assert frozen.effective_config.runtime_lock_digest == second_lock


@pytest.mark.anyio
async def test_runner_publish_rechecks_active_catalog_in_commit_transaction(
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    service, _invocation, publication, plugin_resolver = _runner_services(agent_preset_sessions)
    await _set_runner_catalog(
        agent_preset_sessions,
        plugin_resolver,
        plugin_version_id=PLUGIN_VERSION_ID,
    )
    preset = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-runner-race",
        request=CreateAgentPresetRequest(
            name="Runner Race",
            config=preset_config(plugins=[_runner_selection()]),
        ),
    )
    prepared = await publication.prepare(
        actor=actor(),
        organization_id=preset.organization_id,
        workspace_id=WORKSPACE_ID,
        agent_preset_id=preset.id,
        config=preset.config,
    )
    second_version_id = "plgv_race234567890123"
    await _add_plugin_version(
        agent_preset_sessions,
        plugin_version_id=second_version_id,
        version="2.0.0",
        digest="d" * 64,
    )
    await _set_runner_catalog(
        agent_preset_sessions,
        plugin_resolver,
        plugin_version_id=second_version_id,
    )

    with pytest.raises(AgentPresetError) as rejected:
        async with transaction(agent_preset_sessions) as session:
            await publication.freeze_in_transaction(session, prepared=prepared)

    assert rejected.value.code == "preset_publish_failed"
    assert rejected.value.details == {"reason": "plugin_version_changed", "path": "plugins"}


@pytest.mark.anyio
async def test_runner_parent_publish_rejects_child_from_different_catalog_lock(
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    service, _invocation, _publication, plugin_resolver = _runner_services(agent_preset_sessions)
    await _set_runner_catalog(
        agent_preset_sessions,
        plugin_resolver,
        plugin_version_id=PLUGIN_VERSION_ID,
    )
    child = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-runner-child",
        request=CreateAgentPresetRequest(
            name="Runner Child",
            config=preset_config(plugins=[_runner_selection()]),
        ),
    )
    await service.publish(
        actor=actor(),
        preset_id=child.id,
        idempotency_key="publish-runner-child",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    second_version_id = "plgv_child23456789012"
    await _add_plugin_version(
        agent_preset_sessions,
        plugin_version_id=second_version_id,
        version="2.0.0",
        digest="e" * 64,
    )
    await _set_runner_catalog(
        agent_preset_sessions,
        plugin_resolver,
        plugin_version_id=second_version_id,
    )
    parent = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-runner-parent",
        request=CreateAgentPresetRequest(
            name="Runner Parent",
            config=preset_config(subagents={"child": {"agent_preset_id": child.id, "environment": {"mode": "none"}}}),
        ),
    )

    with pytest.raises(AgentPresetError) as rejected:
        await service.publish(
            actor=actor(),
            preset_id=parent.id,
            idempotency_key="publish-runner-parent",
            request=AgentPresetCommandRequest(expected_resource_version=1),
        )

    assert rejected.value.code == "preset_publish_failed"
    assert rejected.value.details == {"reason": "plugin_dependency_conflict", "path": "subagents"}


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
async def test_retained_revision_fails_closed_when_runtime_lock_is_missing(
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-missing-runtime-lock",
        request=CreateAgentPresetRequest(name="Missing Runtime Lock", config=preset_config()),
    )
    published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-missing-runtime-lock",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    async with transaction(agent_preset_sessions) as session:
        record = await session.get(PluginRuntimeLockRecord, published.revision.runtime_lock_digest)
        assert record is not None
        await session.delete(record)

    prepared = await agent_preset_invocation_resolver.prepare(actor=actor(), agent_preset_id=preset.id)
    with pytest.raises(AgentPresetError) as rejected:
        async with transaction(agent_preset_sessions) as session:
            await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=prepared)

    assert rejected.value.code == "preset_revision_not_executable"
    assert rejected.value.details == {"reason": "plugin_runtime_lock_unavailable"}


@pytest.mark.anyio
async def test_enable_revalidates_retained_plugin_without_following_archive_state(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-enable-retained-plugin",
        request=CreateAgentPresetRequest(
            name="Enable Retained Plugin",
            config=preset_config(plugins=[_selection()]),
        ),
    )
    published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-enable-retained-plugin",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    disabled = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=preset.id,
        action="disable",
        idempotency_key="disable-enable-retained-plugin",
        request=AgentPresetCommandRequest(expected_resource_version=published.preset.resource_version),
    )
    async with transaction(agent_preset_sessions) as session:
        plugin = await session.get(PluginRecord, PLUGIN_ID)
        assert plugin is not None
        plugin.lifecycle_state = "archived"

    enabled = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=preset.id,
        action="enable",
        idempotency_key="enable-retained-plugin",
        request=AgentPresetCommandRequest(expected_resource_version=disabled.resource_version),
    )

    assert enabled.lifecycle_state == "enabled"


@pytest.mark.anyio
async def test_enable_rejects_changed_retained_plugin_evidence(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-changed-retained-plugin",
        request=CreateAgentPresetRequest(
            name="Changed Retained Plugin",
            config=preset_config(plugins=[_selection()]),
        ),
    )
    published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-changed-retained-plugin",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    disabled = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=preset.id,
        action="disable",
        idempotency_key="disable-changed-retained-plugin",
        request=AgentPresetCommandRequest(expected_resource_version=published.preset.resource_version),
    )
    async with transaction(agent_preset_sessions) as session:
        version = await session.get(PluginVersionRecord, PLUGIN_VERSION_ID)
        assert version is not None
        version.content_digest = "b" * 64

    with pytest.raises(AgentPresetError) as rejected:
        await agent_preset_service.change_lifecycle(
            actor=actor(),
            preset_id=preset.id,
            action="enable",
            idempotency_key="enable-changed-retained-plugin",
            request=AgentPresetCommandRequest(expected_resource_version=disabled.resource_version),
        )

    current = await agent_preset_service.get(actor=actor(), preset_id=preset.id)
    assert rejected.value.code == "preset_revision_not_executable"
    assert rejected.value.details == {"reason": "plugin_version_changed"}
    assert current.lifecycle_state == "disabled"
    assert current.resource_version == disabled.resource_version


@pytest.mark.anyio
async def test_rollback_reuses_archived_retained_plugin_version(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-rollback-retained-plugin",
        request=CreateAgentPresetRequest(
            name="Rollback Retained Plugin",
            config=preset_config(plugins=[_selection()]),
        ),
    )
    first = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-rollback-retained-plugin-1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    second = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-rollback-retained-plugin-2",
        request=AgentPresetCommandRequest(expected_resource_version=first.preset.resource_version),
    )
    async with transaction(agent_preset_sessions) as session:
        plugin = await session.get(PluginRecord, PLUGIN_ID)
        assert plugin is not None
        plugin.lifecycle_state = "archived"

    rolled_back = await agent_preset_service.rollback(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="rollback-retained-plugin",
        request=RollbackAgentPresetRequest(
            expected_resource_version=second.preset.resource_version,
            source_revision_id=first.revision.id,
        ),
    )

    assert rolled_back.revision.source_revision_id == first.revision.id
    assert rolled_back.revision.resolved_plugin_versions == first.revision.resolved_plugin_versions
    assert rolled_back.revision.runtime_lock_digest == first.revision.runtime_lock_digest


@pytest.mark.anyio
async def test_rollback_rejects_changed_retained_plugin_without_committing(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-invalid-rollback-plugin",
        request=CreateAgentPresetRequest(
            name="Invalid Rollback Plugin",
            config=preset_config(plugins=[_selection()]),
        ),
    )
    first = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-invalid-rollback-plugin-1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    second = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-invalid-rollback-plugin-2",
        request=AgentPresetCommandRequest(expected_resource_version=first.preset.resource_version),
    )
    async with transaction(agent_preset_sessions) as session:
        version = await session.get(PluginVersionRecord, PLUGIN_VERSION_ID)
        assert version is not None
        version.content_digest = "b" * 64

    with pytest.raises(AgentPresetError) as rejected:
        await agent_preset_service.rollback(
            actor=actor(),
            preset_id=preset.id,
            idempotency_key="rollback-invalid-retained-plugin",
            request=RollbackAgentPresetRequest(
                expected_resource_version=second.preset.resource_version,
                source_revision_id=first.revision.id,
            ),
        )

    current = await agent_preset_service.get(actor=actor(), preset_id=preset.id)
    revisions = await agent_preset_service.list_revisions(
        actor=actor(),
        preset_id=preset.id,
        limit=10,
        cursor=None,
    )
    assert rejected.value.code == "preset_revision_not_executable"
    assert rejected.value.details == {"reason": "plugin_version_changed"}
    assert current.active_revision_id == second.revision.id
    assert current.resource_version == second.preset.resource_version
    assert len(revisions.items) == 2


@pytest.mark.anyio
async def test_duplicate_rejects_changed_retained_plugin_without_creating_copy(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-invalid-duplicate-plugin",
        request=CreateAgentPresetRequest(
            name="Invalid Duplicate Plugin",
            config=preset_config(plugins=[_selection()]),
        ),
    )
    published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-invalid-duplicate-plugin",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    async with transaction(agent_preset_sessions) as session:
        version = await session.get(PluginVersionRecord, PLUGIN_VERSION_ID)
        assert version is not None
        version.content_digest = "b" * 64

    with pytest.raises(AgentPresetError) as rejected:
        await agent_preset_service.duplicate(
            actor=actor(),
            preset_id=preset.id,
            idempotency_key="duplicate-invalid-retained-plugin",
            request=DuplicateAgentPresetRequest(
                expected_resource_version=published.preset.resource_version,
                name="Invalid Duplicate Plugin Copy",
            ),
        )

    presets = await agent_preset_service.list(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        limit=10,
        cursor=None,
        lifecycle_state=None,
        source=None,
        include_archived=True,
    )
    assert rejected.value.code == "preset_revision_not_executable"
    assert rejected.value.details == {"reason": "plugin_version_changed"}
    assert tuple(item.name for item in presets.items) == (preset.name,)


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


@pytest.mark.anyio
async def test_retained_plugin_evidence_is_rechecked_in_commit_transaction(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_preset_sessions)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-retained-plugin-race",
        request=CreateAgentPresetRequest(
            name="Retained Plugin Race",
            config=preset_config(plugins=[_selection()]),
        ),
    )
    published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-retained-plugin-race",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    resolver = AgentPluginSelectionResolver(
        agent_preset_sessions,
        runtime_mode=PluginRuntimeMode.on_demand,
        installed_distributions={},
        installed_top_level_packages=frozenset(),
    )
    async with short_session(agent_preset_sessions) as session:
        prepared = await resolver.prepare_retained(
            session,
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            selections=published.revision.config.plugins,
            resolved=published.revision.resolved_plugin_versions,
            runtime_lock_digest=published.revision.runtime_lock_digest,
        )
    async with transaction(agent_preset_sessions) as session:
        version = await session.get(PluginVersionRecord, PLUGIN_VERSION_ID)
        assert version is not None
        version.content_digest = "b" * 64

    with pytest.raises(PluginSelectionError) as changed:
        async with transaction(agent_preset_sessions) as session:
            await resolver.freeze_in_transaction(
                session,
                actor=actor(),
                workspace_id=WORKSPACE_ID,
                prepared=prepared,
            )
    assert changed.value.reason == "plugin_version_changed"
