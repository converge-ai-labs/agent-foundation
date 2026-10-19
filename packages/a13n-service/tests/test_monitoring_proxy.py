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
from a13n_service.runs.environments.monitoring.proxy import Proxy, lease_key
from a13n_service.runs.environments.monitoring.query import EnvironmentQuery, query_environment
from a13n_service.runs.environments.tables import EnvironmentRow
from redis.exceptions import ConnectionError as RedisConnectionError

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

    async def read(runtime, selection):
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

    async def changed(runtime, selection):
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


async def test_source_rotation_fences_queued_export_and_starts_new_cache(service, monkeypatch):
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
