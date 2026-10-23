"""Durable reverse registrations use workspace administration, not Environment mount authority."""

import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.devices import service as devices
from a13n_service.runs.devices.schemas import DeviceCreate
from a13n_service.runs.devices.tables import DeviceRow
from a13n_service.runs.environments.monitoring import collection
from a13n_service.runs.environments.monitoring import proxy as proxy_module
from a13n_service.runs.environments.monitoring.proxy import Proxy, lease_key
from a13n_service.runs.environments.tables import EnvironmentRow
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from .test_metrics_queries import builder
from .test_monitoring_proxy import controlled, fake_observation, local_source

pytestmark = pytest.mark.anyio


def allow_loopback(service, monkeypatch):
    settings = service.runtime.settings.model_copy(
        update={"monitoring": service.runtime.settings.monitoring.model_copy(update={"allow_loopback_ws": True})}
    )
    monkeypatch.setattr(service.app.state, "runtime", replace(service.runtime, settings=settings))


async def registration(service, *, source=None, identity="device-test"):
    response = await service.client.post(
        f"{service.api}/devices",
        json={"name": "Outbound device", "device_id": identity, "metrics_data_source_id": source},
    )
    assert response.status_code == 201, response.text
    return response


async def test_device_registration_credentials_scope_and_source_references(service):
    await controlled(service)
    source = await service.client.post(
        f"{service.api}/metrics-data-sources", json={"name": "Remote cache", "config": {"collection": "proxy"}}
    )
    created = await registration(service, source=source.json()["id"])
    body = created.json()
    item = f"{service.api}/devices/{body['device']['id']}"
    assert created.headers["cache-control"] == "no-store"
    get = await service.client.get(item)
    assert "token" not in get.json() and "token_hash" not in get.json()
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(DeviceRow, body["device"]["id"])
        assert row.token_hash == secret_hash(body["token"])
        assert await session.scalar(select(func.count()).select_from(EnvironmentRow)) == 0
    actor = await builder(service)
    with pytest.raises(ServiceError) as denied:
        await devices.create_device(
            service.runtime, actor, service.tenant.workspace_id, DeviceCreate(name="Forbidden", device_id="another")
        )
    assert denied.value.code == "forbidden"
    assert (
        await devices.get_device(service.runtime, actor, service.tenant.workspace_id, body["device"]["id"])
    ).id == body["device"]["id"]
    blocked = await service.client.delete(
        f"{service.api}/metrics-data-sources/{source.json()['id']}", headers={"if-match": source.headers["etag"]}
    )
    assert blocked.status_code == 409 and blocked.json()["error"]["details"]["reason"] == "data_source_in_use"
    rotated = await service.client.post(f"{item}/rotate-credential", headers={"if-match": get.headers["etag"]})
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["token"] != body["token"]
    with pytest.raises(ServiceError):
        await devices.authenticate(service.runtime, body["device"]["id"], body["token"])
    assert await devices.authenticate(service.runtime, body["device"]["id"], rotated.json()["token"])
    stale = await service.client.patch(item, json={"enabled": False}, headers={"if-match": get.headers["etag"]})
    assert stale.status_code == 412
    disabled = await service.client.patch(item, json={"enabled": False}, headers={"if-match": rotated.headers["etag"]})
    assert disabled.status_code == 200
    with pytest.raises(ServiceError):
        await devices.authenticate(service.runtime, body["device"]["id"], rotated.json()["token"])
    assert (await service.client.delete(item, headers={"if-match": disabled.headers["etag"]})).status_code == 204
    assert (
        await service.client.delete(
            f"{service.api}/metrics-data-sources/{source.json()['id']}", headers={"if-match": source.headers["etag"]}
        )
    ).status_code == 204


async def test_reverse_owner_capacity_and_admission_fence(service):
    proxy = await controlled(service, max_devices=1)
    source = await local_source(service)
    first = (await registration(service, source=source)).json()["device"]["id"]
    second = (await registration(service, source=source, identity="other-device")).json()["device"]["id"]
    selected = await collection.resolve(service.runtime, first)
    other = await collection.resolve(service.runtime, second)
    peer = Proxy(replace(service.runtime, metrics_proxy=None))
    async with proxy.reverse_connection(selected):
        with pytest.raises(ServiceError):
            async with peer.reverse_connection(selected):
                pytest.fail("Two replicas admitted the same Device")
        with pytest.raises(ServiceError):
            async with proxy.reverse_connection(other):
                pytest.fail("Pending handshakes must consume capacity")
        assert first in proxy.reverse
    assert not proxy.entries and not proxy.reverse
    current = await service.client.get(f"{service.api}/devices/{first}")
    rotated = await service.client.post(
        f"{service.api}/devices/{first}/rotate-credential", headers={"if-match": current.headers["etag"]}
    )
    assert rotated.status_code == 200
    with pytest.raises(ServiceError):
        async with proxy.reverse_connection(selected):
            pytest.fail("Admission must recheck credentials after acquiring ownership")
    assert not proxy.entries and not proxy.reverse


