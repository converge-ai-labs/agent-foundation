from __future__ import annotations

import gzip
import json
from urllib.parse import parse_qs

import httpx2
import pytest
from a13n_service.connectivity.mcp.oauth_client import MCPOAuthClient, MCPOAuthError, OAuthPreparation, issuer_key
from a13n_service.endpoint_policy import EndpointPolicy

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
                    "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
                    "authorization_response_iss_parameter_supported": True,
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
            public_origin="https://1.1.1.1",
            client_name="Service",
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
async def test_dcr_is_used_when_client_metadata_cannot_use_a_public_client() -> None:
    registered = False

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal registered
        if request.url.path == "/mcp":
            return httpx2.Response(401)
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            return _json({"resource": RESOURCE, "authorization_servers": [ISSUER]})
        if request.url.path == "/.well-known/oauth-authorization-server":
            return _json(
                {
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "registration_endpoint": f"{ISSUER}/register",
                    "client_id_metadata_document_supported": True,
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                }
            )
        if request.url.path == "/register":
            registered = True
            assert json.loads(request.content)["token_endpoint_auth_method"] == "client_secret_basic"
            return _json(
                {
                    "client_id": "registered",
                    "client_secret": "secret",
                    "token_endpoint_auth_method": "client_secret_basic",
                }
            )
        raise AssertionError(str(request.url))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        preparation = await MCPOAuthClient(http, EndpointPolicy()).prepare(
            RESOURCE,
            public_origin="https://1.1.1.1",
            client_name="Service",
        )

    assert registered
    assert preparation.client_id == "registered"
    assert preparation.token_endpoint_auth_method == "client_secret_basic"


@pytest.mark.anyio
async def test_token_exchange_never_follows_origin_changing_redirect_with_code_or_secret() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(307, headers={"location": "https://9.9.9.9/token"})

    preparation = OAuthPreparation(
        redirect_uri="https://1.1.1.1/callback",
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
        with pytest.raises(MCPOAuthError, match="oauth_redirect_forbidden"):
            await MCPOAuthClient(http_client, EndpointPolicy()).exchange_code(
                preparation,
                code="code",
                verifier="v" * 64,
            )

    assert len(requests) == 1
    form = parse_qs(requests[0].content.decode())
    assert form["resource"] == [RESOURCE]


def _json(value: dict[str, object], *, status_code: int = 200) -> httpx2.Response:
    return httpx2.Response(status_code, headers={"content-type": "application/json"}, json=value)


@pytest.mark.anyio
@pytest.mark.parametrize("auth_method", ["none", "client_secret_basic", "client_secret_post"])
async def test_authlib_exchange_and_refresh_share_bounded_cookie_free_pool(auth_method: str) -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert "cookie" not in request.headers
        form = parse_qs(request.content.decode())
        assert form["resource"] == [RESOURCE]
        if auth_method == "client_secret_basic":
            assert request.headers["authorization"].startswith("Basic ")
        else:
            assert form["client_id"] == ["client"]
            if auth_method == "client_secret_post":
                assert form["client_secret"] == ["secret"]
        token: dict[str, object] = {"access_token": "access", "token_type": "Bearer", "expires_in": 3600}
        if form["grant_type"] == ["authorization_code"]:
            assert form["code_verifier"] == ["v" * 64]
            token["refresh_token"] = "refresh"
        else:
            assert form["refresh_token"] == ["refresh"]
        return httpx2.Response(200, json=token, headers={"set-cookie": "session=provider; Path=/"})

    preparation = OAuthPreparation(
        redirect_uri="https://1.1.1.1/callback",
        resource_url=RESOURCE,
        issuer_url=ISSUER,
        authorization_endpoint=f"{ISSUER}/authorize",
        token_endpoint=f"{ISSUER}/token",
        registration_endpoint=None,
        client_id="client",
        client_secret=None if auth_method == "none" else "secret",
        token_endpoint_auth_method=auth_method,
        registration_access_token=None,
        registration_client_uri=None,
        scope=None,
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle), cookies={"old": "cookie"}) as pool:
        oauth = MCPOAuthClient(pool, EndpointPolicy())
        bundle = await oauth.exchange_code(preparation, code="code", verifier="v" * 64)
        refreshed = await oauth.refresh(bundle)
        assert refreshed["refresh_token"] == "refresh"
        assert not pool.is_closed
        assert not pool.cookies
    assert len(requests) == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx2.Response(200, content=b"x" * 128, headers={"content-type": "application/json"}), "response_too_large"),
        (httpx2.Response(200, text="not JSON"), "invalid_oauth_response"),
        (httpx2.Response(400, json={"error": "invalid_grant"}), "reauthorization_required"),
        (httpx2.Response(400, json={"access_token": "a", "token_type": "Bearer"}), "token_exchange_failed"),
        (httpx2.Response(200, json=[]), "invalid_oauth_response"),
        (httpx2.Response(400, json={"error": []}), "invalid_oauth_response"),
        (httpx2.Response(307, headers={"location": f"{ISSUER}/other"}), "oauth_redirect_forbidden"),
    ],
)
async def test_authlib_token_boundary_rejects_invalid_response(response: httpx2.Response, reason: str) -> None:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: response)) as pool:
        oauth = MCPOAuthClient(pool, EndpointPolicy(), response_max_bytes=64)
        with pytest.raises(MCPOAuthError, match=reason):
            await oauth.refresh(
                {
                    "refresh_token": "refresh",
                    "token_endpoint": f"{ISSUER}/token",
                    "client_id": "client",
                    "resource": RESOURCE,
                    "token_endpoint_auth_method": "none",
                }
            )


