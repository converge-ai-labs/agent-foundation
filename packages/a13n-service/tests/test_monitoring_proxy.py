"""Proxy ownership, temporary queries and isolation with real PostgreSQL and Redis."""

import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.metrics_data_sources.tables import DataSourceRow
from a13n_service.runs.environments import external
from a13n_service.runs.environments.monitoring import collection
from a13n_service.runs.environments.monitoring import proxy as proxy_module
from a13n_service.runs.environments.monitoring.proxy import Proxy, lease_key
from a13n_service.runs.environments.monitoring.query import EnvironmentQuery, query_environment
from a13n_service.runs.environments.tables import EnvironmentRow
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

from .test_metrics_queries import builder
from .test_monitoring_history import report

pytestmark = pytest.mark.anyio


async def controlled(service, *, max_devices=64):
    tasks = list(service.runtime.tasks.services)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    config = service.runtime.settings.model_copy(
        update={
            "monitoring": service.runtime.settings.monitoring.model_copy(update={"max_devices": max_devices}),
        }
    )
    runtime = replace(service.runtime, settings=config)
    proxy = Proxy(runtime)
    service.runtime = replace(runtime, metrics_proxy=proxy)
    service.app.state.runtime = service.runtime
    return proxy


async def local_source(service):
    response = await service.client.get(f"{service.api}/metrics-data-sources")
    assert response.status_code == 200, response.text
    return next(source["id"] for source in response.json()["items"] if source["builtin"])


async def external_row(service, source_id, *, owner_id=None, endpoint="http://127.0.0.1:9"):
    tenant = service.tenant
    environment_id = new_object_id("env")
    async with transaction(service.runtime.storage) as session:
        row = EnvironmentRow(
            id=environment_id,
            organization_id=tenant.organization_id,
            workspace_id=tenant.workspace_id,
            name="Observed",
            status="ready",
            device_id="device-test",
            endpoint=endpoint,
            owner_principal_id=owner_id or tenant.principal_id,
            created_by_id=tenant.principal_id,
            metrics_data_source_id=source_id,
        )
        row.token = external.seal(service.runtime.keys, row, "test-observation-token")
        session.add(row)
    return environment_id


async def ineligible_rows(service, source_id):
    provider = await service.client.post(
        f"{service.api}/environment-providers",
        json={"name": "Monitoring test", "type": "e2b", "credential": {"api_key": "test-only"}},
    )
    assert provider.status_code == 201, provider.text
    template = await service.client.post(
        f"{service.api}/environment-templates",
        json={
            "name": "Monitoring test",
            "provider_id": provider.json()["id"],
            "config": {"recipe": {"execution": "native"}},
        },
    )
    assert template.status_code == 201, template.text
    ids = []
    async with transaction(service.runtime.storage) as session:
        for index, status in enumerate(("reserved", "stopped", "ready", "ready", "ready")):
            identity = f"env_{index:024d}"
            ids.append(identity)
            session.add(
                EnvironmentRow(
                    id=identity,
                    organization_id=service.tenant.organization_id,
                    workspace_id=service.tenant.workspace_id,
                    name="Ineligible",
                    provider_id=provider.json()["id"],
                    template_id=template.json()["id"],
                    status=status,
                    metrics_data_source_id=source_id,
                    handle=None if status == "reserved" else {"recipe": {"execution": "native"}, "state": None},
                    created_by_id=service.tenant.principal_id,
                )
            )
    return ids


