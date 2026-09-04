from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version as distribution_version

import pytest
from a13n_service.agents.application import AgentManagement
from a13n_service.agents.domain import (
    AgentRunOverride,
    CreateAgentRequest,
    CreateAgentRevisionRequest,
    DuplicateAgentRequest,
    PluginRuntimeMode,
    RestoreAgentRevisionRequest,
)
from a13n_service.agents.errors import AgentError
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.plugin_resolution import AgentPluginSelectionResolver, PluginSelectionError
from a13n_service.agents.resolution import AgentResolver
from a13n_service.etags import resource_etag
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.plugins.models import (
    PluginRecord,
    PluginRuntimeLockRecord,
    PluginRuntimeStateRecord,
    PluginVersionRecord,
)
from a13n_service.plugins.runtime import PluginRuntimeLock
from a13n_service.storage import short_session, transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, USER_ID, WORKSPACE_ID, actor, agent_config, create_current_revision

PLUGIN_ID = "plg_1234567890abcdef"
PLUGIN_VERSION_ID = "plgv_1234567890abcdef"
PLUGIN_DIGEST = "a" * 64


@dataclass(frozen=True, slots=True)
class _RuntimePlugin:
    plugin_id: str
    plugin_version_id: str
    plugin_key: str
    distribution_name: str
    version: str
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
                archived_at=NOW if lifecycle_state == "archived" else None,
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
) -> tuple[AgentManagement, AgentInvocationResolver, AgentResolver, AgentPluginSelectionResolver]:
    model_selector = AcceptedModelSelector(
        sessions,
        built_in_provider_registry(),
    )
    plugin_resolver = AgentPluginSelectionResolver(
        sessions,
        runtime_mode=PluginRuntimeMode.runner,
        installed_distributions={},
        installed_top_level_packages=frozenset(),
        worker_release="runner-test-worker",
        harness_version="runner-test-harness",
    )
    revision_resolver = AgentResolver(
        sessions,
        model_selector,
        plugin_runtime_mode=PluginRuntimeMode.runner,
        plugin_resolver=plugin_resolver,
    )
    invocation = AgentInvocationResolver(
        sessions,
        model_selector,
        plugin_runtime_mode=PluginRuntimeMode.runner,
        plugin_resolver=plugin_resolver,
    )
    return (
        AgentManagement(sessions, revision_resolver, invocation, clock=lambda: NOW),
        invocation,
        revision_resolver,
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
                    version=plugin_version.version,
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
                    runtime_generation=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        else:
            state.active_lock_digest = runtime_lock.digest
            state.runtime_generation += 1
            state.updated_at = NOW
        return runtime_lock.digest


@pytest.mark.anyio
async def test_create_revision_freezes_exact_plugin_version(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions, requires_dist=("packaging>=25,<26",))
    agent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-plugin-agent",
        request=CreateAgentRequest(
            name="Plugin Agent",
            config=agent_config(plugins=[_selection()]),
        ),
    )

    revision_result = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=agent.agent.id,
        idempotency_key="create_revision-plugin-agent",
        request=CreateAgentRevisionRequest(expected_version=1, config=agent.revision.config),
    )

    assert revision_result.revision.plugin_runtime_mode is PluginRuntimeMode.on_demand
    assert revision_result.revision.resolved_plugin_versions[0].model_dump(mode="json") == {
        "instance_name": "audit",
        "plugin_id": PLUGIN_ID,
        "plugin_version_id": PLUGIN_VERSION_ID,
        "plugin_key": "acme.audit",
        "distribution_name": "acme-audit",
        "version": "1.2.3",
        "top_level_package": "acme_audit",
        "wheel_digest": PLUGIN_DIGEST,
        "config": {"level": "strict"},
    }
    assert len(revision_result.revision.runtime_lock_digest) == 64
    async with short_session(agent_sessions) as session:
        record = await session.get(PluginRuntimeLockRecord, revision_result.revision.runtime_lock_digest)
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
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    agent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-plugin-override",
        request=CreateAgentRequest(name="Plugin Override", config=agent_config()),
    )
    revision_result = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=agent.agent.id,
        idempotency_key="create_revision-plugin-override",
        request=CreateAgentRevisionRequest(expected_version=1, config=agent.revision.config),
    )

    prepared = await agent_invocation_resolver.preparation.prepare(
        actor=actor(),
        agent_id=agent.agent.id,
        agent_revision_id=revision_result.revision.id,
        config_override=AgentRunOverride.model_validate({"plugins": [_selection()]}),
    )
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)

    assert frozen.effective_config.resolved_plugin_versions[0].plugin_version_id == PLUGIN_VERSION_ID
    assert frozen.effective_config.runtime_lock_digest != revision_result.revision.runtime_lock_digest