@pytest.mark.anyio
async def test_compressed_oauth_response_is_decoded_once() -> None:
    compressed = gzip.compress(json.dumps({"access_token": "a", "token_type": "Bearer"}).encode())

    def handle(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            content=compressed,
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as pool:
        token = await MCPOAuthClient(pool, EndpointPolicy()).refresh(
            {
                "refresh_token": "refresh",
                "token_endpoint": f"{ISSUER}/token",
                "client_id": "client",
                "resource": RESOURCE,
                "token_endpoint_auth_method": "none",
            }
        )
    assert token["access_token"] == "a"


@pytest.mark.anyio
@pytest.mark.parametrize("status, confirmed", [(202, False), (204, True), (500, False)])
async def test_registration_cleanup_requires_confirmed_result(status, confirmed):
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: httpx2.Response(status))) as http:
        client = MCPOAuthClient(http, EndpointPolicy())
        assert (
            await client.cleanup_registration_bundle(
                {"registration_client_uri": f"{ISSUER}/registration", "registration_access_token": "secret"}
            )
            is confirmed
        )


@pytest.mark.anyio
@pytest.mark.parametrize("resource", ["https://8.8.8.8/mcp/", "https://8.8.8.8"])
async def test_well_known_fallback_preserves_resource_and_issuer_identity(resource: str) -> None:
    issuer = f"{ISSUER}/tenant/"
    paths: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        if request.method == "POST":
            return httpx2.Response(401)
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            return (
                _json({"resource": resource, "authorization_servers": [issuer]})
                if resource.endswith("/mcp/")
                else httpx2.Response(404)
            )
        if request.url.path == "/.well-known/oauth-protected-resource":
            return _json({"resource": resource, "authorization_servers": [issuer]})
        if request.url.path == "/.well-known/oauth-authorization-server/tenant":
            return httpx2.Response(404)
        if request.url.path == "/.well-known/openid-configuration/tenant":
            return _json(
                {
                    "issuer": issuer,
                    "authorization_endpoint": f"{ISSUER}/authorize/",
                    "token_endpoint": f"{ISSUER}/token/",
                    "code_challenge_methods_supported": ["S256"],
                    "client_id_metadata_document_supported": True,
                    "token_endpoint_auth_methods_supported": ["none"],
                    "authorization_response_iss_parameter_supported": True,
                }
            )
        raise AssertionError(str(request.url))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        preparation = await MCPOAuthClient(http, EndpointPolicy()).prepare(
            "https://8.8.8.8/mcp/",
            public_origin="https://1.1.1.1",
            client_name="Service",
        )
    assert preparation.resource_url == resource
    assert preparation.issuer_url == issuer
    assert preparation.token_endpoint == f"{ISSUER}/token/"
    assert paths[0] == "/mcp/"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "metadata_url", "resource"),
    [
        ("https://8.8.8.8/mcp", "https://8.8.8.8/.well-known/oauth-protected-resource", "https://8.8.8.8"),
        ("https://8.8.8.8", "https://8.8.8.8/.well-known/oauth-protected-resource", "https://8.8.8.8/"),
    ],
)
async def test_advertised_metadata_accepts_a_canonical_parent_resource(
    endpoint: str,
    metadata_url: str,
    resource: str,
) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            return httpx2.Response(401, headers={"www-authenticate": f'Bearer resource_metadata="{metadata_url}"'})
        if str(request.url) == metadata_url:
            return _json({"resource": resource, "authorization_servers": [ISSUER]})
        if request.url.path == "/.well-known/oauth-authorization-server":
            return _json(
                {
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "code_challenge_methods_supported": ["S256"],
                    "client_id_metadata_document_supported": True,
                    "token_endpoint_auth_methods_supported": ["none"],
                }
            )
        raise AssertionError(str(request.url))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        preparation = await MCPOAuthClient(http, EndpointPolicy()).prepare(
            endpoint,
            public_origin="https://1.1.1.1",
            client_name="Service",
        )

    assert preparation.resource_url == resource


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("metadata_url", "resource"),
    [
        ("https://8.8.8.8/resource-metadata", "https://8.8.8.8"),
        ("https://8.8.8.8/.well-known/oauth-protected-resource/other", "https://8.8.8.8/other"),
        ("https://9.9.9.9/.well-known/oauth-protected-resource", "https://9.9.9.9"),
        ("https://8.8.8.8/.well-known/oauth-protected-resource", "https://8.8.8.8/#fragment"),
        ("https://8.8.8.8/.well-known/oauth-protected-resource", "https://8.8.8.8:invalid"),
    ],
)
async def test_advertised_parent_resource_requires_authoritative_coverage(
    metadata_url: str,
    resource: str,
) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            return httpx2.Response(401, headers={"www-authenticate": f'Bearer resource_metadata="{metadata_url}"'})
        return _json({"resource": resource, "authorization_servers": [ISSUER]})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        with pytest.raises(MCPOAuthError, match="resource_mismatch"):
            await MCPOAuthClient(http, EndpointPolicy()).prepare(
                RESOURCE,
                public_origin="https://1.1.1.1",
                client_name="Service",
            )


