"""Attempt-owned web clients reuse real connections and close on every exit path."""

import base64
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, Literal

import anyio
import httpx2
import pytest
from a13n_harness.capabilities.web import WebProviderError
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.web.options import ScrapeOptions, SearchOptions
from a13n_service.distribution import OSS
from a13n_service.infra.crypto import KeyRing, SecretLocation
from a13n_service.providers.registry import Registry
from a13n_service.resources.providers.service import ResolvedProvider
from a13n_service.resources.web_providers import runtime as web
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from pydantic import SecretStr

pytestmark = pytest.mark.anyio


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> list[httpx2.AsyncClient]:
    opened: list[httpx2.AsyncClient] = []
    factory = web.provider_client

    def create() -> httpx2.AsyncClient:
        client = factory()
        opened.append(client)
        return client

    monkeypatch.setattr(web, "provider_client", create)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    return opened


@asynccontextmanager
async def backend(
    operation: Literal["search", "scrape"], url: str, *, account: str = "account-a"
) -> AsyncIterator[Callable[..., Awaitable[bytes]]]:
    provider = ResolvedProvider(
        id=account,
        version=1,
        type="tavily",
        config={},
        enabled=True,
        credential=None,
        extra_headers={},
        location=SecretLocation("org_test", "web_providers", "credential", account),
    )
    # These tests exercise the Service-owned transport lifetime, independently of vendor payload parsing.
    # Supply a valid encrypted credential so the real provider definition opens normally.
    keys = KeyRing(active_key_id="test", keys={"test": SecretStr(base64.b64encode(b"k" * 32).decode())})
    provider = replace(provider, credential=keys.protect(b'{"api_key":"test"}', provider.location))
    context = {"registry": Registry.of(OSS.providers), "keys": keys, "policy": EndpointPolicy()}
    opened = (
        web.open_search_backend(provider, SearchOptions(), **context)
        if operation == "search"
        else web.open_scrape_backend(provider, ScrapeOptions(), **context)
    )
    async with opened as binding:

        async def exchange(path: str = "/", *, max_bytes: int = 100) -> bytes:
            return await binding.provider.transport.exchange(  # type: ignore[attr-defined]
                lambda client: client.build_request("GET", url + path, headers={"authorization": account}),
                operation=operation,
                max_response_bytes=max_bytes,
            )

        yield exchange


@pytest.mark.parametrize("operation", ["search", "scrape"])
async def test_web_backend_reuses_connections_and_isolates_owners(operation, clients, listen) -> None:  # type: ignore[no-untyped-def]
    peers: list[tuple[Any, str]] = []
    app = FastAPI()

    @app.get("/")
    async def respond(request: Request) -> PlainTextResponse:
        peers.append((request.client, request.headers["authorization"]))
        return PlainTextResponse("ok")

    async with listen(app) as url:
        async with backend(operation, url) as call:
            assert await call() == await call() == b"ok"
            assert len(clients) == 1 and not clients[0].is_closed
            assert peers[0] == peers[1]
            async with backend(operation, url, account="account-b") as other:
                assert await other() == b"ok"
                assert peers[2][0] != peers[0][0] and peers[2][1] == "account-b"
            assert clients[1].is_closed and not clients[0].is_closed
            assert await call() == b"ok"
            assert peers[3] == peers[0]
        assert clients[0].is_closed
        async with backend(operation, url) as next_attempt:
            assert await next_attempt() == b"ok"
            assert peers[4][0] != peers[0][0]
        assert len(clients) == 3 and all(client.is_closed for client in clients)


@pytest.mark.parametrize("operation", ["search", "scrape"])
async def test_web_backend_closes_after_response_limit_failure(operation, clients, listen) -> None:  # type: ignore[no-untyped-def]
    app = FastAPI()
    app.get("/")(lambda: PlainTextResponse("too large"))
    async with listen(app) as url:
        with pytest.raises(WebProviderError, match=f"web_{operation}_response_invalid"):
            async with backend(operation, url) as call:
                await call(max_bytes=1)
        assert len(clients) == 1 and clients[0].is_closed


@pytest.mark.parametrize("operation", ["search", "scrape"])
async def test_web_backend_closes_during_cancellation(operation, clients, listen) -> None:  # type: ignore[no-untyped-def]
    app = FastAPI()
    received, release = anyio.Event(), anyio.Event()

    @app.get("/")
    async def respond() -> PlainTextResponse:
        received.set()
        await release.wait()
        return PlainTextResponse("ok")

    async with listen(app) as url:
        async with anyio.create_task_group() as tasks:

            async def attempt() -> None:
                async with backend(operation, url) as call:
                    await call()

            tasks.start_soon(attempt)
            await received.wait()
            tasks.cancel_scope.cancel()
        release.set()
        assert len(clients) == 1 and clients[0].is_closed
