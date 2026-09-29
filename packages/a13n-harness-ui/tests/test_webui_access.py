"""Configured public addresses extend admission, not authentication or same-origin access."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.webui import create_webui
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .test_interactive_protocol import listener_with_app

pytestmark = pytest.mark.anyio
PUBLIC = "https://anui.wh1isper.top:8090"
AUTH = {"Authorization": "Bearer test-only-key"}


def configuration(tmp_path: Path, origins: list[str]) -> Path:
    path = tmp_path / "a13n-harness-ui.yaml"
    path.write_text(json.dumps({"schema_version": "1", "webui": {"allowed_origins": origins}}))
    return path


@asynccontextmanager
async def webui(tmp_path: Path, origins: list[str], *, api_key: str | None = "test-only-key", host: str = "127.0.0.1"):
    path = configuration(tmp_path, origins)
    opened = None

    @asynccontextmanager
    async def factory():
        nonlocal opened
        settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
        async with open_harness_ui_app(
            settings, configuration_path=path, host_mode="webui", instrumentation=None
        ) as app:
            opened = app
            yield app

    server = create_webui(factory, api_key=api_key, host=host)
    async with server.router.lifespan_context(server):
        assert opened is not None
        yield server, opened


async def test_exact_public_origin_authentication_and_same_origin(tmp_path: Path) -> None:
    async with webui(tmp_path, [PUBLIC + "/", "https://other.example"]) as (server, _):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), headers=AUTH) as client:
            for address in (PUBLIC, "http://localhost", "http://127.0.0.1", "http://[::1]"):
                assert (await client.get(address + "/api/status", headers={"Origin": address})).status_code == 200
            for address in (
                "http://anui.wh1isper.top:8090",
                "https://anui.wh1isper.top",
                "https://anui.wh1isper.top:8091",
                "https://anui.wh1isper.top.attacker.invalid:8090",
            ):
                response = await client.get(address + "/api/status")
                assert response.status_code == 400
                assert response.json()["error"]["code"] == "host_rejected"
            for incoming_origin in ("https://other.example", "https://anui.wh1isper.top:8091", PUBLIC + "/", "null"):
                response = await client.get(PUBLIC + "/api/status", headers={"Origin": incoming_origin})
                assert response.status_code == 403
                assert response.json()["error"]["code"] == "origin_rejected"
            for key in ("", "Bearer wrong"):
                response = await client.get(PUBLIC + "/api/status", headers={"Authorization": key})
                assert response.status_code == 401
            assert (await client.get(PUBLIC + "/healthz", headers={"Authorization": ""})).status_code == 200


@pytest.mark.parametrize("host, ip_status", [("127.0.0.1", 400), ("0.0.0.0", 200), ("::", 200)])
async def test_default_host_admission_is_unchanged(tmp_path: Path, host: str, ip_status: int) -> None:
    async with webui(tmp_path, [], host=host) as (server, _):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), headers=AUTH) as client:
            assert (await client.get(PUBLIC + "/api/status")).status_code == 400
            assert (await client.get("http://192.0.2.1/api/status")).status_code == ip_status
            assert (await client.get("http://localhost/api/status")).status_code == 200


@pytest.mark.parametrize("api_key", ["test-only-key", None])
async def test_wildcard_only_relaxes_address_admission(tmp_path: Path, api_key: str | None) -> None:
    async with webui(tmp_path, ["*"], api_key=api_key) as (server, _):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server)) as client:
            for address in (PUBLIC, "http://another.example:9080"):
                response = await client.get(address + "/api/status")
                assert response.status_code == (401 if api_key else 200)
                response = await client.get(address + "/api/status", headers={**AUTH, "Origin": address})
                assert response.status_code == 200
                response = await client.get(
                    address + "/api/status", headers={**AUTH, "Origin": "https://attacker.invalid"}
                )
                assert response.status_code == 403


async def test_origins_are_captured_at_listener_startup(tmp_path: Path) -> None:
    async with webui(tmp_path, [PUBLIC]) as (server, app):
        configuration(tmp_path, ["https://replacement.example"])
        loaded = await app.reload_configuration()
        assert loaded is not None
        assert loaded.document.webui.allowed_origins == ("https://replacement.example",)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), headers=AUTH) as client:
            assert (await client.get(PUBLIC + "/api/status")).status_code == 200
            assert (await client.get("https://replacement.example/api/status")).status_code == 400
    # The same adapter captures the new configuration on a new lifespan.
    async with server.router.lifespan_context(server):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), headers=AUTH) as client:
            assert (await client.get(PUBLIC + "/api/status")).status_code == 400
            assert (await client.get("https://replacement.example/api/status")).status_code == 200


async def test_https_forwarding_requires_a_trusted_proxy(tmp_path: Path) -> None:
    async with webui(tmp_path, [PUBLIC]) as (server, _):
        proxy = ProxyHeadersMiddleware(server, trusted_hosts="192.0.2.10")
        for peer, expected in (("192.0.2.10", 200), ("192.0.2.11", 400)):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=proxy, client=(peer, 1234)),
                headers={**AUTH, "X-Forwarded-Proto": "https", "Origin": PUBLIC},
            ) as client:
                response = await client.get("http://anui.wh1isper.top:8090/api/status")
                assert response.status_code == expected


@pytest.mark.parametrize("origins", [[PUBLIC + "/"], ["*"]])
async def test_public_websocket_admission_preserves_auth_and_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origins: list[str]
) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    async with listener_with_app(tmp_path, configuration_path=configuration(tmp_path, origins)) as (_, ws, _):
        port = urlsplit(ws).port
        path = "ws://anui.wh1isper.top:8090/api/host/terminals/missing/connect"
        options = {
            "host": "127.0.0.1",
            "port": port,
            "proxy": None,
            "additional_headers": {"X-Forwarded-Proto": "https"},
        }
        async with connect(path, origin=PUBLIC, **options) as client:
            await client.send('{"api_key":"wrong"}')
            with pytest.raises(ConnectionClosed) as failure:
                await client.recv()
            assert failure.value.rcvd.code == 4401
        async with connect(path, origin=PUBLIC, **options) as client:
            await client.send('{"api_key":"test-only-key"}')
            error = json.loads(await client.recv())
            expected = "host_terminal_not_found" if os.name == "posix" else "host_terminal_unavailable"
            assert error["error"]["code"] == expected
        with pytest.raises(InvalidStatus) as failure:
            async with connect(path, origin="https://attacker.invalid", **options):
                pass
        assert failure.value.response.status_code == 403
        if origins != ["*"]:
            for rejected_path, proto in ((path.replace(":8090/", ":8091/"), "https"), (path, "http")):
                with pytest.raises(InvalidStatus) as failure:
                    async with connect(
                        rejected_path, **{**options, "additional_headers": {"X-Forwarded-Proto": proto}}
                    ):
                        pass
                assert failure.value.response.status_code == 403
