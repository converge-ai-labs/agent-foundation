from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_service.app import create_app
from a13n_service.connectivity.mcp import router as mcp_router
from a13n_service.settings import ProcessRole, Settings
from httpx2 import ASGITransport, AsyncClient


@pytest.mark.anyio
async def test_public_callback_binds_route_and_redirects_with_private_receipt(monkeypatch):
    receive = AsyncMock(return_value="https://service.example/mcp-setup/callback#state=state&receipt=receipt")
    monkeypatch.setattr(mcp_router, "_oauth", lambda request: SimpleNamespace(receive_callback=receive))
    app = create_app(Settings(service={"role": ProcessRole.control}))
    async with AsyncClient(transport=ASGITransport(app), base_url="https://service.example") as client:
        response = await client.get(
            f"/api/v1/oauth/mcp/callback/{'a' * 64}",
            params={"state": "s" * 32, "code": "private-code", "iss": "https://issuer.example"},
        )
    assert response.status_code == 303
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "private-code" not in response.headers["location"]
    receive.assert_awaited_once_with(
        callback_key="a" * 64, state="s" * 32, code="private-code", issuer="https://issuer.example"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "extra", ["&code=another", "&iss=https://other.example&iss=https://issuer.example", "&error=access_denied"]
)
async def test_public_callback_rejects_ambiguous_or_failed_responses(monkeypatch, extra):
    receive = AsyncMock()
    monkeypatch.setattr(mcp_router, "_oauth", lambda request: SimpleNamespace(receive_callback=receive))
    app = create_app(Settings(service={"role": ProcessRole.control}))
    async with AsyncClient(transport=ASGITransport(app), base_url="https://service.example") as client:
        response = await client.get(f"/api/v1/oauth/mcp/callback/{'a' * 64}?state={'s' * 32}&code=private-code{extra}")
    assert response.status_code == 400
    assert "private-code" not in response.text
    receive.assert_not_awaited()
