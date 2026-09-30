"""Host-owned async HTTP clients with endpoint and response bounds.

The endpoint policy checks declared URL hosts, including redirected requests, without DNS prechecks or
address pinning. Native transports and operator-configured proxies own resolution and routing.
The URL and TLS identity are never rewritten.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import anyio
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
        await policy.validate(str(request.url))
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
    direct_transport = httpx2.AsyncHTTPTransport(trust_env=False, limits=_LIMITS)
    client = EnvironmentProxyClient(
        direct_transport,
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