async def test_ineligible_targets_do_not_reserve_proxy_capacity(service, monkeypatch):
    proxy = await controlled(service, max_devices=1)
    source = await local_source(service)
    ineligible = await ineligible_rows(service, source)
    ready = await external_row(service, source)
    read = AsyncMock(return_value=fake_observation())
    monkeypatch.setattr(collection, "read", read)
    candidates = await collection.candidates(service.runtime, after="", limit=64)
    assert not set(ineligible[:2]) & set(candidates)
    for identity in ineligible:
        await proxy.poll(identity)
        assert not proxy.entries
        assert await service.runtime.redis.get(lease_key(identity)) is None
    # The candidate cursor traverses unsupported rows without retaining a slot.
    for _ in range(len(ineligible) + 1):
        await proxy.tick()
    assert set(proxy.entries) == {ready}
    selection = await collection.resolve(service.runtime, ineligible[1])
    result = await proxy.query(selection, query())
    assert result.observation.status == "not_ready"
    selection = await collection.resolve(service.runtime, ready)
    monkeypatch.setattr(
        collection, "resolve", AsyncMock(return_value=replace(selection, target=None, status="not_ready"))
    )
    await proxy.poll(ready)
    assert not proxy.entries
    assert await service.runtime.redis.get(lease_key(ready)) is None


def query():
    now = time.time()
    return EnvironmentQuery(metric="system_cpu_busy_ratio", start=now - 3600, end=now + 1, step=60)


def fake_observation(**kwargs):
    return SimpleNamespace(descriptor=SimpleNamespace(system_info=None), metrics=report(**kwargs))


async def test_private_caches_do_not_merge_self_reported_device_id(service, monkeypatch):
    proxy = await controlled(service)
    source = await local_source(service)
    owner, other = await builder(service), await builder(service)
    first = await external_row(service, source, owner_id=owner.id)
    second = await external_row(service, source, owner_id=other.id)

    async def read(runtime, selection, *, credential_cache=None):
        assert runtime.storage.engine.pool.checkedout() == 0
        return fake_observation(cpu=25 if selection.target_id == first else 75)

    monkeypatch.setattr(collection, "read", read)
    # Isolate pool accounting from another poll's independent short resolution scope.
    await proxy.poll(first)
    await proxy.poll(second)
    assert set(proxy.entries) == {first, second}
    workspace = service.tenant.workspace_id
    a = await query_environment(service.runtime, owner, workspace, first, query())
    b = await query_environment(service.runtime, other, workspace, second, query())
    assert a.history.series[0].points[0][2] == 0.25
    assert b.history.series[0].points[0][2] == 0.75
    with pytest.raises(ServiceError) as error:
        await query_environment(service.runtime, other, workspace, first, query())
    assert error.value.code == "forbidden"
    response = await service.client.post(f"{service.api}/environments/{first}/metrics/query", json=query().model_dump())
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "temporary"
    assert response.json()["history"]["retention_seconds"] == 86400
    assert "test-observation-token" not in response.text


async def test_two_replicas_route_reads_and_fence_owner_takeover(service, monkeypatch):
    first = await controlled(service)
    second = Proxy(service.runtime)
    source = await local_source(service)
    environment = await external_row(service, source)
    read = AsyncMock(return_value=fake_observation())
    monkeypatch.setattr(collection, "read", read)
    await asyncio.gather(first.poll(environment), second.poll(environment))
    assert read.await_count == 1
    owner, reader = (first, second) if first.entries else (second, first)
    selection = await collection.resolve(service.runtime, environment)
    routes = [asyncio.create_task(owner.route()), asyncio.create_task(reader.route())]
    try:
        await asyncio.wait_for(asyncio.gather(owner.ready.wait(), reader.ready.wait()), timeout=3)
        result = await reader.query(selection, query())
        assert result.observation.status == "ready"
        assert len(result.history.series[0].points) == 1
        assert [series.metric for series in result.history.series] == ["system_cpu_busy_ratio"]
        assert result.observation.sample is None and result.observation.age_ms is not None
        snapshot = await reader.query(selection, query().model_copy(update={"include_snapshot": True}))
        assert snapshot.observation.sample is not None
        config = owner.config
        owner.config = config.model_copy(update={"query_bytes": 1024})
        with pytest.raises(ServiceError) as error:
            await reader.query(selection, query().model_copy(update={"include_snapshot": True}))
        assert error.value.code == "payload_too_large"
        owner.config = config
        assert (await reader.query(selection, query())).history.series
        old = owner.entries[environment]
        await service.runtime.redis.delete(lease_key(environment))  # Explicitly simulate expiry/loss.
        await reader.poll(environment)
        assert not await owner.current(old)
        await owner.drop(environment, expected=old)
        assert await service.runtime.redis.get(lease_key(environment)) == reader.entries[environment].lease
        assert not owner.entries
    finally:
        for task in routes:
            task.cancel()
        await asyncio.gather(*routes, return_exceptions=True)


