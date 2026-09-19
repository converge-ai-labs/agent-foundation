"""Durable pairing across Control replicas and the native connect user journey."""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
from datetime import timedelta

import httpx2
import pytest
import uvicorn
from a13n_environment.remote_envd.pairing import PairingPending, PairingRequest, credential_digest, pairing_id
from a13n_service.app import Components, create_app
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.models import DevicePairingRecord, EnvironmentProviderRecord, EnvironmentRecord
from a13n_service.environments.pairing import DevicePairingService
from a13n_service.environments.websocket.coordination import ConnectionCoordination, CoordinationError
from a13n_service.environments.websocket.resources import ConnectionResources
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now
from sqlalchemy import func, select

from ..conftest import ORG_ID, WORKSPACE_ID, actor
from ..test_router import authenticate, seed_database, settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def pairing_service(environment_service, relay_redis):
    return DevicePairingService(
        environment_service, ConnectionCoordination(relay_redis), public_origin="wss://foundation.example"
    )


async def test_pairing_approval_is_atomic_durable_and_idempotent(pairing_service, environment_service, relay_redis):
    credential = secrets.token_hex(32)
    request = PairingRequest(device_id="computer", name="Work computer")
    pending = await pairing_service.pair(credential, request)
    assert isinstance(pending, PairingPending)
    assert credential not in pending.model_dump_json()
    # A different Control process can inspect and approve the same durable request.
    replica = DevicePairingService(
        environment_service, ConnectionCoordination(relay_redis), public_origin="wss://foundation.example"
    )
    challenge = await replica.inspect(actor(), WORKSPACE_ID, pending.challenge.pairing_id)
    assert challenge == pending.challenge
    first, second = await asyncio.gather(
        replica.approve(actor(), WORKSPACE_ID, challenge.pairing_id),
        pairing_service.approve(actor(), WORKSPACE_ID, challenge.pairing_id),
    )
    assert first == second
    assert first.device_registration == "paired" and first.device_id == "computer"
    approved = await replica.pair(credential, request)
    assert approved.status == "approved" and approved.resource_id == first.id
    assert approved.websocket_url == f"wss://foundation.example/api/v1/environments/{first.id}/connect"
    assert credential not in first.model_dump_json() and credential_digest(credential) not in first.model_dump_json()
    async with short_session(environment_service.sessions) as session:
        provider = await session.get(EnvironmentProviderRecord, first.provider_id)
        assert provider.configuration_source == "user" and provider.workspace_id == WORKSPACE_ID
        assert provider.ciphertext is None
        row = await session.get(EnvironmentRecord, first.id)
        assert row.device_credential_digest == credential_digest(credential)
        assert await session.get(DevicePairingRecord, challenge.pairing_id) is None
    assert (await replica.approve(actor(), WORKSPACE_ID, challenge.pairing_id)).id == first.id
    revoked = await replica.revoke(actor(), first.id)
    assert revoked.device_registration == "revoked" and revoked.status == "unavailable"
    assert (await pairing_service.revoke(actor(), first.id)).id == first.id
    with pytest.raises(EnvironmentManagementError) as denied:
        await pairing_service.pair(credential, request)
    assert denied.value.code == "device_pairing_denied"
    with pytest.raises(EnvironmentManagementError):
        await replica.approve(actor(), WORKSPACE_ID, challenge.pairing_id)
    with pytest.raises(EnvironmentManagementError):
        await ConnectionResources(environment_service).resolve(first.id)
    # A fresh credential cannot take over or revive the old installation identity.
    replacement = await pairing_service.pair(secrets.token_hex(32), request)
    with pytest.raises(EnvironmentManagementError) as conflict:
        await replica.approve(actor(), WORKSPACE_ID, replacement.challenge.pairing_id)
    assert conflict.value.code == "environment_target_conflict"


