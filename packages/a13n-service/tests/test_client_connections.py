"""Reverse carrier admission, initialization and ownership on separate Control instances."""

import asyncio
import json

import pytest
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.environments.clients.connections import Connections, owner_key, presence_key
from a13n_service.runs.environments.clients.service import authenticate
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from .conftest import listening
from .test_client_environments import register

pytestmark = pytest.mark.anyio


async def test_cross_replica_connection_is_exclusive_and_old_cleanup_preserves_replacement(service):
    registered = (await register(service)).json()
    identity = registered["client"]["environment"]["id"]
    attachment = await authenticate(service.runtime, identity, registered["token"])
    first, second = Connections(service.runtime), Connections(service.runtime)
    # Explicitly losing the owner key stands for expiration without waiting for TTL.
    async with first.reserve(attachment) as old:
        assert await first.presence(identity) is None
        with pytest.raises(ServiceError) as busy:
            async with second.reserve(attachment):
                pytest.fail("Second Control accepted an owned connection")
        assert busy.value.code == "conflict"
        assert not second.entries
        await service.runtime.redis.delete(owner_key(identity))
        replacement = second.reserve(attachment)
        new = await replacement.__aenter__()
        assert new.epoch != old.epoch
        with pytest.raises(ServiceError):
            await first.current(old)
    try:
        assert await service.runtime.redis.get(owner_key(identity)) == new.epoch
        await second.current(new)
    finally:
        await replacement.__aexit__(None, None, None)
    assert await service.runtime.redis.get(owner_key(identity)) is None
    assert await service.runtime.redis.get(presence_key(identity)) is None


async def test_initialization_timeout_does_not_publish_online_and_releases_connection(service, runs_kit):
    await runs_kit.pause_sweeps(service)
    pool = service.runtime.client_connections
    pool.config = pool.config.model_copy(update={"initialization_seconds": 1})
    created = (await register(service)).json()
    identity, token = created["client"]["environment"]["id"], created["token"]
    async with listening(service.app) as endpoint:
        url = endpoint.replace("http://", "ws://") + created["connection_path"]
        async with connect(
            url,
            subprotocols=["eip.v1"],
            additional_headers={
                "authorization": f"Bearer {token}",
                "x-forwarded-proto": "https",
            },
        ) as socket:
            request = json.loads(await socket.recv())
            assert request["method"] == "initialize" and "eip_session" not in request
            assert request["params"]["expected_device_id"] == "laptop"
            assert await pool.presence(identity) is None
            listed = (await service.client.get(f"{service.api}/environments")).json()["items"]
            assert listed[0]["client_connection"]["state"] == "connecting"
            assert listed[0]["client_connection"]["observed_at"]
            assert service.runtime.storage.engine.pool.checkedout() == 0
            await asyncio.wait_for(socket.wait_closed(), timeout=3)
        async with asyncio.timeout(3):
            while identity in pool.entries or await service.runtime.redis.get(owner_key(identity)) is not None:
                await asyncio.sleep(0.01)
        assert await pool.presence(identity) is None


@pytest.mark.parametrize("recovering", [False, True])
async def test_temporary_connection_rejection_remains_retryable(service, monkeypatch, recovering):
    created = (await register(service)).json()
    pool = service.runtime.client_connections
    if recovering:
        monkeypatch.setattr(pool, "recovering", True)
    else:
        monkeypatch.setattr(pool, "config", pool.config.model_copy(update={"max_connections": 0}))
    async with listening(service.app) as endpoint:
        with pytest.raises(InvalidStatus) as failed:
            async with connect(
                endpoint.replace("http://", "ws://") + created["connection_path"],
                subprotocols=["eip.v1"],
                additional_headers={"authorization": f"Bearer {created['token']}", "x-forwarded-proto": "https"},
            ):
                pytest.fail("Unavailable Control accepted a connection")
        assert failed.value.response.status_code == 503
        assert failed.value.response.headers["Retry-After"] == "1"
    assert not pool.entries


@pytest.mark.parametrize(
    "invalid", ["missing-token", "wrong-token", "origin", "query-token", "plaintext", "subprotocol"]
)
async def test_execution_carrier_rejects_browser_and_unbound_authentication(service, invalid):
    created = (await register(service)).json()
    headers = {"authorization": f"Bearer {created['token']}", "x-forwarded-proto": "https"}
    protocols = ["eip.v1"]
    suffix = ""
    if invalid == "missing-token":
        headers.pop("authorization")
    elif invalid == "wrong-token":
        headers["authorization"] = "Bearer wrong"
    elif invalid == "origin":
        headers["origin"] = "https://example.com"
    elif invalid == "query-token":
        suffix = "?token=test-token"
    elif invalid == "plaintext":
        headers.pop("x-forwarded-proto")
    else:
        protocols = []
    async with listening(service.app) as endpoint:
        url = endpoint.replace("http://", "ws://") + created["connection_path"] + suffix
        with pytest.raises(InvalidStatus):
            async with connect(url, subprotocols=protocols, additional_headers=headers):
                pytest.fail("Invalid connection was upgraded")
    assert not service.runtime.client_connections.entries