async def test_unconfigured_device_has_no_collection_and_duplicate_identity_is_refused(service):
    proxy = await controlled(service)
    result = await registration(service)
    identity = result.json()["device"]["id"]
    assert await collection.resolve(service.runtime, identity) is None
    await proxy.tick()
    assert not proxy.entries
    duplicate = await service.client.post(
        f"{service.api}/devices", json={"name": "Duplicate", "device_id": "device-test"}
    )
    assert duplicate.status_code == 409
    assert "token" not in json.dumps((await service.client.get(f"{service.api}/devices")).json())


@pytest.mark.parametrize("stage", ["resolve", "renew", "renew_lost", "redis_error", "observe"])
async def test_previous_reverse_poll_cannot_retire_replacement(service, monkeypatch, stage):
    proxy = await controlled(service)
    identity = (await registration(service, source=await local_source(service))).json()["device"]["id"]
    selection = await collection.resolve(service.runtime, identity)
    entered, release = asyncio.Event(), asyncio.Event()
    original_resolve, original_eval = collection.resolve, service.runtime.redis.eval
    observed = AsyncMock(return_value=fake_observation())
    monkeypatch.setattr(proxy_module, "observe", observed)
    old_context = proxy.reverse_connection(selection)
    old_connection = await old_context.__aenter__()
    old = proxy.entries[identity]

    async def gate():
        entered.set()
        await release.wait()

    async def resolve(runtime, target_id):
        selected = await original_resolve(runtime, target_id)
        if stage == "resolve" and not entered.is_set():
            await gate()
            return None
        return selected

    async def evaluate(script, count, key, token, *args):
        result = await original_eval(script, count, key, token, *args)
        if script == proxy_module._RENEW and token == old.lease and stage.startswith(("renew", "redis")):
            await gate()
            if stage == "redis_error":
                raise RedisConnectionError("Coordination unavailable")
            return 0 if stage == "renew_lost" else result
        return result

    async def observe(connection, device_id):
        assert connection is old_connection
        await gate()
        return fake_observation()

    monkeypatch.setattr(collection, "resolve", resolve)
    monkeypatch.setattr(service.runtime.redis, "eval", evaluate)
    if stage == "observe":
        monkeypatch.setattr(proxy_module, "observe", observe)
    poll = asyncio.create_task(proxy.poll(identity))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        await old_context.__aexit__(None, None, None)
        async with proxy.reverse_connection(selection) as replacement:
            close = AsyncMock(wraps=replacement.close)
            monkeypatch.setattr(replacement, "close", close)
            fresh = proxy.entries[identity]
            release.set()
            await asyncio.wait_for(poll, timeout=3)
            assert proxy.entries[identity] is fresh
            assert proxy.reverse[identity] is replacement
            assert await service.runtime.redis.get(lease_key(identity)) == fresh.lease
            assert fresh.cache.report is None
            close.assert_not_awaited()
            if stage != "observe":
                observed.assert_not_awaited()
    finally:
        release.set()
        poll.cancel()
        await asyncio.gather(poll, return_exceptions=True)
        await old_context.__aexit__(None, None, None)
    assert not proxy.entries and not proxy.reverse