@pytest.mark.anyio
async def test_run_override_plugin_config_reuses_exact_runtime_lock(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    agent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-plugin-config-override",
        request=CreateAgentRequest(
            name="Plugin Config Override",
            config=agent_config(plugins=[_selection()]),
        ),
    )
    revision_result = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=agent.agent.id,
        idempotency_key="create_revision-plugin-config-override",
        request=CreateAgentRevisionRequest(expected_version=1, config=agent.revision.config),
    )

    prepared = await agent_invocation_resolver.preparation.prepare(
        actor=actor(),
        agent_id=agent.agent.id,
        agent_revision_id=revision_result.revision.id,
        config_override=AgentRunOverride.model_validate(
            {"plugins": [_selection(config={"level": "lenient"})]},
        ),
    )
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)

    assert frozen.effective_config.resolved_plugin_versions[0].config == {"level": "lenient"}
    assert frozen.effective_config.runtime_lock_digest == revision_result.revision.runtime_lock_digest


@pytest.mark.anyio
async def test_create_revision_composes_subagent_runtime_lock_and_rejects_version_conflict(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    child = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-plugin-lock-child",
        request=CreateAgentRequest(
            name="Plugin Lock Child",
            config=agent_config(plugins=[_selection()]),
        ),
    )
    child_revision, _ = await create_current_revision(
        agent_management,
        agent_id=child.agent.id,
        expected_version=1,
        key="plugin-lock-child",
    )
    parent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-plugin-lock-parent",
        request=CreateAgentRequest(
            name="Plugin Lock Parent",
            config=agent_config(subagents={"child": {"agent_id": child.agent.id, "environment": {"mode": "none"}}}),
        ),
    )
    parent_revision = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=parent.agent.id,
        idempotency_key="create_revision-plugin-lock-parent",
        request=CreateAgentRevisionRequest(expected_version=1, config=parent.revision.config),
    )
    async with short_session(agent_sessions) as session:
        parent_record = await session.get(PluginRuntimeLockRecord, parent_revision.revision.runtime_lock_digest)
    assert parent_record is not None
    parent_lock = PluginRuntimeLock.model_validate(parent_record.manifest)
    assert tuple(item.plugin_version_id for item in parent_lock.plugins) == (PLUGIN_VERSION_ID,)
    assert parent_revision.revision.runtime_lock_digest == child_revision.revision.runtime_lock_digest

    conflicting_version_id = "plgv_abcdef1234567890"
    await _add_plugin_version(
        agent_sessions,
        plugin_version_id=conflicting_version_id,
        version="2.0.0",
        digest="b" * 64,
    )
    with pytest.raises(AgentError) as rejected:
        await agent_management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="create-plugin-lock-conflict",
            request=CreateAgentRequest(
                name="Plugin Lock Conflict",
                config=agent_config(
                    plugins=[_selection(plugin_version_id=conflicting_version_id)],
                    subagents={"child": {"agent_id": child.agent.id, "environment": {"mode": "none"}}},
                ),
            ),
        )
    assert rejected.value.code == "agent_revision_create_failed"
    assert rejected.value.details == {"reason": "plugin_dependency_conflict", "path": "plugins"}


