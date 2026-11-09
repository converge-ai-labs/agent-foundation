"""Real HTTP approval, credential confinement and replay/expiry behavior for native enrollment."""

import asyncio
from dataclasses import replace

import httpx2
import pytest
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.environments.clients import pairing
from a13n_service.runs.environments.clients import service as clients
from a13n_service.runs.environments.clients.tables import ClientSettingsRow
from a13n_service.tenancy.access import principal_for
from a13n_service.tenancy.authorize import WorkspaceScope

from .test_metrics_queries import builder

pytestmark = pytest.mark.anyio
TOKEN = "a" * 64
DEVICE = {"device_id": "dev-laptop", "name": "My laptop", "default_working_directory": "/work", "computer_use": True}


async def create(service):
    response = await service.client.post(f"{service.api}/client-environments/pairings")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json(), f"{service.api}/client-environments/pairings/{response.json()['id']}"


async def claim(service, path, token=TOKEN, device=None, **headers):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test"
    ) as native:
        return await native.post(
            path + "/claim", json=device or DEVICE, headers={"x-a13n-device-credential": f"Bearer {token}", **headers}
        )


async def actor(service):
    async with short_session(service.runtime.storage) as session:
        return await principal_for(session, service.runtime.access, service.tenant.principal_id)


async def test_approval_registers_device_owned_credential_and_retries_without_rotation(service):
    invitation, path = await create(service)
    first = await claim(service, path)
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "pending"
    assert (await service.client.get(f"{service.api}/environments")).json()["items"] == []
    observation = await service.client.get(path)
    assert observation.json()["device"]["name"] == "My laptop"
    assert "token_hash" not in observation.text and TOKEN not in observation.text
    raw = await service.runtime.redis.get(pairing.key(invitation["id"]))
    assert TOKEN not in raw and secret_hash(TOKEN) in raw
    assert 0 < await service.runtime.redis.ttl(pairing.key(invitation["id"])) <= 600
    wrong = await service.client.post(path + "/approve", json={"verification_code": "wrong"})
    assert wrong.status_code == 409
    approved = await service.client.post(
        path + "/approve", json={"verification_code": first.json()["verification_code"]}
    )
    assert approved.status_code == 200, approved.text
    identity = approved.json()["environment_id"]
    repeated = await service.client.post(
        path + "/approve", json={"verification_code": first.json()["verification_code"]}
    )
    assert repeated.status_code == 200 and repeated.json()["environment_id"] == identity
    for _ in range(2):
        response = await claim(service, path)
        assert response.json()["environment_id"] == identity
        assert response.json()["connection_path"] == f"/api/v1/client-environments/{identity}/connect"
    attachment = await clients.authenticate(service.runtime, identity, TOKEN)
    assert attachment.device_id == DEVICE["device_id"]
    async with short_session(service.runtime.storage) as session:
        config = await session.get_one(ClientSettingsRow, identity)
        assert config.token_hash == secret_hash(TOKEN) and config.credential_version == 1
        assert "environment.computer.observe" in config.permission_ceiling["operations"]
    # A fresh invitation with the retained credential reconnects without issuing another token.
    _, second_path = await create(service)
    second = await claim(service, second_path)
    again = await service.client.post(
        second_path + "/approve", json={"verification_code": second.json()["verification_code"]}
    )
    assert again.status_code == 200 and again.json()["environment_id"] == identity
    current = await service.client.get(f"{service.api}/client-environments/{identity}")
    revoked = await service.client.post(
        f"{service.api}/client-environments/{identity}/revoke-credential",
        headers={"if-match": current.headers["etag"]},
    )
    assert revoked.status_code == 200
    assert (await claim(service, second_path)).status_code == 401


@pytest.mark.parametrize("transport", ["http", "query", "duplicate_header"])
async def test_native_claim_refuses_unsafe_credential_transport(service, transport):
    _, path = await create(service)
    headers = [("x-a13n-device-credential", f"Bearer {TOKEN}")]
    if transport == "duplicate_header":
        headers.append(headers[0])
    base = "http://service.test" if transport == "http" else "https://service.test"
    suffix = "?credential=not-accepted" if transport == "query" else ""
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app, client=("192.0.2.1", 1234)), base_url=base
    ) as native:
        response = await native.post(path + "/claim" + suffix, json=DEVICE, headers=headers)
    assert response.status_code == 401
    assert (await service.client.get(path)).json()["state"] == "waiting"


async def test_claim_is_exclusive_and_browser_or_other_credentials_cannot_take_over(service):
    _, path = await create(service)
    responses = await asyncio.gather(claim(service, path), claim(service, path, token="b" * 64))
    assert sum(response.status_code == 200 for response in responses) == 1
    assert all(response.status_code in {200, 401, 409} for response in responses)
    refused = await claim(service, path, origin="https://service.test")
    assert refused.status_code == 401
    changed = await claim(service, path, device={**DEVICE, "name": "Changed"})
    assert changed.status_code == 401


