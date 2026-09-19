"""Device discovery uses the shared finite relay without a Run or Session."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.websocket.authority import DeviceReadIdentity
from a13n_service.environments.websocket.device_reads import DeviceReadClient
from a13n_service.environments.websocket.relay_runtime import RelayResponseRuntime
from a13n_service.environments.websocket.relay_storage import ConnectionRelayStore, RelayStoreError
from a13n_service.environments.websocket.worker_connections import WorkerClientConnections
from a13n_service.storage.config import RedisServerConfig
from a13n_service.storage.redis import open_redis

from ..conftest import actor
from .test_connection_host import binding_directories as binding_directories
from .test_connection_host import daemon, online
from .test_connection_host import host_server as host_server

pytestmark = pytest.mark.anyio


@pytest.fixture
async def device_reads(environment_service, relay_redis, redis_url):
    async with open_redis(RedisServerConfig(url=redis_url, max_connections=1)) as reader:
        responses = RelayResponseRuntime(relay_redis, reader, "origin-device")
        await responses.prepare()
        environment_service.devices.relay = DeviceReadClient(relay_redis, responses)
        receiving = asyncio.create_task(responses.run())
        try:
            yield responses
        finally:
            await responses.close()
            await receiving


async def test_native_device_info_and_paged_browse_do_not_acquire_use(
    envd_binary, host_server, target, relay_redis, tmp_path, environment_service, device_reads, monkeypatch
):
    _, service, url, authorized = host_server
    ticket = await service.issue_ticket(actor(), target.environment_id)
    requests = []
    append = ConnectionRelayStore.append

    async def capture(store, request):
        assert environment_service.sessions.kw["bind"].sync_engine.pool.checkedout() == 0
        assert isinstance(request.scope, DeviceReadIdentity)
        requests.append(request)
        return await append(store, request)

    monkeypatch.setattr(ConnectionRelayStore, "append", capture)
    async with daemon(envd_binary, tmp_path / "browse", url, ticket.ticket, target.device_id) as (_, root):
        await online(service, target, ticket.connection_id)
        for name in ("a", "b", "c"):
            (root / name).mkdir()
        (root / "file.txt").write_text("not a directory")
        info = await environment_service.device_info(actor=actor(), environment_id=target.environment_id)
        assert info.default_working_directory == str(root)
        first = await environment_service.device_directories(
            actor=actor(), environment_id=target.environment_id, limit=2
        )
        assert [entry.name for entry in first.entries] == ["a", "b"]
        assert first.next_offset == 2
        second = await environment_service.device_directories(
            actor=actor(), environment_id=target.environment_id, path=str(root), offset=first.next_offset, limit=2
        )
        assert [entry.name for entry in second.entries] == ["c"]
        assert second.next_offset is None
        with pytest.raises(EnvironmentManagementError) as missing:
            await environment_service.device_directories(
                actor=actor(), environment_id=target.environment_id, path=str(root / "absent")
            )
        assert missing.value.code == "environment_directory_unavailable"
        assert not authorized
        observed = await service.observe(target.organization_id, target.environment_id)
        assert observed.value.status == "online" and not observed.value.uses
        assert not device_reads.responses._pending
        assert {request.operation for request in requests} == {"device.describe", "directory.list"}
        assert all(request.scope.principal == actor().principal for request in requests)


async def test_lost_device_publication_reply_reuses_identity_and_worker_close_keeps_reader(
    envd_binary, host_server, target, relay_redis, tmp_path, environment_service, device_reads, monkeypatch
):
    _, service, url, _ = host_server
    ticket = await service.issue_ticket(actor(), target.environment_id)
    append = ConnectionRelayStore.append
    requests = []

    async def lose_first_reply(store, request):
        requests.append(request)
        result = await append(store, request)
        if len(requests) == 1:
            raise RelayStoreError("relay_unavailable")
        return result

    monkeypatch.setattr(ConnectionRelayStore, "append", lose_first_reply)
    async with daemon(envd_binary, tmp_path / "dedup", url, ticket.ticket, target.device_id) as (_, root):
        await online(service, target, ticket.connection_id)
        worker = WorkerClientConnections(relay_redis, device_reads)
        await worker.close()
        info = await environment_service.device_info(actor=actor(), environment_id=target.environment_id)
        assert info.default_working_directory == str(root)
        assert len(requests) == 2 and requests[0] == requests[1]
        assert not device_reads.is_closed()
        assert not device_reads.responses._pending


async def test_cancelling_device_read_releases_waiter_without_creating_use(
    envd_binary, host_server, target, tmp_path, environment_service, device_reads, monkeypatch
):
    _, service, url, authorized = host_server
    ticket = await service.issue_ticket(actor(), target.environment_id)
    entered = asyncio.Event()

    async def stalled_append(store, request):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ConnectionRelayStore, "append", stalled_append)
    async with daemon(envd_binary, tmp_path / "cancel", url, ticket.ticket, target.device_id):
        await online(service, target, ticket.connection_id)
        reading = asyncio.create_task(
            environment_service.device_info(actor=actor(), environment_id=target.environment_id)
        )
        await asyncio.wait_for(entered.wait(), 2)
        reading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reading
        assert not device_reads.responses._pending
        assert not authorized
        assert not (await service.observe(target.organization_id, target.environment_id)).value.uses


async def test_offline_device_read_never_publishes(environment_service, target, device_reads, monkeypatch):
    append = AsyncMock(side_effect=AssertionError("offline Device was published"))
    monkeypatch.setattr(ConnectionRelayStore, "append", append)
    with pytest.raises(EnvironmentManagementError) as error:
        await environment_service.device_info(actor=actor(), environment_id=target.environment_id)
    assert error.value.code == "environment_unavailable"
    append.assert_not_awaited()