@pytest.mark.parametrize("reason", ["owner_loss", "credential_rotation", "source_disabled"])
async def test_reverse_renewal_retires_revoked_attachment_without_polling(service, monkeypatch, reason):
    proxy = await controlled(service)
    created = await service.client.post(
        f"{service.api}/metrics-data-sources", json={"name": "Revocable", "config": {"collection": "proxy"}}
    )
    assert created.status_code == 201, created.text
    source = created.json()["id"]
    registered = await registration(service, source=source)
    identity = registered.json()["device"]["id"]
    selected = await collection.resolve(service.runtime, identity)
    proxy.config = proxy.config.model_copy(update={"poll_seconds": 0.01})
    closed = asyncio.Event()
    async with proxy.reverse_connection(selected) as connection:
        original_close = connection.close

        async def close():
            await original_close()
            closed.set()

        monkeypatch.setattr(connection, "close", close)
        if reason == "owner_loss":
            await service.runtime.redis.set(lease_key(identity), "replacement-owner", px=30_000)
        elif reason == "credential_rotation":
            result = await service.client.post(
                f"{service.api}/devices/{identity}/rotate-credential",
                headers={"if-match": registered.headers["etag"]},
            )
            assert result.status_code == 200, result.text
        else:
            changed = await service.client.patch(
                f"{service.api}/metrics-data-sources/{source}",
                json={"enabled": False},
                headers={"if-match": created.headers["etag"]},
            )
            assert changed.status_code == 200, changed.text
        renewal = asyncio.create_task(proxy.renew())
        try:
            await asyncio.wait_for(closed.wait(), timeout=3)
            assert identity not in proxy.entries and identity not in proxy.reverse
            lease = await service.runtime.redis.get(lease_key(identity))
            assert lease == ("replacement-owner" if reason == "owner_loss" else None)
        finally:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)


@pytest.mark.parametrize("outcome", ["recover", "expire", "owner_loss"])
async def test_reverse_database_grace_preserves_carrier_until_recovery_or_retirement(service, monkeypatch, outcome):
    proxy = await controlled(service)
    identity = (await registration(service, source=await local_source(service))).json()["device"]["id"]
    selection = await collection.resolve(service.runtime, identity)
    monkeypatch.setattr(proxy_module, "observe", AsyncMock(return_value=fake_observation()))
    async with proxy.reverse_connection(selection) as connection:
        await proxy.poll(identity)
        entry = proxy.entries[identity]
        close = AsyncMock(wraps=connection.close)
        monkeypatch.setattr(connection, "close", close)
        now = 100.0
        monkeypatch.setattr(proxy_module, "time", SimpleNamespace(monotonic=lambda: now, time=time.time))
        original = collection.resolve
        monkeypatch.setattr(
            collection, "resolve", AsyncMock(side_effect=OperationalError("SELECT", {}, RuntimeError("offline")))
        )
        await proxy.poll(identity)
        assert proxy.reverse[identity] is connection
        assert entry.cache.status == "offline"
        checked = asyncio.Event()
        original_resolve = proxy.resolve

        async def resolve(target_id, expected):
            try:
                return await original_resolve(target_id, expected)
            finally:
                checked.set()

        monkeypatch.setattr(proxy, "resolve", resolve)
        proxy.config = proxy.config.model_copy(update={"poll_seconds": 0.01})
        renewal = asyncio.create_task(proxy.renew())
        try:
            await asyncio.wait_for(checked.wait(), timeout=3)
            assert proxy.entries[identity] is entry
            close.assert_not_awaited()
            if outcome == "recover":
                monkeypatch.setattr(collection, "resolve", original)
                checked.clear()
                await asyncio.wait_for(checked.wait(), timeout=3)
                assert proxy.entries[identity] is entry
                assert entry.authority_failed_at is None
                close.assert_not_awaited()
            else:
                retired = asyncio.Event()

                async def closed():
                    await close()
                    retired.set()

                monkeypatch.setattr(connection, "close", closed)
                if outcome == "expire":
                    now += 3 * proxy.config.poll_seconds + 1
                else:
                    await service.runtime.redis.set(lease_key(identity), "replacement-owner")
                await asyncio.wait_for(retired.wait(), timeout=3)
                assert identity not in proxy.entries and identity not in proxy.reverse
                close.assert_awaited_once()
                assert await service.runtime.redis.get(lease_key(identity)) == (
                    "replacement-owner" if outcome == "owner_loss" else None
                )
        finally:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)


