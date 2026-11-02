"""Current Browser input grants admit private VNC relays without exposing upstream URLs."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.infra.db import now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.providers.tables import BrowserProviderRow
from a13n_service.runs.browsers import viewer
from a13n_service.runs.browsers.tables import BrowserRow
from a13n_service.runs.tables import ThreadRow
from starlette.requests import Request
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from .http_support import serve_http
from .test_browsers import bound_run, command, setup

pytestmark = pytest.mark.anyio


async def human_browser(service, runs_kit, scripted_model, monkeypatch, url="ws://127.0.0.1:1/?token=fixture-token"):
    supplier, browser = await setup(service, runs_kit)
    original = supplier.ensure_viewer

    async def ensure(*args, **kwargs):
        grant = await original(*args, **kwargs)
        return replace(grant, url=url, state={"protocol": "vnc", "password": "fixture-password"})

    monkeypatch.setattr(supplier, "ensure_viewer", ensure)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    result = await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    assert result["control"] == "human"
    return result, lease


@pytest.mark.parametrize("refusal", ["token", "episode", "generation", "returning", "expiry", "provider", "controller"])
async def test_viewer_rechecks_current_grant(service, runs_kit, scripted_model, monkeypatch, refusal):
    browser, lease = await human_browser(service, runs_kit, scripted_model, monkeypatch)
    assert (await viewer.authorize(service.runtime, browser["id"], "fixture-token")).password == "fixture-password"
    async with transaction(service.runtime.storage) as session:
        row = await session.get(BrowserRow, browser["id"])
        assert row is not None
        if refusal == "episode":
            row.episode += 1
        elif refusal == "generation":
            row.generation += 1
        elif refusal == "returning":
            row.control = "returning"
        elif refusal == "expiry":
            row.control_expires_at = await now(session) - timedelta(seconds=1)
        elif refusal == "provider":
            provider = await session.get(BrowserProviderRow, row.provider_id)
            provider.enabled = False
        elif refusal == "controller":
            thread = await session.get(ThreadRow, lease.thread_id)
            thread.archived_at = await now(session)
    with pytest.raises(ServiceError, match="unavailable"):
        await viewer.authorize(service.runtime, browser["id"], "wrong" if refusal == "token" else "fixture-token")


async def test_viewer_relay_and_public_credentials(service, runs_kit, scripted_model, monkeypatch):
    connections = []

    async def echo(socket):
        connections.append(socket)
        assert socket.request.path == "/?token=fixture-token"
        async for message in socket:
            await socket.send(message)

    async with serve(echo, "127.0.0.1", 0, subprotocols=["binary"]) as upstream:
        endpoint = f"ws://127.0.0.1:{upstream.sockets[0].getsockname()[1]}/?token=fixture-token"
        browser, _ = await human_browser(service, runs_kit, scripted_model, monkeypatch, endpoint)
        response = await service.client.post(f"{service.api}/browsers/{browser['id']}/viewer")
        assert response.status_code == 200, response.text
        public = response.json()
        assert public["protocol"] == "vnc" and public["password"] == "fixture-password"
        assert public["token"] == "fixture-token"
        assert "token" not in public["url"] and endpoint not in response.text
        assert response.headers["Cache-Control"] == "no-store"
        grant = await viewer.authorize(service.runtime, browser["id"], "fixture-token")
        request = Request(
            {
                "type": "http",
                "scheme": "https",
                "server": ("console.example", 443),
                "path": "/",
                "root_path": "",
                "headers": [],
                "router": service.app.router,
            }
        )
        assert viewer.public_viewer(request, browser["id"], grant).url.startswith("wss://console.example/")
        async with serve_http(service.app, lifespan="off") as base:
            url = base.replace("http:", "ws:") + f"/api/v1/browsers/{browser['id']}/viewer/connect"
            for protocols in (["binary"], ["binary", viewer.PROTOCOL + "wrong"]):
                with pytest.raises(InvalidStatus):
                    async with connect(url, subprotocols=protocols, proxy=None):
                        pass
            assert not connections
            async with connect(url, subprotocols=["binary", viewer.PROTOCOL + public["token"]], proxy=None) as socket:
                await socket.send(b"real websocket bytes")
                assert await socket.recv() == b"real websocket bytes"
            assert len(connections) == 1
            await connections[0].wait_closed()
        # A disconnected viewer does not release or stop the Browser.
        assert (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()["status"] == "ready"
    # The stored grant is still current but its upstream has gone away: fail admission cleanly.
    async with serve_http(service.app, lifespan="off") as base:
        url = base.replace("http:", "ws:") + f"/api/v1/browsers/{browser['id']}/viewer/connect"
        with pytest.raises(InvalidStatus):
            async with connect(url, subprotocols=["binary", viewer.PROTOCOL + "fixture-token"], proxy=None):
                pass


async def test_iframe_viewer_remains_direct(service, runs_kit, scripted_model):
    _, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    response = await service.client.post(f"{service.api}/browsers/{browser['id']}/viewer")
    assert response.status_code == 200, response.text
    assert response.json()["url"] == "https://viewer.example/secret"
    assert response.json()["protocol"] == "iframe"
    with pytest.raises(ServiceError):
        await viewer.authorize(service.runtime, browser["id"], "secret")
