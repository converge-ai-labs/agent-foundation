"""Service endpoint and body policy applies on environment-selected proxy routes."""

import asyncio
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def isolated_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "request_method"}:
            monkeypatch.delenv(key)


@asynccontextmanager
async def proxy_server(*, status: int = 200, encoding: str = "identity") -> AsyncIterator[tuple[str, list[bytes]]]:
    requests: list[bytes] = []
    tasks: set[asyncio.Task[None]] = set()

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(3):
                requests.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(
                    f"HTTP/1.1 {status} Test\r\nContent-Length: 4\r\nContent-Encoding: {encoding}\r\n"
                    "Location: http://127.0.0.1/private\r\nConnection: close\r\n\r\nbody".encode()
                )
                await writer.drain()
        finally:
            writer.close()

    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tasks.add(asyncio.create_task(respond(reader, writer)))

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    async with server:
        try:
            yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", requests
        finally:
            if tasks:
                await asyncio.gather(*tasks)


def resolve_to(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    async def resolve(host: str, port: int, **kwargs):
        assert host == "target.test"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses]

    monkeypatch.setattr("a13n_service.infra.outbound.anyio.getaddrinfo", resolve)


@pytest.mark.parametrize("status", [200, 302])
async def test_service_proxy_keeps_host_hooks_and_redirect_policy(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    async def unexpected_dns(*args, **kwargs):
        pytest.fail("Proxy destinations must be resolved by the trusted proxy")

    monkeypatch.setattr("a13n_service.infra.outbound.allowed_addresses", unexpected_dns)
    calls = []

    async def before(request: httpx2.Request) -> None:
        calls.append(str(request.url))
        request.headers["authorization"] = "Bearer origin-token"

    async with proxy_server(status=status) as (proxy, requests):
        monkeypatch.setenv("HTTP_PROXY", proxy)
        async with open_http(EndpointPolicy(), timeout=2, max_bytes=4, before_request=before) as client:
            response = await client.get("http://target.test/read")
            assert response.status_code == status
            assert response.text == "body"
        assert client.is_closed
    assert calls == ["http://target.test/read"]
    assert len(requests) == 1
    assert requests[0].startswith(b"GET http://target.test/read HTTP/1.1")
    assert b"Host: target.test\r\n" in requests[0]
    assert b"accept-encoding: identity\r\n" in requests[0]
    assert b"Bearer origin-token" in requests[0]


@pytest.mark.parametrize("addresses", [("127.0.0.1",), ("93.184.216.34", "127.0.0.1"), ("169.254.169.254",)])
async def test_service_no_proxy_still_denies_every_unsafe_dns_answer(
    monkeypatch: pytest.MonkeyPatch, addresses: tuple[str, ...]
) -> None:
    resolve_to(monkeypatch, *addresses)
    monkeypatch.setenv("NO_PROXY", "target.test")
    async with proxy_server() as (proxy, requests):
        monkeypatch.setenv("ALL_PROXY", proxy)
        async with open_http(EndpointPolicy(), timeout=2, max_bytes=10) as client:
            with pytest.raises(EndpointPolicyError):
                await client.get("http://target.test/read")
    assert not requests


@pytest.mark.parametrize("encoding,max_bytes", [("gzip", 10), ("identity", 3)])
async def test_service_proxy_keeps_response_bounds(
    monkeypatch: pytest.MonkeyPatch, encoding: str, max_bytes: int
) -> None:
    resolve_to(monkeypatch, "93.184.216.34")
    async with proxy_server(encoding=encoding) as (proxy, requests):
        monkeypatch.setenv("ALL_PROXY", proxy)
        async with open_http(EndpointPolicy(), timeout=2, max_bytes=max_bytes) as client:
            with pytest.raises(httpx2.DecodingError if encoding == "gzip" else ServiceError):
                await client.get("http://target.test/read")
    assert len(requests) == 1


async def test_service_proxy_keeps_literal_address_url_restrictions(monkeypatch: pytest.MonkeyPatch) -> None:
    async with proxy_server() as (proxy, requests):
        monkeypatch.setenv("ALL_PROXY", proxy)
        async with open_http(EndpointPolicy(), timeout=2, max_bytes=10) as client:
            with pytest.raises(EndpointPolicyError):
                await client.get("http://169.254.169.254/metadata")
    assert not requests
