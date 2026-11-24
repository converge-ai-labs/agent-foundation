"""Apply the provider origin and inherited Run policy to every SDK request."""

from collections.abc import AsyncIterator

import httpx2
from a13n_harness.configuration import RunConfiguration
from a13n_harness.models.transport import create_model_http_client

from .sdk import ModelProviderDeclaration, https_url, origin


class _Budget:
    def __init__(self, remaining: int) -> None:
        self.remaining = remaining


class _LimitedStream(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream, budget: _Budget) -> None:
        self.stream = stream
        self.budget = budget

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.stream:
            self.budget.remaining -= len(chunk)
            if self.budget.remaining < 0:
                raise ValueError("Model Provider discovery exceeded its response budget")
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


def provider_http_client(
    declaration: ModelProviderDeclaration,
    configuration: RunConfiguration | None = None,
    *,
    discovery: bool = False,
    oauth: bool = False,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> httpx2.AsyncClient:
    client = create_model_http_client(
        configuration=configuration, transport=transport, retry=None, timeout=10 if discovery else 30 if oauth else 600
    )
    origins = frozenset(origin(https_url(value)) for value in declaration.allowed_origins)
    budget = _Budget(4 * 1024 * 1024) if discovery else None

    async def authorize(request: httpx2.Request) -> None:
        if origin(https_url(str(request.url))) not in origins:
            raise ValueError("The Model Provider request targets an undeclared HTTPS origin")
        hosts = request.headers.get_list("host")
        if len(hosts) != 1 or hosts[0].lower() != request.url.netloc.decode("ascii").lower():
            raise ValueError("The Model Provider request must use its URL authority")
        if budget is not None:
            request.headers["Accept-Encoding"] = "identity"

    async def response_policy(response: httpx2.Response) -> None:
        if response.has_redirect_location:
            raise ValueError("Model Provider clients do not follow redirects")
        if budget is not None:
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("Model Provider discovery must return an identity-encoded response")
            if response.is_stream_consumed:
                budget.remaining -= len(response.content)
                if budget.remaining < 0:
                    raise ValueError("Model Provider discovery exceeded its response budget")
                return
            assert isinstance(response.stream, httpx2.AsyncByteStream)
            response.stream = _LimitedStream(response.stream, budget)

    client.event_hooks["request"].append(authorize)
    client.event_hooks["response"].append(response_policy)
    return client
