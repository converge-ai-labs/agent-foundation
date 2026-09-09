"""Environment-backed Web tool transport with live authority and pinned destinations."""

import ipaddress
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx2
from a13n_harness.capabilities.web import WebPolicy, WebProviderError, WebRequest, WebResponse
from anyio import getaddrinfo, move_on_after

from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError

_REDIRECTS = frozenset({301, 302, 303, 307, 308})


@dataclass
class WebTransportPolicy:
    reauthorize: Callable[[], Awaitable[None]]
    endpoints: EndpointPolicy = field(default_factory=EndpointPolicy)

    async def authorize(self, url: str, *, purpose: str) -> None:
        await self.resolve(url, purpose=purpose)

    async def resolve(self, url: str, *, purpose: str) -> tuple[httpx2.URL, str]:
        del purpose
        await self.reauthorize()
        try:
            target = httpx2.URL(url).copy_with(fragment=None)
            await self.endpoints.validate(str(target), resolve_dns=False)
            _, hostname, port = self.endpoints.validate_syntax(str(target))
            answers = await getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            addresses = tuple(dict.fromkeys(str(item[4][0]) for item in answers))
            if not addresses:
                raise EndpointPolicyError("No destination address")
            for address in addresses:
                self.endpoints.validate_address(hostname, ipaddress.ip_address(address))
            return target, addresses[0]
        except (EndpointPolicyError, OSError, ValueError, httpx2.InvalidURL) as error:
            raise WebProviderError("web_destination_denied") from error


def web_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(follow_redirects=False, trust_env=False)


class WebTransport:
    def __init__(self, *, client_factory: Callable[[], httpx2.AsyncClient] = web_client) -> None:
        self._clients = client_factory

    async def request(self, request: WebRequest, *, policy: WebPolicy) -> WebResponse:
        if not isinstance(policy, WebTransportPolicy):
            raise WebProviderError("web_policy_unsupported")
        current_url = request.url
        redirects = 0
        while True:
            target, address = await policy.resolve(current_url, purpose=request.purpose)
            # One client per hop prevents cookie or pooled TLS identity reuse across origins.
            client = self._clients()
            response: httpx2.Response | None = None
            try:
                outgoing = client.build_request(
                    request.method,
                    target.copy_with(host=address),
                    headers={"Host": target.netloc.decode("ascii")},
                    extensions={"sni_hostname": target.host},
                    timeout=request.deadline_seconds,
                )
                response = await client.send(outgoing, stream=True, follow_redirects=False)
                if (
                    len(response.headers) > request.max_header_count
                    or sum(len(key) + len(value) for key, value in response.headers.raw) > request.max_header_bytes
                ):
                    raise WebProviderError("web_response_invalid")
                await policy.reauthorize()
                if response.status_code in _REDIRECTS:
                    location = response.headers.get("location")
                    if location is None or redirects >= request.max_redirects:
                        raise WebProviderError("web_redirect_limit")
                    next_url = urljoin(str(target), location)
                    try:
                        await policy.endpoints.validate_redirect(str(target), next_url, resolve_dns=False)
                    except EndpointPolicyError as error:
                        raise WebProviderError("web_destination_denied") from error
                    current_url = next_url
                    redirects += 1
                else:
                    return _web_response(response, client, request, policy, str(target), redirects)
            except BaseException:
                await _close(response, client)
                raise
            await _close(response, client)


def _web_response(
    response: httpx2.Response,
    client: httpx2.AsyncClient,
    request: WebRequest,
    policy: WebTransportPolicy,
    final_url: str,
    redirects: int,
) -> WebResponse:
    async def body() -> AsyncIterator[bytes]:
        total = 0
        async for chunk in response.aiter_bytes(chunk_size=request.max_stream_chunk_bytes):
            total += len(chunk)
            if total > request.max_response_bytes:
                raise WebProviderError("web_body_too_large")
            await policy.reauthorize()
            yield chunk
        await policy.reauthorize()

    async def close() -> None:
        await _close(response, client)

    return WebResponse(
        status_code=response.status_code,
        final_url=final_url,
        canonical_url=final_url,
        headers=dict(response.headers),
        body=body(),
        reason=response.reason_phrase,
        redirect_count=redirects,
        _close=close,
    )


async def _close(response: httpx2.Response | None, client: httpx2.AsyncClient) -> None:
    try:
        if response is not None:
            with move_on_after(1, shield=True):
                await response.aclose()
    finally:
        with move_on_after(1, shield=True):
            await client.aclose()
