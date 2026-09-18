"""HTTP acceptance preserves durable mount receipts and resource authorization."""

from dataclasses import replace

import httpx2
import pytest
from a13n_service.api import install_api_conventions
from a13n_service.environments.router import router
from a13n_service.environments.websocket.coordination import DEFAULT_LIMITS
from a13n_service.iam import PrincipalRef
from anyio import create_task_group, sleep
from fastapi import FastAPI, Request

from tests.environments.websocket.conftest import relay_redis as relay_redis
from tests.hooks.support import hook_actor

from .test_mount_acceptance import mount_run as mount_run
from .test_websocket_acceptance import _connect
from .test_websocket_use_authorization import client_environment as client_environment

pytestmark = pytest.mark.anyio


@pytest.fixture
async def mount_api(mount_run, process_runtime_factory, interaction_sessions):
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
        # Construct the HTTP app before starting the short-lived online lease.
        connection = await _connect(coordination, environment.id)

        async def keep_online():
            while True:
                await sleep(coordination.limits.lease_ms / 3000)
                observed = await coordination.renew(connection)
                assert observed.value.connection == connection and observed.value.status == "online"

        try:
            async with create_task_group() as tasks:
                tasks.start_soon(keep_online)
                try:
                    yield client, app, runtime, f"/api/v1/runs/{run.id}/environment-mounts", environment.id
                finally:
                    tasks.cancel_scope.cancel()
        finally:
            await coordination.retire(connection)


async def test_mount_http_receipt_replay_conflict_and_ordered_pagination(mount_api):
    client, _, _, path, environment_id = mount_api
    # An online fixture must survive the original connection lease.
    await sleep(DEFAULT_LIMITS.lease_ms / 1000 + 0.1)
    first = {"name": "computer", "environment_id": environment_id}
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
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
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
    valid = {"name": "computer", "environment_id": environment_id}
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
        json={"name": "computer", "environment_id": environment_id},
        headers={"Idempotency-Key": "unauthorized"},
    )
    assert response.status_code == 404, response.text


async def test_mount_http_drain_keeps_history_readable(mount_api):
    client, _, runtime, path, environment_id = mount_api
    runtime.status.draining = True
    response = await client.post(
        path,
        json={"name": "computer", "environment_id": environment_id},
        headers={"Idempotency-Key": "draining"},
    )
    assert response.status_code == 503, response.text
    response = await client.get(path)
    assert response.status_code == 200 and response.json()["items"] == []
