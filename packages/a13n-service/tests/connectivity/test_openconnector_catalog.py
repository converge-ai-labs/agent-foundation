from __future__ import annotations

import httpx2
import pytest
from a13n_service.connectivity.connectors.contracts import ConnectorProviderError
from a13n_service.connectivity.connectors.http import ConnectorHttpClient
from a13n_service.connectivity.connectors.providers.openconnector.catalog import OpenConnectorCatalog


class AllowEndpoint:
    async def validate(self, value: str, *, resolve_dns: bool = False) -> str:
        return value


@pytest.fixture
def upstream():
    action = {
        "id": "github.get_current_user",
        "service": "github",
        "description": "Read current user",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "outputSchema": {"allOf": [{"type": "object"}]},
    }
    state = {"action": action}
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer dummy-key"
        assert "x-api-key" not in request.headers
        assert request.url.host == "connector.oomol.com"
        assert request.method == "GET"
        if request.url.path == "/v1/providers":
            data = [
                {"service": "17track", "displayName": "17TRACK", "authTypes": ["api_key"]},
                {"service": "github", "displayName": "GitHub", "authTypes": ["oauth2"]},
            ]
        elif request.url.path == "/v1/actions":
            assert dict(request.url.params) == {"service": "github"}
            data = [state["action"]]
        else:
            raise AssertionError(request.url)
        return httpx2.Response(200, json={"success": True, "message": "OK", "data": data})

    return state, requests, handler


def catalog(client):
    return OpenConnectorCatalog(
        ConnectorHttpClient(client, AllowEndpoint(), response_max_bytes=4 * 1024 * 1024),
        "dummy-key",
    )


@pytest.mark.anyio
async def test_catalog_reads_provider_and_action_schemas(upstream):
    _, requests, handler = upstream
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        directory = catalog(client)
        providers = await directory.providers()
        actions = await directory.actions("github")
        assert providers[0].service == "17track"
        assert len(actions) == 1
        assert actions[0].output_schema == {"allOf": [{"type": "object"}]}
        assert not client.is_closed
    assert [request.url.path for request in requests] == ["/v1/providers", "/v1/actions"]


@pytest.mark.anyio
async def test_catalog_bounds_are_enforced(upstream, monkeypatch):
    from a13n_service.connectivity.connectors.providers import discovery

    monkeypatch.setattr(discovery, "DISCOVERY_MAX_TOOLS", 1)
    _, _, handler = upstream
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(ConnectorProviderError, match="directory_too_large"):
            await catalog(client).providers()
