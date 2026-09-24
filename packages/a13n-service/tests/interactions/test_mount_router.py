"""HTTP acceptance preserves durable mount receipts and resource authorization."""

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import replace

import httpx2
import pytest
from a13n_service.api import install_api_conventions
from a13n_service.environments.router import router
from a13n_service.iam import PrincipalRef
from anyio import sleep
from fastapi import FastAPI, Request

from tests.environments.websocket.conftest import relay_redis as relay_redis
from tests.hooks.support import hook_actor

from .test_mount_acceptance import mount_run as mount_run
from .test_websocket_acceptance import _connect
from .test_websocket_use_authorization import client_environment as client_environment

pytestmark = pytest.mark.anyio
HTTP_LEASE_MS = 5_000


@asynccontextmanager
async def _online_connection(coordination, environment_id):
    # HTTP acceptance is not a lease-expiration test. Allow for busy CI workers.
    coordination.limits = replace(coordination.limits, lease_ms=HTTP_LEASE_MS, candidate_ms=30_000)
    connection = await _connect(coordination, environment_id)

    async def keep_online():
        while True:
            observed = await coordination.renew(connection)
            assert observed.value.connection == connection and observed.value.status == "online"
            await sleep(coordination.limits.lease_ms / 3000)

    # Do not keep an AnyIO cancel scope across a fixture yield: a keeper failure
    # would cancel the shared pytest runner before it can report or clean up.
    keeper = asyncio.create_task(keep_online())
    try:
        yield
    finally:
        keeper.cancel()
        try:
            with suppress(asyncio.CancelledError):
                await keeper
        finally:
            await coordination.retire(connection)


@pytest.fixture
async def mount_api(mount_run, process_runtime_factory, interaction_sessions, request):
    service, coordination, run, _, environment = mount_run
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(router)

    async def authenticate(_request: Request):
        return hook_actor()

    runtime = process_runtime_factory(
        request_authenticator=authenticate, sessions=interaction_sessions, environment_mounts=service
    )
    app.state.runtime = runtime
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test") as client:
        if getattr(request, "param", "online") == "offline":
            yield client, app, runtime, f"/api/v1/runs/{run.id}/environment-mounts", environment.id
            return
        async with _online_connection(coordination, environment.id):
            yield client, app, runtime, f"/api/v1/runs/{run.id}/environment-mounts", environment.id


async def test_online_connection_reports_keeper_failure_and_retires(mount_run, monkeypatch):
    _, coordination, _, _, environment = mount_run
    failed = asyncio.Event()

    async def fail_renew(connection):
        failed.set()
        raise RuntimeError("renewal failed")

    monkeypatch.setattr(coordination, "renew", fail_renew)
    with pytest.raises(RuntimeError, match="renewal failed"):
        async with _online_connection(coordination, environment.id):
            await asyncio.wait_for(failed.wait(), timeout=5)
            # A failed keeper must not cancel the pytest runner or its teardown.
            await sleep(0)
    observed = await coordination.observe(environment.organization_id, environment.id)
    assert observed.value.status == "offline"


async def test_mount_http_current_replay_and_ordered_pagination(mount_api):
    client, _, _, path, environment_id = mount_api
    # An online fixture must survive the original connection lease.
    await sleep(HTTP_LEASE_MS / 1000 + 0.1)
    first = {
        "name": "computer",
        "environment_id": environment_id,
        "working_directory": "/projects/computer",
    }
    response = await client.post(path, json=first, headers={"Idempotency-Key": "first"})
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["name"] == "computer" and receipt["application_status"] == "pending"
    assert receipt["applied_attempt_id"] is None
    assert "ETag" not in response.headers
    replay = await client.post(path, json=first, headers={"Idempotency-Key": "first"})
    assert replay.status_code == 201 and replay.json() == receipt
    second = {**first, "name": "archive"}
    conflict = await client.post(path, json=second, headers={"Idempotency-Key": "first"})
    assert conflict.status_code == 201
    assert conflict.json() == receipt
    added = await client.post(path, json=second, headers={"Idempotency-Key": "second"})
    assert added.status_code == 201, added.text
    page = await client.get(path, params={"limit": 1})
    assert page.status_code == 200
    assert page.json()["items"] == [receipt]
    cursor = page.json()["next_cursor"]
    assert cursor
    page = await client.get(path, params={"limit": 1, "cursor": cursor})
    assert page.status_code == 200
    assert page.json()["items"] == [added.json()]
    assert page.json()["next_cursor"] is None


async def test_mount_http_rejects_invalid_inputs_before_acceptance(mount_api):
    client, _, _, path, environment_id = mount_api
    valid = {
        "name": "computer",
        "environment_id": environment_id,
        "working_directory": "/projects/computer",
    }
    missing_key = await client.post(path, json=valid)
    assert missing_key.status_code == 400
    for changes in ({"name": "workspace"}, {"name": "../escape"}, {"access": "admin"}, {"native_path": "/tmp"}):
        response = await client.post(path, json={**valid, **changes}, headers={"Idempotency-Key": "invalid"})
        assert response.status_code == 400, response.text
    for query in ({"limit": 0}, {"limit": 101}):
        response = await client.get(path, params=query)
        assert response.status_code == 400, response.text
    invalid_cursor = await client.get(path, params={"cursor": "invalid"})
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json()["error"]["code"] == "environment_invalid"
    assert (await client.get(path)).json()["items"] == []


async def test_mount_http_conceals_resources_from_unauthorized_principal(mount_api):
    client, app, runtime, path, environment_id = mount_api
    actor = replace(hook_actor(), principal=PrincipalRef(principal_type="user", principal_id="usr_ffffffffffffffff"))

    async def authenticate(_request: Request):
        return actor

    # Replace the immutable process value, preserving the actual mount service.
    app.state.runtime = replace(runtime, request_authenticator=authenticate)
    response = await client.get(path)
    assert response.status_code == 404, response.text
    response = await client.post(
        path,
        json={
            "name": "computer",
            "environment_id": environment_id,
            "working_directory": "/projects/computer",
        },
        headers={"Idempotency-Key": "unauthorized"},
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("mount_api", ["offline"], indirect=True)
async def test_mount_http_drain_keeps_history_readable(mount_api):
    client, _, runtime, path, environment_id = mount_api
    runtime.status.draining = True
    response = await client.post(
        path,
        json={
            "name": "computer",
            "environment_id": environment_id,
            "working_directory": "/projects/computer",
        },
        headers={"Idempotency-Key": "draining"},
    )
    assert response.status_code == 503, response.text
    response = await client.get(path)
    assert response.status_code == 200 and response.json()["items"] == []