async def test_previous_renewal_cannot_retire_replacement_after_eligibility_read(service, monkeypatch):
    proxy = await controlled(service)
    identity = (await registration(service, source=await local_source(service))).json()["device"]["id"]
    selection = await collection.resolve(service.runtime, identity)
    entered, release = asyncio.Event(), asyncio.Event()
    original_resolve = collection.resolve

    async def resolve(runtime, target_id):
        if not entered.is_set():
            entered.set()
            await release.wait()
            return None
        return await original_resolve(runtime, target_id)

    old_context = proxy.reverse_connection(selection)
    await old_context.__aenter__()
    monkeypatch.setattr(collection, "resolve", resolve)
    proxy.config = proxy.config.model_copy(update={"poll_seconds": 0.01})
    renewal = asyncio.create_task(proxy.renew())
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        await old_context.__aexit__(None, None, None)
        async with proxy.reverse_connection(selection) as replacement:
            fresh = proxy.entries[identity]
            close = AsyncMock(wraps=replacement.close)
            monkeypatch.setattr(replacement, "close", close)
            original_drop = proxy.drop
            checked = asyncio.Event()

            async def drop(target_id, *, expected):
                await original_drop(target_id, expected=expected)
                checked.set()

            monkeypatch.setattr(proxy, "drop", drop)
            release.set()
            await asyncio.wait_for(checked.wait(), timeout=3)
            assert proxy.entries[identity] is fresh
            assert await service.runtime.redis.get(lease_key(identity)) == fresh.lease
            close.assert_not_awaited()
    finally:
        release.set()
        renewal.cancel()
        await asyncio.gather(renewal, return_exceptions=True)
        await old_context.__aexit__(None, None, None)


