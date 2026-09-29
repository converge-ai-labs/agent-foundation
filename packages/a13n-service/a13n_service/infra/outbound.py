"""Host-owned async HTTP clients with endpoint and response bounds.

The endpoint policy checks each request URL before it is sent, and each new connection resolves its host once
and connects only when the policy allows every answer on direct routes. Operator-configured environment
proxies own final DNS and destination policy on proxy routes. The URL and TLS identity are never rewritten.
"""

import ipaddress
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from typing import cast

import anyio
import httpcore2
import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.http_transport import EnvironmentProxyClient

from a13n_service.infra.errors import ServiceError

_LIMITS = httpx2.Limits(max_connections=4, max_keepalive_connections=2)


class BoundedBody(httpx2.AsyncByteStream):
    """A response body read only uncompressed and never beyond `max_bytes`; a caller may leave it unread."""

    def __init__(self, stream: httpx2.AsyncByteStream, max_bytes: int, *, encoding: str):
        self.stream, self.max_bytes, self.encoding = stream, max_bytes, encoding

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self.encoding != "identity":
            # A transport failure, which each caller classifies as it does its other ones.
            raise httpx2.DecodingError(f"Response used unsupported compression {self.encoding}")
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > self.max_bytes:
                raise ServiceError(
                    "payload_too_large", "Provider response exceeds its byte limit", {"limit": self.max_bytes}
                )
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


async def allowed_addresses(
    policy: EndpointPolicy, host: str, port: int
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve `host` once; its distinct addresses when the policy allows every one. A name that does not resolve
    raises `OSError`, a refused answer `EndpointPolicyError`."""
    answers = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(ipaddress.ip_address(answer[4][0]) for answer in answers))
    for address in addresses:
        policy.validate_address(host, address)
    return addresses


class _PolicyBackend(httpcore2.AsyncNetworkBackend):
    def __init__(self, policy: EndpointPolicy):
        self.policy = policy
        self.backend = cast(httpcore2.AsyncNetworkBackend, httpcore2.AnyIOBackend())

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore2.SOCKET_OPTION] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        try:
            with anyio.fail_after(timeout):
                addresses = await allowed_addresses(self.policy, host, port)
        except TimeoutError as error:
            raise httpcore2.ConnectTimeout(str(error)) from error
        except OSError as error:
            raise httpcore2.ConnectError(str(error)) from error
        failure = httpcore2.ConnectError(f"{host} has no address")
        for address in addresses:
            try:
                return await self.backend.connect_tcp(
                    str(address), port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except httpcore2.ConnectError as error:
                failure = error
        raise failure

    async def sleep(self, seconds: float) -> None:
        await self.backend.sleep(seconds)


class _PolicyTransport(httpx2.AsyncHTTPTransport):
    def __init__(self, policy: EndpointPolicy):
        super().__init__(trust_env=False, limits=_LIMITS)
        # httpx2 accepts no network backend, so the pool it built is replaced by an equivalent one using ours.
        self._pool = httpcore2.AsyncConnectionPool(
            ssl_context=httpx2.create_ssl_context(trust_env=False),
            max_connections=_LIMITS.max_connections,
            max_keepalive_connections=_LIMITS.max_keepalive_connections,
            keepalive_expiry=_LIMITS.keepalive_expiry,
            network_backend=_PolicyBackend(policy),
        )


@asynccontextmanager
async def open_http(
    policy: EndpointPolicy,
    *,
    timeout: float,
    max_bytes: int,
    before_request: Callable[[httpx2.Request], Awaitable[None]] | None = None,
    after_response: Callable[[httpx2.Response], Awaitable[None]] | None = None,
) -> AsyncIterator[httpx2.AsyncClient]:
    async def check_request(request: httpx2.Request) -> None:
        # Resolved addresses are checked by the transport, at connect.
        await policy.validate(str(request.url), resolve_dns=False)
        if before_request is not None:
            await before_request(request)

    async def bound_response(response: httpx2.Response) -> None:
        if after_response is not None:
            await after_response(response)
        assert isinstance(response.stream, httpx2.AsyncByteStream), "an async client streams asynchronously"
        encoding = response.headers.get("content-encoding", "identity")
        response.stream = BoundedBody(response.stream, max_bytes, encoding=encoding)

    # Native MCP owns entry/exit; model clients start lazily on first send.
    # The outer owner also closes clients that never reached native setup.
    client = EnvironmentProxyClient(
        _PolicyTransport(policy),
        verify=httpx2.create_ssl_context(trust_env=False),
        limits=_LIMITS,
        timeout=timeout,
        follow_redirects=False,
        headers={"accept-encoding": "identity"},
        event_hooks={"request": [check_request], "response": [bound_response]},
    )
    try:
        yield client
    finally:
        if not client.is_closed:
            with anyio.fail_after(5, shield=True):
                await client.aclose()
