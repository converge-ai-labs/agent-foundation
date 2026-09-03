from __future__ import annotations

from urllib.parse import parse_qs

import httpx2
import pytest
from a13n_service.connectivity.mcp.oauth_client import MCPOAuthClient, MCPOAuthError, OAuthPreparation
from a13n_service.connectivity.outbound_policy import EndpointPolicy

RESOURCE = "https://8.8.8.8/mcp"
ISSUER = "https://8.8.4.4"


@pytest.mark.anyio
async def test_dcr_fallback_is_discovered_and_cleanup_uses_exact_registration() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/mcp":
            return httpx2.Response(
                401,
                headers={"www-authenticate": 'Bearer resource_metadata="https://8.8.8.8/resource"'},
            )
        if request.url.path == "/resource":
            return _json({"resource": RESOURCE, "authorization_servers": [ISSUER]})
        if request.url.path == "/.well-known/oauth-authorization-server":
            return _json(
                {
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "registration_endpoint": f"{ISSUER}/register",
                    "code_challenge_methods_supported": ["S256"],
                }
            )
        if request.url.path == "/register" and request.method == "POST":
            return _json(
                {
                    "client_id": "dynamic-client",
                    "client_secret": "dynamic-secret",
                    "token_endpoint_auth_method": "client_secret_post",
                    "registration_access_token": "registration-token",
                    "registration_client_uri": f"{ISSUER}/register/dynamic-client",
                },
                status_code=201,
            )
        if request.url.path == "/register/dynamic-client" and request.method == "DELETE":
            assert request.headers["authorization"] == "Bearer registration-token"
            return httpx2.Response(204)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle), follow_redirects=False) as http_client:
        client = MCPOAuthClient(http_client, EndpointPolicy())
        preparation = await client.prepare(
            RESOURCE,
            client_metadata_url="https://1.1.1.1/api/v1/oauth/mcp/client-metadata.json",
            redirect_uri="https://1.1.1.1/api/v1/oauth/mcp/callback",
            client_name="Foundation",
        )
        cleaned = await client.cleanup_registration_bundle(
            {
                "registration_access_token": preparation.registration_access_token,
                "registration_client_uri": preparation.registration_client_uri,
            }
        )

    assert preparation.client_id == "dynamic-client"
    assert preparation.client_secret == "dynamic-secret"
    assert cleaned is True
    assert requests[-1].method == "DELETE"


@pytest.mark.anyio
async def test_token_exchange_never_follows_origin_changing_redirect_with_code_or_secret() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(307, headers={"location": "https://9.9.9.9/token"})

    preparation = OAuthPreparation(
        resource_url=RESOURCE,
        issuer_url=ISSUER,
        authorization_endpoint=f"{ISSUER}/authorize",
        token_endpoint=f"{ISSUER}/token",
        registration_endpoint=None,
        client_id="client",
        client_secret="secret",
        token_endpoint_auth_method="client_secret_post",
        registration_access_token=None,
        registration_client_uri=None,
        scope=None,
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle), follow_redirects=False) as http_client:
        with pytest.raises(MCPOAuthError, match="credential_origin_redirect"):
            await MCPOAuthClient(http_client, EndpointPolicy()).exchange_code(
                preparation,
                code="code",
                verifier="v" * 64,
                redirect_uri="https://1.1.1.1/api/v1/oauth/mcp/callback",
            )

    assert len(requests) == 1
    form = parse_qs(requests[0].content.decode())
    assert form["resource"] == [RESOURCE]


def _json(value: dict[str, object], *, status_code: int = 200) -> httpx2.Response:
    return httpx2.Response(status_code, headers={"content-type": "application/json"}, json=value)
