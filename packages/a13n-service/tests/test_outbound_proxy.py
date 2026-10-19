"""Service endpoint and body policy applies on environment-selected proxy routes."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import pytest
from a13n_harness import RunConfiguration
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


@pytest.mark.parametrize("status", [200, 302])
async def test_service_proxy_keeps_host_hooks_and_redirect_policy(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
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


@pytest.mark.parametrize("bypass", [False, True])
async def test_service_authorizes_hosts_before_direct_or_proxy_routing(monkeypatch, bypass):
    monkeypatch.setenv("NO_PROXY", "target.test" if bypass else "")
    async with proxy_server() as (proxy, requests):
        monkeypatch.setenv("ALL_PROXY", proxy)
        policy = EndpointPolicy(configuration=RunConfiguration(allowed_hosts={"other.test"}))
        async with open_http(policy, timeout=2, max_bytes=10) as client:
            with pytest.raises(EndpointPolicyError):
                await client.get("http://target.test/read")
    assert not requests


@pytest.mark.parametrize("encoding,max_bytes", [("gzip", 10), ("identity", 3)])
async def test_service_proxy_keeps_response_bounds(
    monkeypatch: pytest.MonkeyPatch, encoding: str, max_bytes: int
) -> None:
    async with proxy_server(encoding=encoding) as (proxy, requests):
        monkeypatch.setenv("ALL_PROXY", proxy)
        async with open_http(EndpointPolicy(), timeout=2, max_bytes=max_bytes) as client:
            with pytest.raises(httpx2.DecodingError if encoding == "gzip" else ServiceError):
                await client.get("http://target.test/read")
    assert len(requests) == 1


async def test_service_proxy_keeps_run_literal_host_restrictions(monkeypatch: pytest.MonkeyPatch) -> None:
    async with proxy_server() as (proxy, requests):
        monkeypatch.setenv("ALL_PROXY", proxy)
        async with open_http(
            EndpointPolicy(configuration=RunConfiguration(allowed_hosts={"target.test"})), timeout=2, max_bytes=10
        ) as client:
            with pytest.raises(EndpointPolicyError):
                await client.get("http://169.254.169.254/metadata")
    assert not requests


@pytest.mark.parametrize("proxied", [False, True])
async def test_service_uses_native_transport_without_pre_resolution(
    monkeypatch: pytest.MonkeyPatch, clean_environment: None, proxied: bool
) -> None:
    from a13n_service.settings import load_settings

    monkeypatch.setenv("A13N_PROVIDERS__REQUIRE_HTTPS", "false")
    policy = load_settings().providers.endpoint_policy

    async with proxy_server() as (server, requests):
        if proxied:
            monkeypatch.setenv("HTTP_PROXY", server)
            url = "http://proxy-only.test/read"
        else:
            url = server + "/read"
        async with open_http(policy, timeout=2, max_bytes=4) as client:
            response = await client.get(url)
            assert response.text == "body"
    assert len(requests) == 1
    if proxied:
        assert requests[0].startswith(b"GET http://proxy-only.test/read HTTP/1.1")


async def test_service_direct_transport_keeps_response_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    async with proxy_server() as (server, requests):
        async with open_http(EndpointPolicy(), timeout=2, max_bytes=3) as client:
            with pytest.raises(ServiceError) as error:
                await client.get(server + "/read")
    assert error.value.code == "payload_too_large"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "value,verify_mode", [(None, "CERT_REQUIRED"), ("true", "CERT_REQUIRED"), ("false", "CERT_NONE")]
)
async def test_service_applies_operator_tls_to_both_direct_and_proxy_pools(
    monkeypatch: pytest.MonkeyPatch, value: str | None, verify_mode: str
) -> None:
    import ssl

    monkeypatch.delenv("A13N_OUTBOUND_TLS_VERIFY", raising=False)
    if value is not None:
        monkeypatch.setenv("A13N_OUTBOUND_TLS_VERIFY", value)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:8080")
    # Service deliberately ignores ambient CA bundle paths.
    monkeypatch.setenv("SSL_CERT_FILE", "/missing/ambient-ca.pem")
    async with open_http(EndpointPolicy(), timeout=2, max_bytes=10) as client:
        direct = client._transport_for_url(httpx2.URL("http://direct.test"))
        proxy = client._transport_for_url(httpx2.URL("https://target.test"))
        assert direct is not proxy
        for transport in (direct, proxy):
            context = transport._pool._ssl_context
            assert context.verify_mode == getattr(ssl, verify_mode)
            assert context.check_hostname is (verify_mode == "CERT_REQUIRED")