@pytest.mark.anyio
async def test_runner_revision_creation_resolves_active_version_and_pins_catalog_lock(
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    service, _invocation, _revision_resolver, plugin_resolver = _runner_services(agent_sessions)
    active_lock_digest = await _set_runner_catalog(
        agent_sessions,
        plugin_resolver,
        plugin_version_id=PLUGIN_VERSION_ID,
    )
    agent = await service.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-runner-plugin",
        request=CreateAgentRequest(
            name="Runner Plugin",
            config=agent_config(plugins=[_runner_selection()]),
        ),
    )

    revision_result = await service.revisions.create_revision(
        actor=actor(),
        agent_id=agent.agent.id,
        idempotency_key="create_revision-runner-plugin",
        request=CreateAgentRevisionRequest(expected_version=1, config=agent.revision.config),
    )

    assert revision_result.revision.plugin_runtime_mode is PluginRuntimeMode.runner
    assert revision_result.revision.resolved_plugin_versions[0].plugin_version_id == PLUGIN_VERSION_ID
    assert revision_result.revision.runtime_lock_digest == active_lock_digest


@pytest.mark.anyio
async def test_runner_revision_creation_requires_committed_active_catalog(
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    service, _invocation, _revision_resolver, _plugin_resolver = _runner_services(agent_sessions)
    async with transaction(agent_sessions) as session:
        session.add(
            PluginRuntimeStateRecord(
                id="runtime",
                mode="runner",
                active_lock_digest=None,
                runtime_generation=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    with pytest.raises(AgentError) as rejected:
        await service.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="create-runner-without-catalog",
            request=CreateAgentRequest(
                name="Runner Without Catalog",
                config=agent_config(plugins=[_runner_selection()]),
            ),
        )

    assert rejected.value.code == "agent_revision_create_failed"
    assert rejected.value.details == {"reason": "plugin_runtime_unavailable", "path": "plugins"}


@pytest.mark.anyio
async def test_runner_historical_revision_keeps_lock_while_new_revision_uses_new_catalog(
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    service, invocation, _revision_resolver, plugin_resolver = _runner_services(agent_sessions)
    first_lock = await _set_runner_catalog(
        agent_sessions,
        plugin_resolver,
        plugin_version_id=PLUGIN_VERSION_ID,
    )
    agent = await service.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-runner-history",
        request=CreateAgentRequest(
            name="Runner History",
            config=agent_config(plugins=[_runner_selection()]),
        ),
    )
    first = await service.revisions.create_revision(
        actor=actor(),
        agent_id=agent.agent.id,
        idempotency_key="create_revision-runner-history-v1",
        request=CreateAgentRevisionRequest(expected_version=1, config=agent.revision.config),
    )
    second_version_id = "plgv_runner2345678901"
    await _add_plugin_version(
        agent_sessions,
        plugin_version_id=second_version_id,
        version="2.0.0",
        digest="c" * 64,
    )
    second_lock = await _set_runner_catalog(
        agent_sessions,
        plugin_resolver,
        plugin_version_id=second_version_id,
    )

    retained = await invocation.preparation.prepare(
        actor=actor(),
        agent_id=agent.agent.id,
        agent_revision_id=first.revision.id,
    )
    async with transaction(agent_sessions) as session:
        frozen = await invocation.freezing.freeze_in_transaction(session, prepared=retained)
    second = await service.revisions.create_revision(
        actor=actor(),
        agent_id=agent.agent.id,
        idempotency_key="create_revision-runner-history-v2",
        request=CreateAgentRevisionRequest(
            expected_version=first.agent.version,
            config=first.revision.config,
        ),
    )

    assert frozen.effective_config.resolved_plugin_versions[0].plugin_version_id == PLUGIN_VERSION_ID
    assert frozen.effective_config.runtime_lock_digest == first_lock
    assert second.revision.resolved_plugin_versions[0].plugin_version_id == second_version_id
    assert second.revision.runtime_lock_digest == second_lock
    assert second_lock != first_lock


@pytest.mark.anyio
async def test_runner_plugin_override_resolves_current_catalog_and_freezes_new_lock(
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    service, invocation, _revision_resolver, plugin_resolver = _runner_services(agent_sessions)
    await _set_runner_catalog(
        agent_sessions,
        plugin_resolver,
        plugin_version_id=PLUGIN_VERSION_ID,
    )
    agent = await service.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-runner-override",
        request=CreateAgentRequest(
            name="Runner Override",
            config=agent_config(plugins=[_runner_selection()]),
        ),
    )
    first = await service.revisions.create_revision(
        actor=actor(),
        agent_id=agent.agent.id,
        idempotency_key="create_revision-runner-override",
        request=CreateAgentRevisionRequest(expected_version=1, config=agent.revision.config),
    )
    second_version_id = "plgv_override234567890"
    await _add_plugin_version(
        agent_sessions,
        plugin_version_id=second_version_id,
        version="2.0.0",
        digest="f" * 64,
    )
    second_lock = await _set_runner_catalog(
        agent_sessions,
        plugin_resolver,
        plugin_version_id=second_version_id,
    )

    prepared = await invocation.preparation.prepare(
        actor=actor(),
        agent_id=agent.agent.id,
        agent_revision_id=first.revision.id,
        config_override=AgentRunOverride.model_validate(
            {"plugins": [_runner_selection(config={"level": "lenient"})]},
        ),
    )
    async with transaction(agent_sessions) as session:
        frozen = await invocation.freezing.freeze_in_transaction(session, prepared=prepared)

    assert first.revision.resolved_plugin_versions[0].plugin_version_id == PLUGIN_VERSION_ID
    assert frozen.effective_config.resolved_plugin_versions[0].plugin_version_id == second_version_id
    assert frozen.effective_config.resolved_plugin_versions[0].config == {"level": "lenient"}
    assert frozen.effective_config.runtime_lock_digest == second_lock


@pytest.mark.anyio
async def test_runner_revision_creation_rechecks_active_catalog_in_commit_transaction(
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    service, _invocation, revision_resolver, plugin_resolver = _runner_services(agent_sessions)
    await _set_runner_catalog(
        agent_sessions,
        plugin_resolver,
        plugin_version_id=PLUGIN_VERSION_ID,
    )
    agent = await service.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-runner-race",
        request=CreateAgentRequest(
            name="Runner Race",
            config=agent_config(plugins=[_runner_selection()]),
        ),
    )
    prepared = await revision_resolver.prepare(
        actor=actor(),
        organization_id=agent.agent.organization_id,
        workspace_id=WORKSPACE_ID,
        agent_id=agent.agent.id,
        config=agent.revision.config,
    )
    second_version_id = "plgv_race234567890123"
    await _add_plugin_version(
        agent_sessions,
        plugin_version_id=second_version_id,
        version="2.0.0",
        digest="d" * 64,
    )
    await _set_runner_catalog(
        agent_sessions,
        plugin_resolver,
        plugin_version_id=second_version_id,
    )

    with pytest.raises(AgentError) as rejected:
        async with transaction(agent_sessions) as session:
            await revision_resolver.freeze_in_transaction(session, prepared=prepared)

    assert rejected.value.code == "agent_revision_create_failed"
    assert rejected.value.details == {"reason": "plugin_version_changed", "path": "plugins"}


@pytest.mark.anyio
async def test_runner_parent_revision_rejects_child_from_different_catalog_lock(
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    service, _invocation, _revision_resolver, plugin_resolver = _runner_services(agent_sessions)
    await _set_runner_catalog(
        agent_sessions,
        plugin_resolver,
        plugin_version_id=PLUGIN_VERSION_ID,
    )
    child = await service.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-runner-child",
        request=CreateAgentRequest(
            name="Runner Child",
            config=agent_config(plugins=[_runner_selection()]),
        ),
    )
    await create_current_revision(
        service,
        agent_id=child.agent.id,
        expected_version=1,
        key="runner-child",
    )
    second_version_id = "plgv_child23456789012"
    await _add_plugin_version(
        agent_sessions,
        plugin_version_id=second_version_id,
        version="2.0.0",
        digest="e" * 64,
    )
    await _set_runner_catalog(
        agent_sessions,
        plugin_resolver,
        plugin_version_id=second_version_id,
    )
    with pytest.raises(AgentError) as rejected:
        await service.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="create-runner-parent",
            request=CreateAgentRequest(
                name="Runner Parent",
                config=agent_config(subagents={"child": {"agent_id": child.agent.id, "environment": {"mode": "none"}}}),
            ),
        )

    assert rejected.value.code == "agent_revision_create_failed"
    assert rejected.value.details == {"reason": "plugin_dependency_conflict", "path": "subagents"}


@pytest.mark.anyio
async def test_retained_revision_keeps_archived_plugin_executable(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    agent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-retained-plugin",
        request=CreateAgentRequest(
            name="Retained Plugin",
            config=agent_config(plugins=[_selection()]),
        ),
    )
    revision_result = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=agent.agent.id,
        idempotency_key="create_revision-retained-plugin",
        request=CreateAgentRevisionRequest(expected_version=1, config=agent.revision.config),
    )
    async with transaction(agent_sessions) as session:
        plugin = await session.get(PluginRecord, PLUGIN_ID)
        assert plugin is not None
        plugin.archived_at = NOW

    prepared = await agent_invocation_resolver.preparation.prepare(
        actor=actor(),
        agent_id=agent.agent.id,
        agent_revision_id=revision_result.revision.id,
    )
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)

    assert frozen.effective_config.resolved_plugin_versions == revision_result.revision.resolved_plugin_versions
    assert frozen.effective_config.runtime_lock_digest == revision_result.revision.runtime_lock_digest


