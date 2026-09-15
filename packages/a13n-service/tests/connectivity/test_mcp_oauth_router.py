import pytest
from a13n_service.app import create_app
from a13n_service.settings import ProcessRole, Settings
from httpx2 import ASGITransport, AsyncClient


@pytest.mark.anyio
async def test_service_exposes_no_mcp_client_metadata_route():
    app = create_app(Settings(service={"role": ProcessRole.control}))
    async with AsyncClient(transport=ASGITransport(app), base_url="https://service.example") as client:
        response = await client.get(f"/api/v1/oauth/mcp/client-metadata/{'a' * 64}/{'b' * 64}.json")
    assert response.status_code == 404


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
