from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest
from a13n_service.plugins.models import PluginRuntimeStateRecord
from a13n_service.plugins.runtime import (
    LockedDistribution,
    PluginRuntimeLockError,
    PluginRuntimeLockStore,
    WorkerReleaseManifest,
    default_runtime_target,
)
from a13n_service.storage import short_session, transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW


@dataclass(frozen=True, slots=True)
class _Plugin:
    plugin_id: str
    plugin_version_id: str
    plugin_key: str
    distribution_name: str
    version: str
    top_level_package: str
    wheel_digest: str
    artifact_ref: str
    requires_dist: tuple[str, ...]


def _plugin(
    suffix: str,
    *,
    distribution_name: str | None = None,
    version: str = "1.0.0",
    requires_dist: tuple[str, ...] = (),
) -> _Plugin:
    digest = hashlib.sha256(suffix.encode()).hexdigest()
    return _Plugin(
        plugin_id=f"plg_{suffix:0<16}",
        plugin_version_id=f"plgv_{suffix:0<16}",
        plugin_key=f"acme.{suffix}",
        distribution_name=distribution_name or f"acme-{suffix}",
        version=version,
        top_level_package=f"acme_{suffix}",
        wheel_digest=digest,
        artifact_ref=f"plugins/artifacts/v1/sha256/{digest}.whl",
        requires_dist=requires_dist,
    )


def _store() -> PluginRuntimeLockStore:
    return PluginRuntimeLockStore(
        WorkerReleaseManifest(
            worker_release="test-worker",
            harness_version="test-harness",
            runtime_target=default_runtime_target(),
            distributions={},
        ),
        clock=lambda: NOW,
    )


@pytest.mark.anyio
async def test_empty_runner_catalog_bootstrap_is_idempotent(plugin_sessions, runner_plugin_service):
    store = _store()
    async with transaction(plugin_sessions) as session:
        await store.initialize_empty_runner_catalog(session)
    async with short_session(plugin_sessions) as session:
        initial = await session.get(PluginRuntimeStateRecord, "runtime")
        digest = initial.active_lock_digest
        lock = await store.require(session, digest, mode="runner")
        assert lock.plugins == ()
    async with transaction(plugin_sessions) as session:
        await store.initialize_empty_runner_catalog(session)
    async with short_session(plugin_sessions) as session:
        restarted = await session.get(PluginRuntimeStateRecord, "runtime")
        assert restarted.active_lock_digest == digest
        assert restarted.runtime_generation == 1


@pytest.mark.anyio
@pytest.mark.parametrize("existing", ["head", "command", "generation"])
async def test_empty_runner_catalog_does_not_overwrite_durable_state(plugin_sessions, runner_plugin_service, existing):
    async with transaction(plugin_sessions) as session:
        state = await session.get(PluginRuntimeStateRecord, "runtime")
        if existing == "head":
            state.active_lock_digest = "a" * 64
        elif existing == "command":
            state.command_operation_id = "operation-pending"
            state.command_lease_expires_at = NOW
        else:
            state.runtime_generation = 2
    if existing == "generation":
        with pytest.raises(PluginRuntimeLockError, match="plugin_runtime_lock_unavailable"):
            async with transaction(plugin_sessions) as session:
                await _store().initialize_empty_runner_catalog(session)
    else:
        async with transaction(plugin_sessions) as session:
            await _store().initialize_empty_runner_catalog(session)
    async with short_session(plugin_sessions) as session:
        state = await session.get(PluginRuntimeStateRecord, "runtime")
        assert state.active_lock_digest == ("a" * 64 if existing == "head" else None)


@pytest.mark.anyio
async def test_runner_lock_accepts_resolved_dependency_artifact(
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    dependency = LockedDistribution(
        distribution_name="httpx",
        version="1.2.3",
        source="artifact",
        artifact_digest="b" * 64,
        artifact_ref=f"plugins/artifacts/v1/sha256/{'b' * 64}.whl",
    )
    async with transaction(plugin_sessions) as session:
        runtime_lock = await _store().build_and_persist(
            session,
            mode="runner",
            plugins=(_plugin("audit", requires_dist=("httpx>=1",)),),
            locked_distributions=(dependency,),
        )

    assert dependency in runtime_lock.distributions


@pytest.mark.anyio
async def test_runner_lock_resolves_plugin_to_plugin_requirement_independent_of_order(
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    consumer = _plugin("consumer", requires_dist=("acme-provider>=2",))
    provider = _plugin("provider", distribution_name="acme-provider", version="2.1.0")
    async with transaction(plugin_sessions) as session:
        runtime_lock = await _store().build_and_persist(
            session,
            mode="runner",
            plugins=(consumer, provider),
        )

    assert {item.plugin_id for item in runtime_lock.plugins} == {consumer.plugin_id, provider.plugin_id}


@pytest.mark.anyio
async def test_runner_lock_rejects_resolved_dependency_outside_plugin_constraint(
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    dependency = LockedDistribution(
        distribution_name="httpx",
        version="1.0.0",
        source="artifact",
        artifact_digest="b" * 64,
        artifact_ref=f"plugins/artifacts/v1/sha256/{'b' * 64}.whl",
    )
    with pytest.raises(PluginRuntimeLockError, match="plugin_dependency_conflict"):
        async with transaction(plugin_sessions) as session:
            await _store().build_and_persist(
                session,
                mode="runner",
                plugins=(_plugin("audit", requires_dist=("httpx>=2",)),),
                locked_distributions=(dependency,),
            )