async def test_binding_change_during_poll_discards_in_flight_sample(service, monkeypatch):
    proxy = await controlled(service)
    source = await local_source(service)
    environment = await external_row(service, source)

    async def changed(runtime, selection, *, credential_cache=None):
        assert runtime.storage.engine.pool.checkedout() == 0
        async with transaction(runtime.storage) as session:
            row = await session.get(EnvironmentRow, environment)
            row.metrics_data_source_id = None
        return fake_observation()

    monkeypatch.setattr(collection, "read", changed)
    await proxy.poll(environment)
    assert not proxy.entries
    assert proxy.exporter.health().queued == 0
    assert await service.runtime.redis.get(lease_key(environment)) is None
    assert await collection.candidates(service.runtime, after="", limit=64) == []


@pytest.mark.parametrize("change", ["credentials", "endpoint", "target"])
async def test_collection_changes_fence_exports_but_metadata_preserves_history(service, monkeypatch, change):
    proxy = await controlled(service)
    created = await service.client.post(
        f"{service.api}/metrics-data-sources",
        json={
            "name": "Proxy",
            "config": {"collection": "proxy", "export": {"endpoint": "http://127.0.0.1:9/v1/metrics"}},
            "export_credentials": {"headers": {"authorization": "Bearer write-only"}},
        },
    )
    assert created.status_code == 201, created.text
    source = created.json()["id"]
    environment = await external_row(service, source)
    monkeypatch.setattr(collection, "read", AsyncMock(return_value=fake_observation()))
    await proxy.poll(environment)
    previous = proxy.entries[environment]
    assert proxy.exporter.health().queued == 1
    pending = proxy.exporter.queue.get_nowait()
    proxy.exporter.queue.task_done()
    assert pending.destination.headers == {"authorization": "Bearer write-only"}
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DataSourceRow, source)
        row.name = "Edited"
        row.config = {**row.config, "query": {"endpoint": "http://127.0.0.1:9/query"}}
    assert await pending.current()
    await proxy.poll(environment)
    assert proxy.entries[environment] is previous
    assert len((await proxy.query(previous.selection, query())).history.series[0].points) == 1
    if change == "target":
        async with transaction(service.runtime.storage) as session:
            row = await session.get_one(EnvironmentRow, environment)
            row.endpoint = "http://127.0.0.1:8"
    else:
        path = f"{service.api}/metrics-data-sources/{source}"
        current_source = await service.client.get(path)
        updated = await service.client.patch(
            path,
            headers={"if-match": current_source.headers["etag"]},
            json={"export_credentials": {"headers": {"authorization": "Bearer rotated"}}}
            if change == "credentials"
            else {
                "config": {"collection": "proxy", "export": {"endpoint": "http://127.0.0.1:8/v1/metrics"}},
                "export_credentials": None,
            },
        )
        assert updated.status_code == 200, updated.text
    assert not await pending.current()
    await proxy.poll(environment)
    assert proxy.entries[environment] is not previous
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DataSourceRow, source)
        row.enabled = False
    await proxy.poll(environment)
    assert not proxy.entries
    assert not await pending.current()


async def test_capacity_is_bounded_and_redis_loss_cannot_serve_retained_cache(service, monkeypatch):
    proxy = await controlled(service, max_devices=1)
    source = await local_source(service)
    first, second = await external_row(service, source), await external_row(service, source)
    read = AsyncMock(return_value=fake_observation())
    monkeypatch.setattr(collection, "read", read)
    await proxy.poll(first)
    await proxy.poll(second)
    assert read.await_count == 1 and len(proxy.entries) == 1
    result = await proxy.query(await collection.resolve(service.runtime, second), query())
    assert result.observation.status == "capacity" and not result.history.series
    monkeypatch.setattr(service.runtime.redis, "get", AsyncMock(side_effect=RedisConnectionError()))
    with pytest.raises(ServiceError) as error:
        await proxy.query(await collection.resolve(service.runtime, first), query())
    assert error.value.code == "unavailable"
    assert proxy.query_slots.borrowed_tokens == 0