@pytest.mark.anyio
async def test_retained_revision_fails_closed_when_runtime_lock_is_missing(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    agent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-missing-runtime-lock",
        request=CreateAgentRequest(name="Missing Runtime Lock", config=agent_config()),
    )
    revision_result = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=agent.agent.id,
        idempotency_key="create_revision-missing-runtime-lock",
        request=CreateAgentRevisionRequest(expected_version=1, config=agent.revision.config),
    )
    async with transaction(agent_sessions) as session:
        record = await session.get(PluginRuntimeLockRecord, revision_result.revision.runtime_lock_digest)
        assert record is not None
        await session.delete(record)

    prepared = await agent_invocation_resolver.preparation.prepare(
        actor=actor(),
        agent_id=agent.agent.id,
        agent_revision_id=revision_result.revision.id,
    )
    with pytest.raises(AgentError) as rejected:
        async with transaction(agent_sessions) as session:
            await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)

    assert rejected.value.code == "agent_revision_not_executable"
    assert rejected.value.details == {"reason": "plugin_runtime_lock_unavailable"}


@pytest.mark.anyio
async def test_enable_revalidates_retained_plugin_without_following_archive_state(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    agent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-enable-retained-plugin",
        request=CreateAgentRequest(
            name="Enable Retained Plugin",
            config=agent_config(plugins=[_selection()]),
        ),
    )
    _, selected = await create_current_revision(
        agent_management,
        agent_id=agent.agent.id,
        expected_version=1,
        key="enable-retained-plugin",
    )
    disabled = await agent_management.commands.change_lifecycle(
        actor=actor(),
        agent_id=agent.agent.id,
        action="disable",
        idempotency_key="disable-enable-retained-plugin",
        if_match=resource_etag(selected.id, selected.updated_at),
    )
    async with transaction(agent_sessions) as session:
        plugin = await session.get(PluginRecord, PLUGIN_ID)
        assert plugin is not None
        plugin.archived_at = NOW

    enabled = await agent_management.commands.change_lifecycle(
        actor=actor(),
        agent_id=agent.agent.id,
        action="enable",
        idempotency_key="enable-retained-plugin",
        if_match=resource_etag(disabled.id, disabled.updated_at),
    )

    assert enabled.enabled


