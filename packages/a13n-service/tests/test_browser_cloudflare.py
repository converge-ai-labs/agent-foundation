"""Cloudflare Browser Run session lifecycle without external requests."""

from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.providers.browser import cloud
from a13n_service.providers.browser.definition import BrowserProviderError

pytestmark = pytest.mark.anyio
ACCOUNT = "a" * 32
SESSION = "c84575e2-d2a2-43e8-8cf1-08fb47daac28"
ROOT = f"/client/v4/accounts/{ACCOUNT}"
BROWSER = f"{ROOT}/browser-rendering/devtools/browser"
DETAIL = f"{ROOT}/browser-rendering/devtools/session/{SESSION}"
AUTH = "Bearer fixture-token"


@pytest.fixture
def supplier():
    responses = []
    requests = []

    async def handle(request):
        requests.append(request)
        status, body = responses.pop(0)
        if isinstance(body, Exception):
            raise body
        if body is None:
            return httpx2.Response(status, content=b"null", headers={"Content-Type": "application/json"})
        return httpx2.Response(status, json=body)

    api = cloud.CloudAPI(
        "cloudflare",
        cloud.CloudCredential(api_key="fixture-token"),
        EndpointPolicy(),
        transport=httpx2.MockTransport(handle),
    )
    return cloud.Cloudflare(cloud.CloudflareConfiguration(account_id=ACCOUNT), api), requests, responses


async def test_create_and_reconnect_use_same_session_and_fixed_cloudflare_origin(supplier, monkeypatch):
    provider, requests, responses = supplier
    responses.append((200, {"sessionId": SESSION, "webSocketDebuggerUrl": "wss://wrong.example/debug"}))
    connect = AsyncMock()
    monkeypatch.setattr(cloud.Chromium, "connect", connect)
    async with provider.api.client:
        live = await provider.create("allocation", cloud.CloudflareRecipe(keep_alive_seconds=90))
        assert live.state == {"id": SESSION}
        assert live.runtime_id == SESSION
        assert live.expires_at is None
        for _ in range(2):
            await provider.connect(live.state)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"https://api.cloudflare.com{BROWSER}?keep_alive=90000"
    assert request.headers["Authorization"] == AUTH
    assert connect.await_count == 2
    connect.assert_awaited_with(
        f"wss://api.cloudflare.com{ROOT}/browser-run/devtools/browser/{SESSION}",
        headers={"Authorization": AUTH},
        timeout=15,
        discover=False,
    )


async def test_lost_create_response_does_not_retry_or_guess_session_ownership(supplier):
    provider, requests, responses = supplier
    responses.append((0, httpx2.ReadTimeout("unconfirmed response")))
    async with provider.api.client:
        with pytest.raises(BrowserProviderError) as failure:
            await provider.create("allocation", cloud.CloudflareRecipe())
        assert failure.value.certainty == "unknown"
        with pytest.raises(BrowserProviderError, match="Browser provider operation failed") as inspect:
            await provider.inspect("allocation", None)
        assert inspect.value.code == "cloud_inspect_unresolved"
        with pytest.raises(BrowserProviderError) as delete:
            await provider.delete("allocation", None)
        assert delete.value.code == "cloud_delete_unresolved"
    assert [r.method for r in requests] == ["POST"]


@pytest.mark.parametrize("terminal", [None, {"sessionId": SESSION, "endTime": 200}])
async def test_release_waits_for_session_shutdown_not_disconnect(supplier, terminal):
    provider, requests, responses = supplier
    disconnected = {"sessionId": SESSION, "connectionEndTime": 100, "endTime": 0}
    responses.extend(
        (200, body) for body in (disconnected, disconnected, {"status": "closing"}, disconnected, terminal)
    )
    state = {"id": SESSION}
    async with provider.api.client:
        assert (await provider.inspect("allocation", state)).runtime_id == SESSION
        with pytest.raises(BrowserProviderError) as failure:
            await provider.delete("allocation", state)
        assert failure.value.code == "cloud_delete_unresolved"
        await provider.delete("allocation", state)
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", DETAIL),
        ("GET", DETAIL),
        ("DELETE", f"{BROWSER}/{SESSION}"),
        ("GET", DETAIL),
        ("GET", DETAIL),
    ]


@pytest.mark.parametrize("status,body", [(404, {}), (200, []), (200, {"sessionId": "another", "endTime": 1})])
async def test_release_requires_authoritative_session_status(supplier, status, body):
    provider, requests, responses = supplier
    responses.append((status, body))
    async with provider.api.client:
        with pytest.raises(BrowserProviderError):
            await provider.delete("allocation", {"id": SESSION})
    assert [r.method for r in requests] == ["GET"]


async def test_release_accepts_closed_response_for_exact_session(supplier):
    provider, requests, responses = supplier
    responses.extend([(200, {"sessionId": SESSION}), (200, {"status": "closed"})])
    async with provider.api.client:
        await provider.delete("allocation", {"id": SESSION})
    assert [r.method for r in requests] == ["GET", "DELETE"]