async def test_duplicate_and_stale_samples_are_not_reexported(service, monkeypatch):
    proxy = await controlled(service)
    source = await local_source(service)
    environment = await external_row(service, source)
    read = AsyncMock(return_value=fake_observation())
    monkeypatch.setattr(collection, "read", read)
    await proxy.poll(environment)
    await proxy.poll(environment)
    entry = proxy.entries[environment]
    result = await proxy.query(entry.selection, query())
    assert len(result.history.series[0].points) == 1
    read.return_value = fake_observation(sequence=2, age=20001)
    await proxy.poll(environment)
    assert (await proxy.query(entry.selection, query())).observation.status == "stale"
    read.side_effect = TimeoutError()
    await proxy.poll(environment)
    assert (await proxy.query(entry.selection, query())).observation.status == "offline"


async def test_selection_failure_retires_only_bad_target_and_preserves_cancellation(service, monkeypatch):
    proxy = await controlled(service)
    source = await local_source(service)
    bad, healthy = await external_row(service, source), await external_row(service, source)
    read = AsyncMock(return_value=fake_observation())
    monkeypatch.setattr(collection, "read", read)
    await proxy.tick()
    old = proxy.entries[bad]
    real_resolve = collection.resolve

    async def resolve(runtime, identity):
        if identity == bad:
            raise ValueError("private-broken-configuration")
        await asyncio.sleep(0)
        return await real_resolve(runtime, identity)

    monkeypatch.setattr(collection, "resolve", resolve)
    read.reset_mock()
    await proxy.tick()
    assert set(proxy.entries) == {healthy}
    assert await service.runtime.redis.get(lease_key(bad)) is None
    assert [call.args[1].target_id for call in read.await_args_list] == [healthy]
    assert not await proxy.current(old)
    assert await proxy.local(bad, old.selection.revision, query()) is None
    monkeypatch.setattr(collection, "resolve", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await proxy.poll(healthy)


@pytest.mark.parametrize("failure_at", ["selection", "publication"])
async def test_transient_database_failure_preserves_history_but_not_authority(service, monkeypatch, failure_at):
    proxy = await controlled(service)
    environment = await external_row(service, await local_source(service))
    read = AsyncMock(return_value=fake_observation())
    monkeypatch.setattr(collection, "read", read)
    await proxy.poll(environment)
    entry = proxy.entries[environment]
    history = entry.cache.read(query(), now=time.time())
    original = collection.resolve
    calls = 0

    async def failed(runtime, identity):
        nonlocal calls
        calls += 1
        if failure_at == "publication" and calls == 1:
            return await original(runtime, identity)
        raise OperationalError("SELECT", {}, RuntimeError("database temporarily unavailable"))

    monkeypatch.setattr(collection, "resolve", failed)
    read.reset_mock()
    read.return_value = fake_observation(sequence=2)
    await proxy.poll(environment)
    assert proxy.entries[environment] is entry
    assert entry.cache.status == "offline"
    assert entry.cache.read(query(), now=time.time()).series == history.series
    assert read.await_count == (1 if failure_at == "publication" else 0)
    assert proxy.exporter.health().queued == 0
    for check in (proxy.current(entry), proxy.local(environment, entry.selection.revision, query())):
        with pytest.raises(OperationalError):
            await check
    monkeypatch.setattr(collection, "resolve", original)
    await proxy.poll(environment)
    assert proxy.entries[environment] is entry
    assert entry.authority_failed_at is None
    assert entry.cache.status == "ready"
    assert entry.cache.read(query(), now=time.time()).series[0].points


async def test_discovery_failure_still_polls_owned_entries_and_renewal_expires_database_grace(service, monkeypatch):
    proxy = await controlled(service)
    environment = await external_row(service, await local_source(service))
    monkeypatch.setattr(collection, "read", AsyncMock(return_value=fake_observation()))
    await proxy.poll(environment)
    entry = proxy.entries[environment]
    now = 100.0
    monkeypatch.setattr(proxy_module, "time", SimpleNamespace(monotonic=lambda: now, time=time.time))
    failure = OperationalError("SELECT", {}, RuntimeError("database unavailable"))
    monkeypatch.setattr(collection, "candidates", AsyncMock(side_effect=failure))
    resolve = AsyncMock(side_effect=failure)
    monkeypatch.setattr(collection, "resolve", resolve)
    await proxy.tick()
    assert proxy.entries[environment] is entry
    assert entry.cache.status == "offline"
    assert entry.authority_failed_at == now
    resolve.assert_awaited_once()
    assert await service.runtime.redis.get(lease_key(environment)) == entry.lease

    # No further polling: independent renewal must expire ordinary entries too.
    proxy.config = proxy.config.model_copy(update={"poll_seconds": 0.01})
    now += 3 * proxy.config.poll_seconds + 1
    retired = asyncio.Event()
    original_drop = proxy.drop

    async def drop(target_id, *, expected):
        await original_drop(target_id, expected=expected)
        retired.set()

    monkeypatch.setattr(proxy, "drop", drop)
    renewal = asyncio.create_task(proxy.renew())
    try:
        await asyncio.wait_for(retired.wait(), timeout=3)
        assert environment not in proxy.entries
        assert await service.runtime.redis.get(lease_key(environment)) is None
        resolve.assert_awaited_once()
    finally:
        renewal.cancel()
        await asyncio.gather(renewal, return_exceptions=True)


@pytest.mark.parametrize("retirement", ["deadline", "lost_lease", "redis_error"])
async def test_database_grace_never_retains_unowned_or_expired_entries(service, monkeypatch, retirement):
    proxy = await controlled(service)
    environment = await external_row(service, await local_source(service))
    monkeypatch.setattr(collection, "read", AsyncMock(return_value=fake_observation()))
    await proxy.poll(environment)
    entry = proxy.entries[environment]
    monkeypatch.setattr(
        collection, "resolve", AsyncMock(side_effect=OperationalError("SELECT", {}, RuntimeError("offline")))
    )
    await proxy.poll(environment)
    assert proxy.entries[environment] is entry
    if retirement == "deadline":
        entry.authority_failed_at = time.monotonic() - 3 * proxy.config.poll_seconds
    elif retirement == "lost_lease":
        await service.runtime.redis.set(lease_key(environment), "replacement-owner")
    else:
        monkeypatch.setattr(service.runtime.redis, "get", AsyncMock(side_effect=RedisConnectionError()))
    await proxy.poll(environment)
    assert environment not in proxy.entries
    if retirement == "lost_lease":
        assert await service.runtime.redis.get(lease_key(environment)) == "replacement-owner"


async def test_proxy_credential_cache_is_per_entry_and_cleared_after_failure(service, monkeypatch):
    from a13n_harness.providers.environment.remote_envd.configuration import HttpEnvdCredential
    from pydantic import SecretStr

    proxy = await controlled(service)
    environment = await external_row(service, await local_source(service))
    seen = []
    fail = False

    async def read(runtime, selection, *, credential_cache):
        seen.append(credential_cache)
        if fail:
            raise TimeoutError()
        credential_cache.credential = HttpEnvdCredential(token=SecretStr("cached-token"))
        return fake_observation()

    monkeypatch.setattr(collection, "read", read)
    await proxy.poll(environment)
    await proxy.poll(environment)
    assert seen[0] is seen[1]
    fail = True
    await proxy.poll(environment)
    assert seen[0].credential is None
    assert proxy.entries[environment].cache.status == "offline"
    fail = False
    await proxy.poll(environment)
    assert seen[-1] is seen[0]
    async with transaction(service.runtime.storage) as session:
        row = await session.get(EnvironmentRow, environment)
        row.endpoint = "http://127.0.0.1:10"
    await proxy.poll(environment)
    assert seen[-1] is not seen[0]
