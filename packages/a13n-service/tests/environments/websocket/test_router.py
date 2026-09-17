"""Official Control composition and authenticated client connection routes."""

from __future__ import annotations

import asyncio
import socket

import httpx2
import pytest
import uvicorn
from a13n_service.app import Components, create_app
from a13n_service.settings import Settings

from ..conftest import ORG_ID, WORKSPACE_ID
from ..test_router import authenticate, seed_database, settings
from .test_connection_host import daemon

pytestmark = pytest.mark.anyio


@pytest.fixture(params=["control", "all"])
async def client_api(request, tmp_path, service_database, redis_url):
    values = settings(tmp_path, service_database).model_dump()
    values["service"]["role"] = request.param
    values["environments"] = {
        "provider_builtins": ("a13n.websocket-envd",),
        "client_public_origin": "wss://foundation.example",
    }
    values["redis"] = {"backend": "redis", "url": redis_url}
    config = Settings.model_validate(values)
    await seed_database(config)
    app = create_app(config, components=Components(request_authenticator=authenticate))
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test") as client:
            yield app, client


async def register(client):
    base = f"/api/v1/workspaces/{WORKSPACE_ID}"
    response = await client.post(
        f"{base}/environment-providers", json={"type": "a13n.websocket-envd", "name": "Computer"}
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        f"{base}/environments",
        headers={"Idempotency-Key": "computer"},
        json={
            "provider_id": response.json()["id"],
            "configuration": {},
            "state": {
                "provider_key": "a13n.websocket-envd",
                "state_version": "1",
                "state": {"daemon_environment_id": "local-computer"},
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_ticket_status_and_drain_through_composed_control(client_api):
    app, client = client_api
    environment_id = await register(client)
    path = f"/api/v1/environments/{environment_id}"
    response = await client.post(f"{path}/connection-tickets")
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    ticket = response.json()
    assert ticket["websocket_url"] == f"wss://foundation.example{path}/connect"
    assert ticket["ticket"] and ticket["connection_id"]
    response = await client.get(f"{path}/connection")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["status"] == "offline"
    assert response.json()["connection_id"] is None
    detail = await client.get(path)
    assert ticket["ticket"] not in detail.text
    assert "daemon_environment_id" not in detail.text
    app.state.runtime.begin_drain()
    rejected = await client.post(f"{path}/connection-tickets")
    assert rejected.status_code == 503, rejected.text
    assert app.state.runtime.control.client_connections.reconciler.is_draining()


async def test_connection_management_preserves_resource_authority(client_api):
    _, client = client_api
    environment_id = await register(client)
    for suffix, method in (("connection", client.get), ("connection-tickets", client.post)):
        response = await method(f"/api/v1/environments/env_missing12345678/{suffix}")
        assert response.status_code == 404, response.text
    response = await client.get(f"/api/v1/environments/{environment_id}/connection")
    assert response.status_code == 200


async def test_native_envd_connects_through_official_route(client_api, envd_binary, tmp_path):
    app, client = client_api
    environment_id = await register(client)
    path = f"/api/v1/environments/{environment_id}"
    original = (await client.get(path)).json()
    ticket = (await client.post(f"{path}/connection-tickets")).json()
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off", access_log=False))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        url = f"ws://127.0.0.1:{listener.getsockname()[1]}{path}/connect"
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    assert not serving.done()
                    await asyncio.sleep(0.01)
            async with daemon(
                envd_binary, tmp_path / "client", url, ticket["ticket"], "local-computer", expected_exit=1
            ) as (process, _):
                async with asyncio.timeout(8):
                    while True:
                        status = (await client.get(f"{path}/connection")).json()
                        if status["status"] == "online":
                            break
                        await asyncio.sleep(0.05)
                assert status["connection_id"] == ticket["connection_id"]
                detail = (await client.get(path)).json()
                assert detail["status"] == "running" and detail["generation"] == original["generation"]
                app.state.runtime.begin_drain()
                assert (await client.get(f"{path}/connection")).status_code == 503
                connections = app.state.runtime.control.client_connections
                async with asyncio.timeout(3):
                    while (await connections.service.observe(ORG_ID, environment_id)).value.status != "offline":
                        await asyncio.sleep(0.05)
                # A controller must supply a new ticket after the carrier ends.
                # The daemon's retry of this consumed ticket is rejected.
                await asyncio.wait_for(process.wait(), 5)
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, 5)


@pytest.mark.parametrize(
    ("origin", "error"),
    [
        (None, "client_public_origin"),
        ("ws://foundation.example", "WSS origin"),
        ("wss://foundation.example", "shared Redis server"),
    ],
)
async def test_unsupported_client_deployment_fails_before_readiness(tmp_path, service_database, origin, error):
    values = settings(tmp_path, service_database).model_dump()
    values["service"]["role"] = "control"
    values["environments"] = {
        "provider_builtins": ("a13n.websocket-envd",),
        "client_public_origin": origin,
    }
    app = create_app(Settings.model_validate(values), components=Components(request_authenticator=authenticate))
    with pytest.raises(ValueError, match=error):
        async with app.router.lifespan_context(app):
            pytest.fail("unsupported client ingress became ready")
