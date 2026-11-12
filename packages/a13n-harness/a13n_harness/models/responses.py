"""Host policy and transport observations around the native Responses implementation.

Pydantic AI owns socket leases, continuation, routing, retries and response parsing.
The private reservation seam is pinned and tested against our reviewed minor line.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, cast

import httpx2
from httpx2._utils import URLPattern, get_environment_proxies
from openai import AsyncOpenAI
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.models.openai._responses_websocket import ResponsesWebSocketStream
from pydantic_ai.profiles import ModelProfileSpec
from pydantic_ai.providers import Provider
from pydantic_ai.settings import ModelSettings
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import InvalidStatus, SecurityError

from a13n_harness.http import EndpointValidator, outbound_tls_verify


class _ResponsesConnection(ClientConnection):
    """Keep redirects and bounded response bytes under the Host's authority."""

    def __init__(
        self,
        *args: Any,
        max_response_bytes: int | None = None,
        response_headers: Callable[[Mapping[str, str]], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._response_headers = response_headers
        self._max_response_bytes = max_response_bytes
        self._response_bytes = 0

    async def handshake(self, *args: Any, **kwargs: Any) -> None:
        try:
            await super().handshake(*args, **kwargs)
        except InvalidStatus as error:
            if 300 <= error.response.status_code < 400:
                # SecurityError is not a redirect candidate in websockets.connect.
                raise SecurityError("Responses WebSocket redirects are not permitted") from None
            raise
        if self._response_headers is not None:
            assert self.response is not None
            headers = {name: ", ".join(self.response.headers.get_all(name)) for name in self.response.headers}
            await self._response_headers(headers)

    async def send(self, message: Any, text: bool | None = None) -> None:
        # Bounded connections have one native lane; each send starts its response budget.
        self._response_bytes = 0
        await super().send(message, text=text)

    async def recv(self, decode: bool | None = None) -> Any:
        message = await super().recv(decode=decode)
        self._response_bytes += len(message.encode() if isinstance(message, str) else message)
        if self._max_response_bytes is not None and self._response_bytes > self._max_response_bytes:
            await self.close(code=1009, reason="Response byte limit exceeded")
            raise ModelAPIError(model_name="openai.responses", message="Provider response exceeds its byte limit")
        return message


def _websocket_options(
    endpoint: str,
    max_response_bytes: int | None,
    response_headers: Callable[[Mapping[str, str]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    url = httpx2.URL(endpoint)
    # Use the same HTTP(S)/ALL_PROXY and NO_PROXY routing as our HTTP clients.
    proxies = sorted((URLPattern(pattern), proxy) for pattern, proxy in get_environment_proxies().items())
    proxy = next((proxy for pattern, proxy in proxies if pattern.matches(url)), None)
    options: dict[str, Any] = {
        "proxy": proxy,
        "compression": None,
        "max_size": max_response_bytes,
        "max_queue": 16,
        "create_connection": partial(
            _ResponsesConnection, max_response_bytes=max_response_bytes, response_headers=response_headers
        ),
    }
    if url.scheme == "https":
        options["ssl"] = httpx2.create_ssl_context(verify=outbound_tls_verify(), trust_env=False)
    if proxy is not None and proxy.startswith("https://"):
        options["proxy_ssl"] = httpx2.create_ssl_context(verify=outbound_tls_verify(), trust_env=False)
    return options


class ResponsesModel(OpenAIResponsesModel):
    """Native Responses with explicit Host endpoint policy and actual transport events."""

    endpoint_policy: EndpointValidator | None = None
    max_response_bytes: int | None = None

    def __init__(
        self,
        model_name: str,
        *,
        provider: Provider[AsyncOpenAI],
        profile: ModelProfileSpec | None = None,
        settings: ModelSettings | None = None,
        history_digest_key: bytes | None = None,
        response_headers: Callable[[Mapping[str, str]], Awaitable[None]] | None = None,
    ) -> None:
        self._response_headers = response_headers
        # The Host owns this mapping and supplies it through the public constructor.
        # Populate it at reservation so late-bound Host policy and endpoint are respected.
        self._connection_options: dict[str, Any] = {}
        super().__init__(
            model_name,
            provider=provider,
            profile=profile,
            settings=settings,
            history_digest_key=history_digest_key,
            websocket_connection_options=cast(Any, self._connection_options),
        )

    async def _reserve_websocket_lane(
        self, loop: asyncio.AbstractEventLoop, messages: Sequence[ModelMessage], settings: OpenAIResponsesModelSettings
    ) -> ResponsesWebSocketStream | None:
        endpoint = httpx2.URL(self.client.websocket_base_url or self.client.base_url)
        endpoint = endpoint.copy_with(scheme={"wss": "https", "ws": "http"}.get(endpoint.scheme, endpoint.scheme))
        endpoint = endpoint.copy_with(path=endpoint.path.rstrip("/") + "/responses")
        if self.endpoint_policy is not None:
            await self.endpoint_policy.validate(str(endpoint))
        self._connection_options.clear()
        self._connection_options.update(
            _websocket_options(str(endpoint), self.max_response_bytes, self._response_headers)
        )
        if self.max_response_bytes is not None:
            # A byte budget belongs to one response, not multiplexed unrelated requests.
            self.profile["openai_responses_supports_websocket_stream_id"] = False
        return await super()._reserve_websocket_lane(loop, messages, settings)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        async with super().request_stream(messages, model_settings, model_request_parameters, run_context) as response:
            # This native response type positively marks WS; its other path is HTTP,
            # including a fallback after an idle socket closed before dispatch.
            transport = (
                "websocket" if (response.get().provider_details or {}).get("transport") == "websocket" else "http"
            )
            if run_context is not None and self._websocket_stream is None:
                await _report_transport(run_context, transport)
            yield response


async def _report_transport(ctx: RunContext[Any], transport: str) -> None:
    from a13n_harness.capabilities.lifecycle import active_model_request_index
    from a13n_harness.context import AgentContext
    from a13n_harness.errors import DefinitionError
    from a13n_harness.events import ModelRequestTransportPayload, emit_harness_event

    if not isinstance(ctx.deps, AgentContext):
        return
    try:
        index = active_model_request_index(ctx)
    except DefinitionError as error:
        if error.code != "model_request_boundary_missing":
            raise
        # Auxiliary inference may share deps without a Harness model-request node.
        return
    await emit_harness_event(
        ctx.deps.events,
        kind="lifecycle",
        payload=ModelRequestTransportPayload(
            request_id=f"model-request-{index + 1}", request_index=index, transport=cast(Any, transport)
        ),
    )
