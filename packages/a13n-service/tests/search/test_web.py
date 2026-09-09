import asyncio
import socket

import httpx2
import pytest
from a13n_harness.capabilities.web import WebProviderError, WebRequest
from a13n_harness.errors import RunError
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.search.web import WebTransport, WebTransportPolicy

pytestmark = pytest.mark.anyio


async def allowed():
    pass


def request(url="https://example.com/page/", **limits):
    return WebRequest(
        url=url,
        purpose="fetch",
        deadline_seconds=5,
        max_redirects=limits.get("max_redirects", 2),
        max_response_bytes=limits.get("max_response_bytes", 1024),
        max_header_count=8,
        max_header_bytes=1024,
        max_stream_chunk_bytes=4,
    )


@pytest.fixture
def public_dns(monkeypatch):
    async def resolve(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr("a13n_service.search.web.getaddrinfo", resolve)


async def test_redirect_preserves_origin_and_path_without_credentials_or_pool_reuse(public_dns):
    clients = []
    requests = []

    def handler(outgoing):
        requests.append(outgoing)
        assert outgoing.url.host == "93.184.216.34"
        assert "authorization" not in outgoing.headers and "x-api-key" not in outgoing.headers
        assert "cookie" not in outgoing.headers
        if len(requests) == 1:
            return httpx2.Response(
                302,
                headers={"Location": "https://other.example/final/", "Set-Cookie": "private=cookie; Path=/"},
            )
        return httpx2.Response(200, content=b"evidence")

    def client():
        instance = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        clients.append(instance)
        return instance

    response = await WebTransport(client_factory=client).request(request(), policy=WebTransportPolicy(allowed))
    try:
        assert b"".join([part async for part in response.body]) == b"evidence"
        assert response.final_url == response.canonical_url == "https://other.example/final/"
        assert response.redirect_count == 1
    finally:
        await response.close()
    assert [item.headers["host"] for item in requests] == ["example.com", "other.example"]
    assert [item.extensions["sni_hostname"] for item in requests] == ["example.com", "other.example"]
    assert [item.url.path for item in requests] == ["/page/", "/final/"]
    assert len(clients) == 2 and all(item.is_closed for item in clients)


@pytest.mark.parametrize("addresses", [("127.0.0.1",), ("93.184.216.34", "169.254.169.254")])
async def test_denies_private_or_mixed_dns_before_dispatch(monkeypatch, addresses):
    async def resolve(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses]

    monkeypatch.setattr("a13n_service.search.web.getaddrinfo", resolve)
    transport = WebTransport(client_factory=lambda: pytest.fail("unexpected dispatch"))
    with pytest.raises(WebProviderError, match="web_destination_denied"):
        await transport.request(request(), policy=WebTransportPolicy(allowed))


@pytest.mark.parametrize("target", ["http://example.com/insecure", "https://127.0.0.1/private"])
async def test_redirect_cannot_escape_destination_policy(public_dns, target):
    outgoing = []

    def handler(value):
        outgoing.append(value)
        return httpx2.Response(302, headers={"Location": target})

    transport = WebTransport(client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    # Literal loopback targets must be checked even if a DNS test double returns a public address.
    with pytest.raises(WebProviderError, match="web_destination_denied"):
        await transport.request(request(), policy=WebTransportPolicy(allowed))
    assert len(outgoing) == 1


async def test_stream_bounds_and_live_revocation_close_response(public_dns):
    revoked = False

    async def authorize():
        if revoked:
            raise RunError("Revoked", code="search_provider_unavailable")

    clients = []

    def client():
        value = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: httpx2.Response(200, content=b"12345678")))
        clients.append(value)
        return value

    transport = WebTransport(client_factory=client)
    response = await transport.request(request(max_response_bytes=4), policy=WebTransportPolicy(authorize))
    try:
        with pytest.raises(WebProviderError, match="web_body_too_large"):
            _ = [part async for part in response.body]
    finally:
        await response.close()
    response = await transport.request(request(), policy=WebTransportPolicy(authorize))
    revoked = True
    try:
        with pytest.raises(RunError, match="Revoked"):
            _ = [part async for part in response.body]
    finally:
        await response.close()
    assert all(value.is_closed for value in clients)


async def test_cancellation_closes_client_without_retry(public_dns):
    calls = []

    def handler(value):
        calls.append(value)
        raise asyncio.CancelledError

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    with pytest.raises(asyncio.CancelledError):
        await WebTransport(client_factory=lambda: client).request(request(), policy=WebTransportPolicy(allowed))
    assert len(calls) == 1 and client.is_closed


async def test_real_http_connection_uses_pinned_address_and_original_host(monkeypatch):
    received = []

    async def serve(reader, writer):
        received.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\nbody")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def resolve(host, port, **kwargs):
        assert host == "unresolvable.example"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr("a13n_service.search.web.getaddrinfo", resolve)
    policy = WebTransportPolicy(allowed, EndpointPolicy.from_operator_allowlist(private_cidrs=("127.0.0.1/32",)))
    try:
        response = await WebTransport().request(request(f"http://unresolvable.example:{port}/page/"), policy=policy)
        try:
            assert b"".join([part async for part in response.body]) == b"body"
        finally:
            await response.close()
    finally:
        server.close()
        await server.wait_closed()
    assert f"Host: unresolvable.example:{port}\r\n".encode() in received[0]
