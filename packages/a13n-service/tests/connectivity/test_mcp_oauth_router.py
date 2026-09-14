from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from a13n_service.app import create_app
from a13n_service.connectivity.mcp import router as mcp_router
from a13n_service.connectivity.mcp.domain import MCPClientMetadata
from a13n_service.settings import ProcessRole, Settings
from httpx2 import ASGITransport, AsyncClient


@pytest.mark.anyio
async def test_public_client_metadata_binds_issuer_and_application_callback(monkeypatch):
    metadata = MCPClientMetadata(
        client_id="https://service.example/client-metadata",
        client_name="Application client",
        redirect_uris=("https://application.example/oauth/callback",),
    )
    client_metadata = Mock(return_value=metadata)
    monkeypatch.setattr(mcp_router, "_oauth", lambda request: SimpleNamespace(client_metadata=client_metadata))
    app = create_app(Settings(service={"role": ProcessRole.control}))
    async with AsyncClient(transport=ASGITransport(app), base_url="https://service.example") as client:
        response = await client.get(f"/api/v1/oauth/mcp/client-metadata/{'a' * 64}/{'b' * 64}.json")
    assert response.status_code == 200
    assert response.json()["redirect_uris"] == ["https://application.example/oauth/callback"]
    client_metadata.assert_called_once_with("a" * 64, "b" * 64)


@pytest.mark.anyio
async def test_service_exposes_no_mcp_provider_callback_route():
    app = create_app(Settings(service={"role": ProcessRole.control}))
    async with AsyncClient(transport=ASGITransport(app), base_url="https://service.example") as client:
        response = await client.get(
            f"/api/v1/oauth/mcp/callback/{'a' * 64}",
            params={"state": "s" * 32, "code": "private-code"},
        )
    assert response.status_code == 404
    assert "private-code" not in response.text
