"""Exercise the UI-owned MCP and update factories with actual proxy sockets."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest

# Composition initializes the existing MCP/configuration import graph.
from a13n_harness_ui import composition  # noqa: F401
from a13n_harness_ui.interactive.updates import check_update
from a13n_harness_ui.mcp_adapters import _no_redirect_client

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def isolated_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "request_method"}:
            monkeypatch.delenv(key)


@asynccontextmanager
async def endpoint(status: int = 502) -> AsyncIterator[tuple[str, list[bytes]]]:
    requests = []
    tasks: set[asyncio.Task[None]] = set()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(2):
                requests.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(
                    f"HTTP/1.1 {status} Test\r\nLocation: https://elsewhere.test/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
                )
                await writer.drain()
        finally:
            writer.close()

    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tasks.add(asyncio.create_task(serve(reader, writer)))

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    async with server:
        try:
            yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", requests
        finally:
            if tasks:
                await asyncio.gather(*tasks)


async def test_remote_mcp_uses_https_proxy_without_sending_origin_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    async with endpoint() as (proxy, requests):
        monkeypatch.setenv("https_proxy", proxy)
        async with _no_redirect_client(
            headers={"authorization": "Bearer origin-token"}, timeout=httpx2.Timeout(2), follow_redirects=True
        ) as client:
            assert not client.follow_redirects
            with pytest.raises(httpx2.ProxyError):
                await client.post("https://mcp.test/tools", content=b"mutation")
    assert len(requests) == 1
    assert requests[0].startswith(b"CONNECT mcp.test:443 HTTP/1.1")
    assert b"origin-token" not in requests[0]


async def test_plaintext_loopback_mcp_stays_local_and_does_not_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    async with endpoint() as (proxy, unused), endpoint(302) as (origin, requests):
        monkeypatch.setenv("ALL_PROXY", proxy)
        async with _no_redirect_client(timeout=httpx2.Timeout(2), follow_redirects=True) as client:
            assert (await client.get(origin)).status_code == 302
    assert len(requests) == 1
    assert not unused


async def test_update_check_uses_https_proxy_and_keeps_failure_tolerance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async with endpoint() as (proxy, requests):
        monkeypatch.setenv("HTTPS_PROXY", proxy)
        assert await check_update(tmp_path, current="1.0") is None
    assert len(requests) == 1
    assert requests[0].startswith(b"CONNECT pypi.org:443 HTTP/1.1")


@pytest.mark.parametrize("bypass", [False, True])
async def test_web_client_uses_native_proxy_or_pinned_direct_route(
    monkeypatch: pytest.MonkeyPatch, bypass: bool
) -> None:
    from a13n_harness.capabilities import WebRequest
    from a13n_harness_ui.capability_runtime import HttpxWebClient, PublicWebPolicy

    async def resolve(self, url: str, *, purpose: str) -> tuple[str, ...]:
        # Stand in for the authorized DNS answer; a real connection must use it
        # on NO_PROXY, while the proxy receives the original hostname otherwise.
        return ("127.0.0.1",)

    monkeypatch.setattr(PublicWebPolicy, "resolve", resolve)
    async with endpoint(200) as (proxy, proxied), endpoint(200) as (origin, direct):
        monkeypatch.setenv("http_proxy", proxy)
        if bypass:
            monkeypatch.setenv("NO_PROXY", "public.test")
        url = origin.replace("127.0.0.1", "public.test") + "/page"
        request = WebRequest(
            url=url,
            purpose="fetch",
            deadline_seconds=2,
            max_redirects=2,
            max_response_bytes=1024,
            max_header_count=128,
            max_header_bytes=8192,
            max_stream_chunk_bytes=1024,
        )
        response = await HttpxWebClient().request(request, policy=PublicWebPolicy())
        try:
            assert response.status_code == 200
            assert response.final_url == url
        finally:
            await response.close()
    assert len(direct) == int(bypass)
    assert len(proxied) == int(not bypass)
    if proxied:
        assert proxied[0].startswith(f"GET {url} HTTP/1.1".encode())
