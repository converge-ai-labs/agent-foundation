"""An explicit public origin supports TLS termination without trusting proxy headers."""

from contextlib import asynccontextmanager

import httpx
import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration.models import WebUiConfiguration
from a13n_harness_ui.webui import AccessBoundary, create_webui
from pydantic import ValidationError

from .test_app import _settings, _write_configuration
from .test_device_pairing import REQUEST, TOKEN


def test_public_origin_is_an_exact_root_origin():
    assert WebUiConfiguration().public_origin is None
    assert WebUiConfiguration(public_origin="https://UI.example:443/").public_origin == "https://ui.example"
    assert WebUiConfiguration(public_origin="http://localhost:8765").public_origin == "http://localhost:8765"
    for value in (
        "http://remote.example",
        "https://user:password@ui.example",
        "https://ui.example/prefix",
        "https://*.example",
        "https://ui.example?key=secret",
        "https://ui.example/#fragment",
    ):
        with pytest.raises(ValidationError):
            WebUiConfiguration(public_origin=value)


@pytest.mark.anyio
async def test_public_origin_pairing_and_status_are_captured_for_listener(tmp_path):
    configuration = _write_configuration(tmp_path)
    configuration.write_text(configuration.read_text() + "\nwebui:\n  public_origin: https://ui.example:8443\n")

    @asynccontextmanager
    async def opened():
        async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=configuration) as app:
            yield app

    server = create_webui(opened, api_key="browser-key")
    browser = {"Authorization": "Bearer browser-key", "Origin": "https://ui.example:8443"}
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://ui.example:8443") as client,
    ):
        assert (await client.get("/api/status")).status_code == 401
        status = await client.get("/api/status", headers=browser)
        assert status.status_code == 200
        assert status.json()["public_origin"] == "https://ui.example:8443"
        for bad_origin in (
            "null",
            "http://ui.example:8443",
            "https://ui.example",
            "https://evil.example",
            "https://ui.example:8443/path",
        ):
            response = await client.get("/api/status", headers={**browser, "Origin": bad_origin})
            assert response.status_code == 403
        for bad_host in ("ui.example", "evil.example:8443"):
            response = await client.get(
                "/api/status",
                headers={
                    **browser,
                    "Host": bad_host,
                    "X-Forwarded-Host": "ui.example:8443",
                    "X-Forwarded-Proto": "https",
                },
            )
            assert response.status_code == 400
        # Public Origin cannot be combined with another accepted Host.
        assert (await client.get("/api/status", headers={**browser, "Host": "localhost"})).status_code == 403
        pairing = await client.post("/api/envd/pair", json=REQUEST, headers={"Authorization": f"Bearer {TOKEN}"})
        challenge = pairing.json()["challenge"]
        assert pairing.json()["approval_url"] == "https://ui.example:8443/settings/environments"
        approved = await client.post(f"/api/device-pairings/{challenge['pairing_id']}/approve", headers=browser)
        assert approved.status_code == 200
        paired = await client.post(
            "/api/envd/pair",
            json=REQUEST,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "X-Forwarded-Host": "evil.example",
                "X-Forwarded-Proto": "http",
            },
        )
        assert paired.json()["websocket_url"] == f"wss://ui.example:8443/api/devices/{approved.json()['id']}/connect"
        # Publishing desired configuration does not mutate this listener's security boundary.
        configuration.write_text(configuration.read_text().replace("ui.example:8443", "other.example"))
        assert (await client.get("/api/status", headers=browser)).json()["public_origin"] == "https://ui.example:8443"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "supplied,accepted",
    [("https://ui.example", True), ("http://ui.example", False), ("https://evil.example", False), ("null", False)],
)
async def test_websocket_uses_same_public_origin_boundary(supplied, accepted):
    messages = []

    async def downstream(scope, receive, send):
        await send({"type": "websocket.accept"})

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)

    boundary = AccessBoundary(
        downstream,
        api_key="browser-key",
        allowed_hosts=frozenset({"localhost"}),
        public_origin=lambda: "https://ui.example",
    )
    await boundary(
        {
            "type": "websocket",
            "scheme": "ws",
            "path": "/api/devices/device-one/connect",
            "query_string": b"",
            "headers": [(b"host", b"ui.example"), (b"origin", supplied.encode())],
            "server": ("127.0.0.1", 8765),
        },
        receive,
        send,
    )
    assert messages[0]["type"] == ("websocket.accept" if accepted else "websocket.close")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "sandbox,match",
    [
        ("", "public_url"),
        ("      public_url: https://ui.example\n", "separate sandbox"),
        ("      public_url: http://127.0.0.1:9100\n", "HTTPS"),
    ],
)
async def test_public_origin_keeps_mcp_apps_separate_and_https(tmp_path, sandbox, match):
    configuration = _write_configuration(tmp_path)
    configuration.write_text(
        configuration.read_text()
        + "\nwebui:\n  public_origin: https://ui.example\n  mcp_apps:\n    enabled: true\n    sandbox:\n      bind: 127.0.0.1\n"
        + sandbox
    )
    server = create_webui(
        lambda: open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=configuration),
        api_key="browser-key",
    )
    with pytest.RaisesGroup(pytest.RaisesExc(ValueError, match=match)):
        async with server.router.lifespan_context(server):
            pytest.fail("Unsafe sandbox deployment accepted")