async def test_pending_pairings_are_bounded_immutable_and_expire(pairing_service, environment_service, monkeypatch):
    monkeypatch.setattr("a13n_service.environments.pairing.MAX_PENDING_PAIRINGS", 2)
    credential = secrets.token_hex(32)
    request = PairingRequest(device_id="one", name="Computer")
    first = await pairing_service.pair(credential, request)
    assert first == await pairing_service.pair(credential, request)
    with pytest.raises(EnvironmentManagementError):
        await pairing_service.pair(credential, request.model_copy(update={"name": "Changed"}))
    await pairing_service.reject(actor(), WORKSPACE_ID, first.challenge.pairing_id)
    with pytest.raises(EnvironmentManagementError) as rejected:
        await pairing_service.pair(credential, request)
    assert rejected.value.code == "device_pairing_denied"
    await pairing_service.pair(secrets.token_hex(32), PairingRequest(device_id="two", name="Two"))
    with pytest.raises(EnvironmentManagementError) as capacity:
        await pairing_service.pair(secrets.token_hex(32), PairingRequest(device_id="three", name="Three"))
    assert capacity.value.code == "device_pairing_capacity"
    async with transaction(environment_service.sessions) as session:
        row = await session.get(DevicePairingRecord, first.challenge.pairing_id)
        row.expires_at = utc_now() - timedelta(seconds=1)
    renewed = await pairing_service.pair(credential, request)
    assert renewed.challenge.expires_at > first.challenge.expires_at
    async with short_session(environment_service.sessions) as session:
        assert await session.scalar(select(func.count()).select_from(DevicePairingRecord)) == 2


async def test_durable_revoke_survives_coordination_failure(pairing_service, environment_service, monkeypatch):
    credential = secrets.token_hex(32)
    request = PairingRequest(device_id="computer", name="Computer")
    pending = await pairing_service.pair(credential, request)
    registered = await pairing_service.approve(actor(), WORKSPACE_ID, pending.challenge.pairing_id)

    async def unavailable(*args):
        raise CoordinationError("coordination_unavailable")

    monkeypatch.setattr(pairing_service._coordination, "revoke", unavailable)
    assert (await pairing_service.revoke(actor(), registered.id)).device_registration == "revoked"
    with pytest.raises(EnvironmentManagementError):
        await ConnectionResources(environment_service).capture(ORG_ID, registered.id)


@pytest.fixture(params=["control", "all"])
async def paired_api(request, tmp_path, service_database, redis_url):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        values = settings(tmp_path, service_database).model_dump()
        values["service"]["role"] = request.param
        # First-party defaults enable WebSocket and derive its URL from the
        # trusted public origin; no Provider or ticket controller is pre-created.
        values["environments"] = {}
        values["iam"]["public_origin"] = origin
        values["redis"] = {"backend": "redis", "url": redis_url}
        config = Settings.model_validate(values)
        await seed_database(config)
        app = create_app(config, components=Components(request_authenticator=authenticate))
        async with app.router.lifespan_context(app):
            server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off", access_log=False))
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                async with asyncio.timeout(5):
                    while not server.started:
                        assert not serving.done()
                        await asyncio.sleep(0.01)
                async with httpx2.AsyncClient(base_url=origin) as client:
                    yield app, client, origin
            finally:
                app.state.runtime.begin_drain()
                server.should_exit = True
                await asyncio.wait_for(serving, 5)


