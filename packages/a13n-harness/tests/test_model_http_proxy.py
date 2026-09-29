"""Exercise proxy routing over loopback sockets, not injected mock transports."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType

import httpx2
import pytest
from a13n_harness.models import ModelHttpRetryConfig, create_model_http_client
from a13n_harness.providers.model.credentials import ApiKeyCredential
from a13n_harness.providers.model.routes import build_api_key_model
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def isolated_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "request_method"}:
            monkeypatch.delenv(key)


@asynccontextmanager
async def _http_endpoint(
    *, statuses: tuple[int, ...] = (200,), body: bytes = b"ok"
) -> AsyncIterator[tuple[str, list[bytes]]]:
    requests: list[bytes] = []

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(5):
                headers = await reader.readuntil(b"\r\n\r\n")
                content_length = next(
                    (
                        int(line.split(b":", 1)[1])
                        for line in headers.split(b"\r\n")
                        if line.lower().startswith(b"content-length:")
                    ),
                    0,
                )
                request_body = await reader.readexactly(content_length)
                requests.append(headers + request_body)
                status = statuses[min(len(requests) - 1, len(statuses) - 1)]
                writer.write(
                    f"HTTP/1.1 {status} Test\r\nContent-Length: {len(body)}\r\n"
                    "Content-Type: application/json\r\nRetry-After: 0\r\nConnection: close\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with asyncio.TaskGroup() as tasks:
        server = await asyncio.start_server(
            lambda reader, writer: tasks.create_task(respond(reader, writer)), "127.0.0.1", 0
        )
        async with server:
            yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", requests


@pytest.mark.parametrize("variable", ["http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"])
async def test_http_proxy_environment(monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    async with _http_endpoint() as (proxy, requests):
        monkeypatch.setenv(variable, proxy)
        async with create_model_http_client() as client:
            response = await client.get("http://model.invalid/v1/models")
        assert response.text == "ok"
        assert requests[0].startswith(b"GET http://model.invalid/v1/models HTTP/1.1\r\n")


@pytest.mark.parametrize("variable", ["https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"])
async def test_https_proxy_environment_uses_connect(monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    # Reject the tunnel deliberately: observing CONNECT proves proxy selection
    # without external DNS, credentials or disabling TLS certificate validation.
    async with _http_endpoint(statuses=(502,)) as (proxy, requests):
        monkeypatch.setenv(variable, proxy)
        async with create_model_http_client() as client:
            with pytest.raises(httpx2.ProxyError):
                await client.get("https://model.invalid/v1/models")
        assert len(requests) == 1
        assert requests[0].startswith(b"CONNECT model.invalid:443 HTTP/1.1\r\n")


@pytest.mark.parametrize("variable", ["no_proxy", "NO_PROXY"])
@pytest.mark.parametrize("bypass", ["127.0.0.1", "*"])
async def test_no_proxy_uses_direct_transport_with_retries(
    monkeypatch: pytest.MonkeyPatch, variable: str, bypass: str
) -> None:
    async with _http_endpoint() as (proxy, proxy_requests), _http_endpoint(statuses=(503, 200)) as (origin, requests):
        monkeypatch.setenv("http_proxy", proxy)
        monkeypatch.setenv(variable, bypass)
        async with create_model_http_client() as client:
            response = await client.get(f"{origin}/model")
        assert response.status_code == 200
        assert len(requests) == 2
        assert requests[0].startswith(b"GET /model HTTP/1.1\r\n")
        assert not proxy_requests


async def test_environment_proxy_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _http_endpoint() as (preferred, requests), _http_endpoint() as (fallback, unused):
        monkeypatch.setenv("all_proxy", fallback)
        monkeypatch.setenv("HTTP_PROXY", fallback)
        monkeypatch.setenv("http_proxy", preferred)
        async with create_model_http_client() as client:
            await client.get("http://model.invalid/model")
        assert len(requests) == 1
        assert not unused


async def test_proxy_retries_preserve_request_body_and_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _http_endpoint(statuses=(503, 200)) as (proxy, requests):
        monkeypatch.setenv("http_proxy", proxy.replace("://", "://user:password@"))
        async with create_model_http_client() as client:
            response = await client.post("http://model.invalid/model", content=b'{"prompt":"hello"}')
        assert response.status_code == 200
        assert len(requests) == 2
        assert requests[0] == requests[1]
        assert b"Proxy-Authorization: Basic dXNlcjpwYXNzd29yZA==\r\n" in requests[0]
        assert requests[0].endswith(b'{"prompt":"hello"}')


@pytest.mark.parametrize("retry", [None, ModelHttpRetryConfig(attempts=2)])
async def test_proxy_retry_disable_and_exhaustion(
    monkeypatch: pytest.MonkeyPatch, retry: ModelHttpRetryConfig | None
) -> None:
    async with _http_endpoint(statuses=(503,)) as (proxy, requests):
        monkeypatch.setenv("http_proxy", proxy)
        async with create_model_http_client(retry=retry) as client:
            if retry is None:
                response = await client.get("http://model.invalid/model")
                assert response.status_code == 503
            else:
                with pytest.raises(httpx2.HTTPStatusError):
                    await client.get("http://model.invalid/model")
        assert len(requests) == (retry.attempts if retry else 1)


@pytest.mark.parametrize("retry", [None, ModelHttpRetryConfig(attempts=1)])
async def test_custom_transport_keeps_routing_control(
    monkeypatch: pytest.MonkeyPatch, retry: ModelHttpRetryConfig | None
) -> None:
    async with _http_endpoint() as (proxy, requests):
        monkeypatch.setenv("all_proxy", proxy)
        transport = httpx2.MockTransport(lambda request: httpx2.Response(200, text="custom"))
        async with create_model_http_client(transport=transport, retry=retry) as client:
            response = await client.get("https://model.invalid/model")
        assert response.text == "custom"
        assert not requests


@pytest.mark.parametrize("context_manager", [False, True])
async def test_client_closes_direct_and_proxy_transports(
    monkeypatch: pytest.MonkeyPatch, context_manager: bool
) -> None:
    closed: list[httpx2.AsyncHTTPTransport] = []
    original_close = httpx2.AsyncHTTPTransport.aclose
    original_exit = httpx2.AsyncHTTPTransport.__aexit__

    async def close(transport: httpx2.AsyncHTTPTransport) -> None:
        closed.append(transport)
        await original_close(transport)

    async def exit_transport(
        transport: httpx2.AsyncHTTPTransport,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        closed.append(transport)
        await original_exit(transport, exc_type, exc_value, traceback)

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "aclose", close)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "__aexit__", exit_transport)
    async with _http_endpoint() as (proxy, _requests):
        monkeypatch.setenv("http_proxy", proxy)
        monkeypatch.setenv("https_proxy", proxy)
        client = create_model_http_client()
        if context_manager:
            async with client:
                await client.get("http://model.invalid/model")
        else:
            try:
                await client.get("http://model.invalid/model")
            finally:
                await client.aclose()
        assert client.is_closed
        assert len(closed) == len({id(transport) for transport in closed}) == 3


async def test_native_model_reentry_recreates_proxy_client(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        b'{"id":"test","model":"test","object":"chat.completion","created":0,'
        b'"choices":[{"index":0,"message":{"role":"assistant","content":"done"},"finish_reason":"stop"}]}'
    )
    async with _http_endpoint(body=body) as (proxy, requests):
        monkeypatch.setenv("http_proxy", proxy)
        model = await build_api_key_model(
            "openai-chat:test", ApiKeyCredential(api_key="fixture"), base_url="http://127.0.0.1:1/v1"
        )
        assert model.provider is not None
        clients: list[httpx2.AsyncClient] = []
        for _ in range(2):
            async with model:
                client = model.provider._own_http_client
                assert isinstance(client, httpx2.AsyncClient)
                clients.append(client)
                response = await model.request(
                    [ModelRequest(parts=[UserPromptPart(content="hello")])], None, ModelRequestParameters()
                )
                assert response.text == "done"
            assert client.is_closed
        assert clients[0] is not clients[1]
        assert len(requests) == 2
        assert all(
            request.startswith(b"POST http://127.0.0.1:1/v1/chat/completions HTTP/1.1\r\n") for request in requests
        )