@pytest.mark.anyio
async def test_enable_rejects_changed_retained_plugin_evidence(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    agent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-changed-retained-plugin",
        request=CreateAgentRequest(
            name="Changed Retained Plugin",
            config=agent_config(plugins=[_selection()]),
        ),
    )
    _, selected = await create_current_revision(
        agent_management,
        agent_id=agent.agent.id,
        expected_version=1,
        key="changed-retained-plugin",
    )
    disabled = await agent_management.commands.change_lifecycle(
        actor=actor(),
        agent_id=agent.agent.id,
        action="disable",
        idempotency_key="disable-changed-retained-plugin",
        if_match=resource_etag(selected.id, selected.updated_at),
    )
    async with transaction(agent_sessions) as session:
        version = await session.get(PluginVersionRecord, PLUGIN_VERSION_ID)
        assert version is not None
        version.content_digest = "b" * 64

    with pytest.raises(AgentError) as rejected:
        await agent_management.commands.change_lifecycle(
            actor=actor(),
            agent_id=agent.agent.id,
            action="enable",
            idempotency_key="enable-changed-retained-plugin",
            if_match=resource_etag(disabled.id, disabled.updated_at),
        )

    current = await agent_management.queries.get(actor=actor(), agent_id=agent.agent.id)
    assert rejected.value.code == "agent_revision_not_executable"
    assert rejected.value.details == {"reason": "plugin_version_changed"}
    assert not current.enabled
    assert current.version == disabled.version


