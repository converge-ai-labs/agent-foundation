"""Host-owned async HTTP clients with endpoint and response bounds.

The endpoint policy checks declared URL hosts, including redirected requests, without DNS prechecks or
address pinning. Native transports and operator-configured proxies own resolution and routing.
The URL and TLS identity are never rewritten.
"""

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from urllib.parse import urlsplit

import anyio
import httpx2
from a13n_harness.http import outbound_tls_verify
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_harness.providers.http_transport import EnvironmentProxyClient

from a13n_service.infra.errors import ServiceError

_LIMITS = httpx2.Limits(max_connections=4, max_keepalive_connections=2)


async def validate_cdp_endpoint(policy: EndpointPolicy, endpoint: str) -> None:
    """Authorize the destination separately from the encrypted CDP URL's secret query."""
    try:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"ws", "wss"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.path.startswith("/devtools/page/")
            or any(character.isspace() or ord(character) < 32 for character in endpoint)
        ):
            raise ValueError
        # Only this secret-bearing connection input omits the query from policy validation.
        # General HTTP endpoints retain their sensitive-query prohibition.
        destination = parsed._replace(scheme="https" if parsed.scheme == "wss" else "http", query="").geturl()
        await replace(policy, require_https=True).validate(destination)
    except (ValueError, EndpointPolicyError):
        raise EndpointPolicyError("CDP destination is not permitted") from None


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
) -> AsyncGenerator[httpx2.AsyncClient]:
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
    verify = httpx2.create_ssl_context(verify=outbound_tls_verify(), trust_env=False)
    direct_transport = httpx2.AsyncHTTPTransport(verify=verify, trust_env=False, limits=_LIMITS)
    client = EnvironmentProxyClient(
        direct_transport,
        verify=verify,
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
