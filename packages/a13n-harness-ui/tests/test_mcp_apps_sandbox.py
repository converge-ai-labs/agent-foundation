"""Separate-origin listener and retained-presentation API boundaries."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration.models import McpAppsSandboxConfiguration
from a13n_harness_ui.mcp_apps.sandbox import content_security_policy, create_sandbox, origin, serve_sandbox
from a13n_harness_ui.webui import create_webui

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


async def test_sandbox_has_no_host_api_credentials_or_cors() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_sandbox()), base_url="http://sandbox.test"
    ) as client:
        response = await client.get("/sandbox.html", params={"host": "https://host.test"})
        assert response.status_code == 200
        assert 'setAttribute("sandbox", "allow-scripts")' in response.text
        assert "event.source === view.contentWindow" in response.text
        assert "event.source === parent && event.origin === host" in response.text
        assert "connect-src 'none'" in response.headers["content-security-policy"]
        assert "frame-ancestors https://host.test" in response.headers["content-security-policy"]
        assert "access-control-allow-origin" not in response.headers
        assert "set-cookie" not in response.headers
        assert (await client.get("/api/status")).status_code == 404
        assert (await client.post("/sandbox.html")).status_code == 405
        assert (await client.get("/sandbox.html")).status_code == 400
        assert (await client.get("/sandbox.html", params={"host": "null"})).status_code == 400


@pytest.mark.parametrize(
    "value", ["https://cdn.test; default-src *", "https://cdn.test/a.js", "https://host.test", "*", "data:"]
)
async def test_sandbox_rejects_invalid_or_host_csp_origins(value: str) -> None:
    with pytest.raises(ValueError):
        content_security_policy("https://host.test", {"resourceDomains": [value]})


async def test_resource_csp_only_grants_declared_exact_origins() -> None:
    policy = content_security_policy(
        "https://host.test", {"resourceDomains": ["https://cdn.test"], "connectDomains": ["https://api.test:443"]}
    )
    assert "script-src 'unsafe-inline' https://cdn.test" in policy
    assert "connect-src https://api.test" in policy
    assert "base-uri 'none'" in policy and "form-action 'none'" in policy
    assert origin("http://[::1]:1234/") == "http://[::1]:1234"


async def test_listener_is_owned_and_closes_its_port() -> None:
    async with httpx.AsyncClient(trust_env=False) as client:
        async with serve_sandbox(McpAppsSandboxConfiguration()) as url:
            response = await client.get(url, params={"host": "http://127.0.0.1:8765"})
            assert response.status_code == 200
        with pytest.raises(httpx.ConnectError):
            await client.get(url)


async def test_webui_enables_only_a_distinct_sandbox_and_keeps_api_auth(tmp_path: Path) -> None:
    configuration = _write_configuration(tmp_path)
    with configuration.open("a") as file:
        file.write("\nwebui:\n  mcp_apps:\n    enabled: true\n")
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>Host</html>")

    @asynccontextmanager
    async def opened():
        async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=configuration) as app:
            yield app

    server = create_webui(opened, api_key="test-key", static_root=static)
    async with server.router.lifespan_context(server):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1:8765"
        ) as client:
            response = await client.get("/")
            assert response.status_code == 200
            policy = response.headers["content-security-policy"]
            assert "frame-src http://127.0.0.1:" in policy
            assert (await client.post("/api/threads/other/apps/open", json={})).status_code == 401
            # An opaque App cannot use the authenticated Host API, even knowing its key.
            response = await client.post(
                "/api/threads/other/apps/open", json={}, headers={"Origin": "null", "Authorization": "Bearer test-key"}
            )
            assert response.status_code == 403
            sandbox = policy.split("frame-src ")[1]
            async with httpx.AsyncClient(trust_env=False) as direct:
                response = await direct.get(f"{sandbox}/api/status")
                assert response.status_code == 404


async def test_remote_listener_requires_public_sandbox_origin(tmp_path: Path) -> None:
    configuration = _write_configuration(tmp_path)
    with configuration.open("a") as file:
        file.write("\nwebui:\n  mcp_apps:\n    enabled: true\n")

    @asynccontextmanager
    async def opened():
        async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=configuration) as app:
            yield app

    server = create_webui(opened, api_key="test-key", host="0.0.0.0")
    with pytest.RaisesGroup(pytest.RaisesExc(ValueError, match="public_url")):
        async with server.router.lifespan_context(server):
            pytest.fail("Remote Host started without a browser-visible sandbox URL")


async def test_sandbox_bind_failure_preserves_webui_and_reports_text_fallback(tmp_path: Path) -> None:
    import socket

    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        configuration = _write_configuration(tmp_path)
        with configuration.open("a") as file:
            file.write(
                f"\nwebui:\n  mcp_apps:\n    enabled: true\n    sandbox:\n      port: {occupied.getsockname()[1]}\n"
            )
        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html>Host</html>")

        @asynccontextmanager
        async def opened():
            async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=configuration) as app:
                yield app

        server = create_webui(opened, api_key="test-key", static_root=static)
        async with server.router.lifespan_context(server):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server),
                base_url="http://127.0.0.1:8765",
                headers={"Authorization": "Bearer test-key"},
            ) as client:
                page = await client.get("/")
                assert page.status_code == 200
                assert "frame-src 'none'" in page.headers["content-security-policy"]
                response = await client.post("/api/threads/other/apps/open", json={})
                assert response.status_code == 400
                assert "mcp_apps_sandbox_unavailable" in response.text
                assert "original tool result remains available" in response.text