@pytest.mark.anyio
async def test_set_default_reuses_archived_retained_plugin_version(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    agent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-set-default-retained-plugin",
        request=CreateAgentRequest(
            name="Set Default Retained Plugin",
            config=agent_config(plugins=[_selection()]),
        ),
    )
    first = agent
    second = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=agent.agent.id,
        idempotency_key="create-revision-retained-plugin-2",
        request=CreateAgentRevisionRequest(
            expected_version=first.agent.version,
            config=agent_config(plugins=[_selection(config={"level": "lenient"})]),
        ),
    )
    async with transaction(agent_sessions) as session:
        plugin = await session.get(PluginRecord, PLUGIN_ID)
        assert plugin is not None
        plugin.archived_at = NOW

    restored = await agent_management.revisions.restore_revision(
        actor=actor(),
        agent_id=agent.agent.id,
        revision_id=first.revision.id,
        idempotency_key="set-default-retained-plugin",
        request=RestoreAgentRevisionRequest(expected_version=second.agent.version),
    )

    revisions = await agent_management.queries.list_revisions(
        actor=actor(), agent_id=agent.agent.id, limit=10, cursor=None
    )
    assert restored.agent.version == 3
    assert restored.agent.current_revision_id == restored.revision.id
    assert restored.revision.source_revision_id == first.revision.id
    assert len(revisions.items) == 3


@pytest.mark.anyio
async def test_set_default_rejects_changed_retained_plugin_without_committing(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    agent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-invalid-default-plugin",
        request=CreateAgentRequest(
            name="Invalid Default Plugin",
            config=agent_config(plugins=[_selection()]),
        ),
    )
    first = agent
    second = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=agent.agent.id,
        idempotency_key="create-revision-invalid-default-plugin-2",
        request=CreateAgentRevisionRequest(
            expected_version=first.agent.version,
            config=agent_config(plugins=[_selection(config={"level": "lenient"})]),
        ),
    )
    async with transaction(agent_sessions) as session:
        version = await session.get(PluginVersionRecord, PLUGIN_VERSION_ID)
        assert version is not None
        version.content_digest = "b" * 64

    with pytest.raises(AgentError) as rejected:
        await agent_management.revisions.restore_revision(
            actor=actor(),
            agent_id=agent.agent.id,
            revision_id=first.revision.id,
            idempotency_key="set-default-invalid-retained-plugin",
            request=RestoreAgentRevisionRequest(expected_version=second.agent.version),
        )

    current = await agent_management.queries.get(actor=actor(), agent_id=agent.agent.id)
    revisions = await agent_management.queries.list_revisions(
        actor=actor(),
        agent_id=agent.agent.id,
        limit=10,
        cursor=None,
    )
    assert rejected.value.code == "agent_revision_not_executable"
    assert rejected.value.details == {"reason": "plugin_version_changed"}
    assert current.current_revision_id == second.revision.id
    assert current.version == second.agent.version
    assert len(revisions.items) == 2