async def test_pairing_requires_its_owner_login_and_current_workspace_authority(service):
    invitation, path = await create(service)
    other = await builder(service)
    with pytest.raises(ServiceError) as denied:
        await pairing.owned(service.runtime, other, service.tenant.workspace_id, invitation["id"])
    assert denied.value.code == "not_found"
    user = await actor(service)
    confined = replace(user, confinement=WorkspaceScope(service.tenant.organization_id, service.tenant.workspace_id))
    with pytest.raises(ServiceError) as denied:
        await pairing.create(service.runtime, confined, service.tenant.workspace_id)
    assert denied.value.code == "forbidden"
    await claim(service, path)
    with pytest.raises(ServiceError):
        await pairing.approve(service.runtime, other, service.tenant.workspace_id, invitation["id"], "000000")


@pytest.mark.parametrize("end", ["cancel", "expire"])
async def test_cancelled_or_expired_invitation_never_registers(service, end):
    invitation, path = await create(service)
    pending = await claim(service, path)
    if end == "cancel":
        assert (await service.client.post(path + "/cancel")).status_code == 200
    else:
        await service.runtime.redis.delete(pairing.key(invitation["id"]))
    assert (await claim(service, path)).status_code in {404, 409}
    approved = await service.client.post(
        path + "/approve", json={"verification_code": pending.json()["verification_code"]}
    )
    assert approved.status_code in {404, 409}
    assert (await service.client.get(f"{service.api}/environments")).json()["items"] == []


async def test_new_pairing_cannot_replace_existing_credential_or_revive_registration(service):
    from .test_client_environments import register

    existing = await register(service, device_id=DEVICE["device_id"])
    _, path = await create(service)
    pending = await claim(service, path)
    approved = await service.client.post(
        path + "/approve", json={"verification_code": pending.json()["verification_code"]}
    )
    assert approved.status_code == 409, approved.text
    assert approved.json()["error"]["details"]["reason"] == "client_credential_required"
    await clients.authenticate(
        service.runtime, existing.json()["client"]["environment"]["id"], existing.json()["token"]
    )


async def test_cancel_cannot_overtake_approval_after_registration_begins(service, monkeypatch):
    _, path = await create(service)
    pending = await claim(service, path)
    started, release = asyncio.Event(), asyncio.Event()
    original = clients.connect

    async def delayed(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(clients, "connect", delayed)
    task = asyncio.create_task(
        service.client.post(path + "/approve", json={"verification_code": pending.json()["verification_code"]})
    )
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert (await service.client.post(path + "/cancel")).status_code == 409
    finally:
        release.set()
    assert (await task).status_code == 200


async def recognize(service, path, identity, token, **headers):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test"
    ) as native:
        return await native.post(
            path + "/recognize",
            json={"environment_id": identity, "device_id": DEVICE["device_id"]},
            headers={"x-a13n-device-credential": f"Bearer {token}", **headers},
        )


async def test_refresh_recognizes_existing_registration_without_approval_or_mutation(service):
    from .test_client_environments import register

    existing = await register(service, device_id=DEVICE["device_id"])
    identity = existing.json()["client"]["environment"]["id"]
    token = existing.json()["token"]
    before = await service.client.get(f"{service.api}/client-environments/{identity}")
    for _ in range(3):
        _, path = await create(service)
        for _ in range(2):
            response = await recognize(service, path, identity, token)
            assert response.status_code == 200, response.text
            assert response.json()["state"] == "approved"
            assert response.json()["reused"] is True
            assert response.json()["environment_id"] == identity
            assert response.json()["verification_code"] is None
            assert token not in response.text
        assert (await service.client.get(path)).json()["environment_id"] == identity
    after = await service.client.get(f"{service.api}/client-environments/{identity}")
    assert before.headers["etag"] == after.headers["etag"]
    assert len((await service.client.get(f"{service.api}/environments")).json()["items"]) == 1
    async with short_session(service.runtime.storage) as session:
        config = await session.get_one(ClientSettingsRow, identity)
        assert config.credential_version == 1 and config.token_hash == secret_hash(token)


@pytest.mark.parametrize("failure", ["token", "origin", "workspace", "device", "cancel", "expire", "revoke"])
async def test_recognition_refuses_invalid_or_inactive_binding_without_fallback(service, failure):
    from .test_client_environments import register

    existing = await register(service, device_id=DEVICE["device_id"] if failure != "device" else "other-device")
    identity = existing.json()["client"]["environment"]["id"]
    token = existing.json()["token"]
    invitation, path = await create(service)
    headers = {}
    if failure == "token":
        token = "wrong" * 16
    elif failure == "origin":
        headers["origin"] = "https://service.test"
    elif failure == "workspace":
        raw, value = await pairing.load(service.runtime, invitation["id"])
        value.workspace_id = "other-workspace"
        await pairing.replace(service.runtime, raw, value)
    elif failure == "cancel":
        await service.client.post(path + "/cancel")
    elif failure == "expire":
        await service.runtime.redis.delete(pairing.key(invitation["id"]))
    elif failure == "revoke":
        current = await service.client.get(f"{service.api}/client-environments/{identity}")
        await service.client.post(
            f"{service.api}/client-environments/{identity}/revoke-credential",
            headers={"if-match": current.headers["etag"]},
        )
    response = await recognize(service, path, identity, token, **headers)
    assert response.status_code in {401, 404, 409}, response.text
    assert len((await service.client.get(f"{service.api}/environments")).json()["items"]) == 1