async def test_pairing_http_keeps_workspace_authority_and_narrow_input(paired_api):
    _, client, _ = paired_api
    body = {"device_id": "computer", "name": "Computer"}
    assert (await client.post("/api/envd/pair", json=body)).status_code == 401
    headers = {"Authorization": f"Bearer {secrets.token_hex(32)}"}
    assert (await client.post("/api/envd/pair?token=invalid", json=body, headers=headers)).status_code == 401
    assert (await client.post("/api/envd/pair", content=b" " * 4097, headers=headers)).status_code == 413
    assert (
        await client.post("/api/envd/pair", json={**body, "workspace_id": WORKSPACE_ID}, headers=headers)
    ).status_code == 422
    response = await client.post("/api/envd/pair", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    pairing = response.json()["challenge"]["pairing_id"]
    base = f"/api/v1/workspaces/{WORKSPACE_ID}/device-pairings"
    assert (await client.get(base)).status_code == 404  # No cross-tenant pending directory.
    assert (await client.get(f"{base}/{pairing}")).status_code == 200
    assert (
        await client.post(f"{base}/{pairing}/approve", headers={"x-test-user": "usr_missing123456789"})
    ).status_code in {403, 404}
    approved = await client.post(f"{base}/{pairing}/approve")
    assert approved.status_code == 200, approved.text
    resource = approved.json()
    assert resource["device_registration"] == "paired"
    path = f"/api/v1/environments/{resource['id']}"
    assert (await client.post(f"{path}/connection-tickets")).status_code == 422
    assert (await client.post(f"{path}/revoke-device")).json()["device_registration"] == "revoked"
    assert (await client.post("/api/envd/pair", json=body, headers=headers)).status_code == 403


async def test_native_connect_approve_discover_restart_and_revoke(paired_api, envd_binary, tmp_path):
    app, client, origin = paired_api
    directory = tmp_path / "device-work"
    directory.mkdir()
    (directory / "project").mkdir()
    state = tmp_path / "device-state"
    env = {key: value for key, value in os.environ.items() if not key.startswith("A13N_ENVD_")}
    env["A13N_ENVD_FULL_CONTROL"] = "1"
    command = [
        str(envd_binary),
        "connect",
        origin,
        "--host",
        "service",
        "--state-dir",
        str(state),
        "--name",
        "Test computer",
        "--default-working-directory",
        str(directory),
    ]
    logs = []
    process = None
    try:
        process = await asyncio.create_subprocess_exec(*command, env=env, stderr=asyncio.subprocess.PIPE)
        async with asyncio.timeout(10):
            while True:
                line = await process.stderr.readline()
                assert line, logs
                logs.append(line)
                if b"Open:" in line:
                    break
        credential = (state / "instances/default/hosts/service/credential").read_text().strip()
        pairing = pairing_id(credential_digest(credential))
        assert pairing.encode() in b"".join(logs)
        base = f"/api/v1/workspaces/{WORKSPACE_ID}/device-pairings/{pairing}"
        challenge = (await client.get(base)).json()
        assert challenge["verification_code"].encode() in b"".join(logs)
        approved = await client.post(f"{base}/approve")
        assert approved.status_code == 200, approved.text
        resource = approved.json()
        path = f"/api/v1/environments/{resource['id']}"

        async def online():
            async with asyncio.timeout(12):
                while True:
                    status = await client.get(f"{path}/connection")
                    assert status.status_code == 200, status.text
                    if status.json()["status"] == "online":
                        return status.json()["connection_id"]
                    assert process.returncode is None, logs
                    await asyncio.sleep(0.05)

        first = await online()
        descriptor = await client.get(f"{path}/device")
        assert descriptor.status_code == 200, descriptor.text
        assert descriptor.json()["default_working_directory"] == str(directory)
        listing = await client.get(f"{path}/directories")
        assert listing.status_code == 200, listing.text
        assert "project" in listing.text
        process.terminate()
        _, output = await asyncio.wait_for(process.communicate(), 5)
        logs.append(output)
        # Saved Host retry does not create a new pending request or credential.
        process = await asyncio.create_subprocess_exec(
            str(envd_binary), "connect", "service", "--state-dir", str(state), env=env, stderr=asyncio.subprocess.PIPE
        )
        second = await online()
        # Wait for the new native carrier, not the disconnecting old observation.
        async with asyncio.timeout(12):
            while second == first:
                await asyncio.sleep(0.05)
                second = await online()
        assert (state / "instances/default/hosts/service/credential").read_text().strip() == credential
        response = await client.post(f"{path}/revoke-device")
        assert response.status_code == 200, response.text
        _, output = await asyncio.wait_for(process.communicate(), 8)
        logs.append(output)
        assert process.returncode == 1
        assert (await client.get(f"{path}/connection")).json()["status"] == "offline"
        assert (await client.get(path)).json()["device_registration"] == "revoked"
        assert credential.encode() not in b"".join(logs)
        assert await app.state.runtime.shared.storage.redis.ping()
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.communicate(), 5)