async def test_reverse_websocket_rejects_non_device_auth_and_browser_inputs(service, monkeypatch):
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    from .conftest import listening

    proxy = await controlled(service)
    registered = (await registration(service, source=await local_source(service))).json()
    identity, token = registered["device"]["id"], registered["token"]
    async with listening(service.app) as endpoint:
        url = endpoint.replace("http://", "ws://") + f"/api/v1/devices/{identity}/connect"
        with pytest.raises(InvalidStatus) as denied:
            async with connect(
                url,
                additional_headers={"authorization": f"Bearer {token}"},
                subprotocols=["eip.v1"],
            ):
                pytest.fail("Plaintext loopback admission must require explicit development consent")
        assert denied.value.response.status_code == 403
        assert not proxy.entries
        allow_loopback(service, monkeypatch)
        cases = [
            ({}, "", ["eip.v1"]),
            ({"authorization": "Bearer wrong"}, "", ["eip.v1"]),
            ({"cookie": f"device={token}"}, "", ["eip.v1"]),
            ({"authorization": f"Bearer {token}"}, "?token=unused", ["eip.v1"]),
            ({"authorization": f"Bearer {token}", "origin": "https://example.com"}, "", ["eip.v1"]),
            ({"authorization": f"Bearer {token}"}, "", []),
            ([("authorization", f"Bearer {token}"), ("authorization", f"Bearer {token}")], "", ["eip.v1"]),
        ]
        for headers, query, protocols in cases:
            with pytest.raises(InvalidStatus) as denied:
                async with connect(
                    url + query, additional_headers=headers, subprotocols=protocols or None, compression=None
                ):
                    pytest.fail("Invalid carrier admission succeeded")
            assert denied.value.response.status_code == 403
            assert not proxy.entries and not proxy.reverse
            assert await service.runtime.redis.get(lease_key(identity)) is None
        response = await service.client.get(
            f"{service.api}/devices/{identity}", headers={"authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401


@pytest.mark.parametrize("development", [False, True])
async def test_reverse_initialization_deadline_releases_slot_and_lease(service, monkeypatch, development):
    from websockets.asyncio.client import connect

    from .conftest import listening

    proxy = await controlled(service)
    proxy.config = proxy.config.model_copy(update={"request_seconds": 0.1})
    if development:
        allow_loopback(service, monkeypatch)
    # The loopback test server trusts its loopback peer's forwarded scheme, as a TLS proxy would.
    headers = {} if development else {"x-forwarded-proto": "https"}
    registered = (await registration(service, source=await local_source(service))).json()
    identity, token = registered["device"]["id"], registered["token"]
    async with listening(service.app) as endpoint:
        url = endpoint.replace("http://", "ws://") + f"/api/v1/devices/{identity}/connect"
        async with connect(
            url, subprotocols=["eip.v1"], additional_headers={"authorization": f"bearer {token}", **headers}
        ) as socket:
            request = json.loads(await socket.recv())
            assert request["method"] == "initialize" and "eip_session" not in request
            await asyncio.wait_for(socket.wait_closed(), timeout=3)
        # Dropping removes the entry before it awaits the lease release.
        async with asyncio.timeout(3):
            while identity in proxy.entries or await service.runtime.redis.get(lease_key(identity)) is not None:
                await asyncio.sleep(0.01)
        assert not proxy.reverse


async def test_device_history_is_workspace_scoped_and_independent_of_proxy_capacity(service):
    from a13n_service.runs.devices.query import query_device

    from .test_metrics_queries import backend, create_source
    from .test_monitoring_proxy import query

    proxy = await controlled(service, max_devices=1)
    async with backend() as (endpoint, calls):
        source = await create_source(service, endpoint)
        registered = (await registration(service, source=source.json()["id"])).json()
        identity = registered["device"]["id"]
        actor = await builder(service)
        result = await query_device(service.runtime, actor, service.tenant.workspace_id, identity, query())
        assert result.data.result == []
        assert calls[0][2]["query"] == ['envd_system_cpu_busy_ratio{device_id="device-test",tenant="ours"}']
        assert not proxy.entries
        other = await service.client.post(f"{service.organization}/workspaces", json={"name": "Other"})
        assert other.status_code == 201, other.text
        response = await service.client.post(
            f"/api/v1/workspaces/{other.json()['id']}/devices/{identity}/metrics/query", json=query().model_dump()
        )
        assert response.status_code == 404
        assert len(calls) == 1


async def test_source_changed_to_pull_retires_reverse_carrier_and_refuses_typed_history(service):
    from .test_monitoring_proxy import query

    proxy = await controlled(service)
    source = await service.client.post(
        f"{service.api}/metrics-data-sources", json={"name": "Mutable", "config": {"collection": "proxy"}}
    )
    assert source.status_code == 201, source.text
    registered = await registration(service, source=source.json()["id"])
    identity = registered.json()["device"]["id"]
    selected = await collection.resolve(service.runtime, identity)
    async with proxy.reverse_connection(selected):
        changed = await service.client.patch(
            f"{service.api}/metrics-data-sources/{source.json()['id']}",
            json={"config": {"collection": "pull", "collector": "vmagent"}},
            headers={"if-match": source.headers["etag"]},
        )
        assert changed.status_code == 200, changed.text
        await proxy.poll(identity)
        assert identity not in proxy.entries and identity not in proxy.reverse
        result = await service.client.post(f"{service.api}/devices/{identity}/metrics/query", json=query().model_dump())
        assert result.status_code == 409
        assert result.json()["error"]["details"]["reason"] == "reverse_devices_require_non_pull_source"
        rejected = await service.client.post(
            f"{service.api}/devices",
            json={"name": "Cannot scrape", "device_id": "new-device", "metrics_data_source_id": source.json()["id"]},
        )
        assert rejected.status_code == 400


async def test_device_rename_preserves_stream_but_rotation_changes_revision(service, monkeypatch):
    proxy = await controlled(service)
    created = await registration(service, source=await local_source(service))
    identity = created.json()["device"]["id"]
    selection = await collection.resolve(service.runtime, identity)
    monkeypatch.setattr(proxy_module, "observe", AsyncMock(return_value=fake_observation()))
    async with proxy.reverse_connection(selection):
        await proxy.poll(identity)
        entry = proxy.entries[identity]
        path = f"{service.api}/devices/{identity}"
        renamed = await service.client.patch(
            path, json={"name": "Renamed device"}, headers={"if-match": created.headers["etag"]}
        )
        assert renamed.status_code == 200, renamed.text
        await proxy.poll(identity)
        assert proxy.entries[identity] is entry
        assert await proxy.current(entry)
        assert entry.cache.status == "ready"
        rotated = await service.client.post(f"{path}/rotate-credential", headers={"if-match": renamed.headers["etag"]})
        assert rotated.status_code == 200, rotated.text
        assert not await proxy.current(entry)
        assert (await collection.resolve(service.runtime, identity)).revision != selection.revision
        await proxy.poll(identity)
        assert identity not in proxy.entries


@pytest.mark.parametrize(
    "scheme,host,development,expected",
    [
        ("wss", "192.0.2.10", False, True),
        ("ws", "127.0.0.1", False, False),
        ("ws", "::1", False, False),
        ("ws", "127.0.0.1", True, True),
        ("ws", "::1", True, True),
        ("ws", "192.0.2.10", True, False),
    ],
)
def test_reverse_transport_requires_wss_unless_explicit_loopback_development(scheme, host, development, expected):
    from a13n_service.runs.devices.transport import secure
    from starlette.websockets import WebSocket

    socket = WebSocket(
        {
            "type": "websocket",
            "scheme": scheme,
            "path": "/connect",
            "query_string": b"",
            "headers": [(b"x-forwarded-proto", b"https")],
            "client": (host, 1234),
            "server": ("service.example", 80),
        },
        AsyncMock(),
        AsyncMock(),
    )
    assert secure(socket, allow_loopback_ws=development) is expected