@pytest.mark.anyio
async def test_mismatched_path_metadata_falls_back_to_valid_root_metadata() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            return httpx2.Response(401)
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            return _json({"resource": "https://8.8.8.8/other", "authorization_servers": [ISSUER]})
        if request.url.path == "/.well-known/oauth-protected-resource":
            return _json({"resource": "https://8.8.8.8", "authorization_servers": [ISSUER]})
        if request.url.path == "/.well-known/oauth-authorization-server":
            return _json(
                {
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "code_challenge_methods_supported": ["S256"],
                    "client_id_metadata_document_supported": True,
                    "token_endpoint_auth_methods_supported": ["none"],
                }
            )
        raise AssertionError(str(request.url))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        preparation = await MCPOAuthClient(http, EndpointPolicy()).prepare(
            RESOURCE,
            public_origin="https://1.1.1.1",
            client_name="Service",
        )

    assert preparation.resource_url == "https://8.8.8.8"


@pytest.mark.anyio
@pytest.mark.parametrize("resource", ["https://8.8.8.8/other", "https://8.8.8.8/mcp/other"])
async def test_well_known_resource_must_match_the_identity_used_to_construct_its_url(resource: str) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST" or request.url.path.endswith("/mcp"):
            return httpx2.Response(404)
        return _json({"resource": resource, "authorization_servers": [ISSUER]})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        with pytest.raises(MCPOAuthError, match="resource_mismatch"):
            await MCPOAuthClient(http, EndpointPolicy()).prepare(
                "https://8.8.8.8/mcp/",
                public_origin="https://1.1.1.1",
                client_name="Service",
            )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("supported", "returned", "secret", "valid"),
    [
        (["client_secret_basic"], "client_secret_basic", "secret", True),
        (["client_secret_post"], "client_secret_post", "secret", True),
        (["none"], "none", None, True),
        (["client_secret_post"], "none", None, False),
        (["client_secret_basic"], "client_secret_basic", None, False),
    ],
)
async def test_registration_negotiates_supported_client_authentication(supported, returned, secret, valid):
    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/mcp":
            return httpx2.Response(401)
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            return _json({"resource": RESOURCE, "authorization_servers": [ISSUER]})
        if request.url.path == "/.well-known/oauth-authorization-server":
            return _json(
                {
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "registration_endpoint": f"{ISSUER}/register",
                    "token_endpoint_auth_methods_supported": supported,
                    "authorization_response_iss_parameter_supported": True,
                    "code_challenge_methods_supported": ["S256"],
                }
            )
        if request.url.path == "/register":
            assert json.loads(request.content)["token_endpoint_auth_method"] == supported[0]
            return _json({"client_id": "registered", "token_endpoint_auth_method": returned, "client_secret": secret})
        raise AssertionError(str(request.url))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http_client:
        client = MCPOAuthClient(http_client, EndpointPolicy())
        if not valid:
            with pytest.raises(MCPOAuthError):
                await client.prepare(RESOURCE, public_origin="https://1.1.1.1", client_name="Service")
        else:
            preparation = await client.prepare(RESOURCE, public_origin="https://1.1.1.1", client_name="Service")
            assert preparation.token_endpoint_auth_method == returned
            assert preparation.client_secret == secret


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("advertised", "declared", "accepted"),
    [
        (ISSUER, f"{ISSUER}/", True),
        (f"{ISSUER}/", ISSUER, True),
        (f"{ISSUER}/tenant", f"{ISSUER}/tenant/", False),
        (ISSUER, "https://1.1.1.1", False),
        (ISSUER, f"{ISSUER}/other", False),
        (ISSUER, None, False),
    ],
)
async def test_discovery_accepts_only_root_slash_alias_and_pins_declared_issuer(advertised, declared, accepted):
    def handle(request):
        if request.method == "POST":
            return httpx2.Response(401)
        if request.url.path.startswith("/.well-known/oauth-protected-resource"):
            return _json({"resource": RESOURCE, "authorization_servers": [advertised]})
        return _json(
            {
                "issuer": declared,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "client_id_metadata_document_supported": True,
                "token_endpoint_auth_methods_supported": ["none"],
                "code_challenge_methods_supported": ["S256"],
            }
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http_client:
        client = MCPOAuthClient(http_client, EndpointPolicy())
        if not accepted:
            with pytest.raises(MCPOAuthError, match="issuer_mismatch"):
                await client.discover(RESOURCE)
            return
        preparation = await client.prepare(RESOURCE, public_origin="https://1.1.1.1", client_name="Service")
        assert preparation.issuer_url == declared
        assert preparation.redirect_uri.endswith(issuer_key(declared))