@pytest.mark.anyio
async def test_duplicate_rejects_changed_retained_plugin_without_creating_copy(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    agent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-invalid-duplicate-plugin",
        request=CreateAgentRequest(
            name="Invalid Duplicate Plugin",
            config=agent_config(plugins=[_selection()]),
        ),
    )
    _, selected = await create_current_revision(
        agent_management,
        agent_id=agent.agent.id,
        expected_version=1,
        key="invalid-duplicate-plugin",
    )
    async with transaction(agent_sessions) as session:
        version = await session.get(PluginVersionRecord, PLUGIN_VERSION_ID)
        assert version is not None
        version.content_digest = "b" * 64

    with pytest.raises(AgentError) as rejected:
        await agent_management.duplication.duplicate(
            actor=actor(),
            agent_id=agent.agent.id,
            idempotency_key="duplicate-invalid-retained-plugin",
            request=DuplicateAgentRequest(
                expected_version=selected.version,
                name="Invalid Duplicate Plugin Copy",
            ),
        )

    agents = await agent_management.queries.list(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        limit=10,
        cursor=None,
        enabled=None,
        source=None,
        include_archived=True,
    )
    assert rejected.value.code == "agent_revision_not_executable"
    assert rejected.value.details == {"reason": "plugin_version_changed"}
    assert tuple(item.name for item in agents.items) == (agent.agent.name,)


@pytest.mark.anyio
async def test_create_revision_rejects_mode_and_worker_dependency_mismatches(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions, requires_dist=("foundation-plugin-missing>=1",))
    with pytest.raises(AgentError) as rejected:
        await agent_management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="create-missing-plugin-dependency",
            request=CreateAgentRequest(
                name="Missing Plugin Dependency",
                config=agent_config(plugins=[_selection()]),
            ),
        )
    assert rejected.value.code == "agent_revision_create_failed"
    assert rejected.value.details == {"reason": "plugin_worker_dependency_missing", "path": "plugins.0"}

    with pytest.raises(AgentError) as mode_rejected:
        await agent_management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="create-wrong-plugin-mode",
            request=CreateAgentRequest(
                name="Wrong Plugin Mode",
                config=agent_config(
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
    assert mode_rejected.value.details == {"reason": "plugin_runtime_mode_mismatch", "path": "plugins.0"}


@pytest.mark.anyio
async def test_plugin_state_is_rechecked_in_commit_transaction(
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    resolver = AgentPluginSelectionResolver(
        agent_sessions,
        runtime_mode=PluginRuntimeMode.on_demand,
        installed_distributions={},
        installed_top_level_packages=frozenset(),
    )
    selections = agent_config(plugins=[_selection()]).plugins
    async with short_session(agent_sessions) as session:
        prepared = await resolver.prepare(
            session,
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            selections=selections,
        )
    async with transaction(agent_sessions) as session:
        plugin = await session.get(PluginRecord, PLUGIN_ID)
        assert plugin is not None
        plugin.archived_at = NOW

    with pytest.raises(PluginSelectionError) as changed:
        async with transaction(agent_sessions) as session:
            await resolver.freeze_in_transaction(
                session,
                actor=actor(),
                workspace_id=WORKSPACE_ID,
                prepared=prepared,
            )
    assert changed.value.reason == "plugin_version_unavailable"


@pytest.mark.anyio
async def test_retained_plugin_evidence_is_rechecked_in_commit_transaction(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _add_plugin(agent_sessions)
    agent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-retained-plugin-race",
        request=CreateAgentRequest(
            name="Retained Plugin Race",
            config=agent_config(plugins=[_selection()]),
        ),
    )
    revision_result = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=agent.agent.id,
        idempotency_key="create_revision-retained-plugin-race",
        request=CreateAgentRevisionRequest(expected_version=1, config=agent.revision.config),
    )
    resolver = AgentPluginSelectionResolver(
        agent_sessions,
        runtime_mode=PluginRuntimeMode.on_demand,
        installed_distributions={},
        installed_top_level_packages=frozenset(),
    )
    async with short_session(agent_sessions) as session:
        prepared = await resolver.prepare_retained(
            session,
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            selections=revision_result.revision.config.plugins,
            resolved=revision_result.revision.resolved_plugin_versions,
            runtime_lock_digest=revision_result.revision.runtime_lock_digest,
        )
    async with transaction(agent_sessions) as session:
        version = await session.get(PluginVersionRecord, PLUGIN_VERSION_ID)
        assert version is not None
        version.content_digest = "b" * 64

    with pytest.raises(PluginSelectionError) as changed:
        async with transaction(agent_sessions) as session:
            await resolver.freeze_in_transaction(
                session,
                actor=actor(),
                workspace_id=WORKSPACE_ID,
                prepared=prepared,
            )
    assert changed.value.reason == "plugin_version_changed"
