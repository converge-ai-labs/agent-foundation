from __future__ import annotations

import time
from pathlib import Path

import httpx2
import pytest
from a13n_harness_ui.mcp_oauth.models import OAuthBundle, OAuthError
from a13n_harness_ui.mcp_oauth.service import McpOAuth
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage.database import open_database
from a13n_harness_ui.storage.mcp_oauth import McpOAuthRepository
from anyio import create_task_group, sleep
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken

pytestmark = pytest.mark.anyio


def bundle(*, expires_at: float | None = None) -> OAuthBundle:
    return OAuthBundle(
        resource_url="https://mcp.example/mcp",
        issuer="https://auth.example",
        metadata=OAuthMetadata(
            issuer="https://auth.example",
            authorization_endpoint="https://auth.example/authorize",
            token_endpoint="https://auth.example/token",
            registration_endpoint="https://auth.example/register",
            response_types_supported=["code"],
        ),
        client=OAuthClientInformationFull(client_id="client", token_endpoint_auth_method="none"),
        tokens=OAuthToken(access_token="old-access", refresh_token="old-refresh", scope="read", expires_in=60),
        expires_at=expires_at if expires_at is not None else time.time() - 10,
        requested_scope="read",
    )


@pytest.fixture
async def repository(tmp_path: Path, migrated_databases):
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as db:
        yield McpOAuthRepository(db.sessions)


async def test_accounts_are_owner_scoped_and_replacement_fences_late_commits(repository):
    first = await repository.bind("config:first", "a" * 64)
    second = await repository.bind("config:second", "a" * 64)
    assert first != second
    await repository.save(first, bundle(), expected_version=0)
    replacement = await repository.replace(first)
    with pytest.raises(OAuthError, match="removed or replaced"):
        await repository.save(first, bundle(), expected_version=1)
    assert (await repository.get(replacement)).bundle is None
    await repository.remove("config:first")
    with pytest.raises(OAuthError):
        await repository.bind("config:first", "a" * 64)
    recreated = await repository.bind("config:first", "a" * 64, replace_configuration=True)
    assert recreated.generation != first.generation


async def test_restart_restores_expiry_and_external_token_endpoint(repository, tmp_path):
    binding = await repository.bind("resource", "a" * 64)
    await repository.save(binding, bundle(), expected_version=0)
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://auth.example/token"
        assert b"refresh_token=old-refresh" in request.content
        return httpx2.Response(
            200, json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 600}
        )

    service = McpOAuth(
        repository,
        tmp_path,
        tmp_path,
        client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    assert await service.access_token(binding) == "new-access"
    restarted = McpOAuth(repository, tmp_path, tmp_path)
    assert await restarted.access_token(binding) == "new-access"
    account = await repository.get(binding)
    assert account.bundle.tokens.refresh_token == "new-refresh"
    assert account.bundle.tokens.scope == "read"
    assert account.bundle.expires_at > time.time() + 590
    assert account.credential_version == 2
    assert not account.refresh_pending
    assert len(calls) == 1


async def test_independent_services_serialize_rotation(repository, tmp_path):
    binding = await repository.bind("resource", "a" * 64)
    await repository.save(binding, bundle(), expected_version=0)
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        await sleep(0.1)
        return httpx2.Response(
            200, json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 600}
        )

    def service():
        return McpOAuth(
            repository,
            tmp_path,
            tmp_path,
            client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )

    results = []

    async def obtain(client):
        results.append(await client.access_token(binding))

    async with create_task_group() as tasks:
        tasks.start_soon(obtain, service())
        tasks.start_soon(obtain, service())
    assert results == ["new-access", "new-access"]
    assert calls == 1


@pytest.mark.parametrize("outcome", ["timeout", "malformed", "invalid_grant", "invalid_client", "scope"])
async def test_refresh_uncertainty_blocks_old_token_reuse(repository, tmp_path, outcome):
    binding = await repository.bind("resource", "a" * 64)
    await repository.save(binding, bundle(), expected_version=0)
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if outcome == "timeout":
            raise httpx2.ReadTimeout("request timed out", request=request)
        if outcome == "malformed":
            return httpx2.Response(200, json={})
        if outcome == "scope":
            return httpx2.Response(200, json={"access_token": "new", "scope": "write"})
        return httpx2.Response(400, json={"error": outcome})

    service = McpOAuth(
        repository,
        tmp_path,
        tmp_path,
        client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    with pytest.raises(OAuthError):
        await service.access_token(binding)
    assert (await repository.get(binding)).refresh_pending
    with pytest.raises(OAuthError, match="unknown outcome"):
        await service.access_token(binding)
    assert calls == 1


async def test_business_401_is_sent_once_without_interactive_fallback(repository, tmp_path):
    binding = await repository.bind("resource", "a" * 64)
    await repository.save(binding, bundle(expires_at=time.time() + 600), expected_version=0)
    service = McpOAuth(repository, tmp_path, tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer old-access"
        return httpx2.Response(401, headers={"WWW-Authenticate": "Bearer"})

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), auth=service.auth(binding, "https://mcp.example/mcp")
    ) as client:
        response = await client.post("https://mcp.example/mcp", json={"method": "tools/call"})
    assert response.status_code == 401
    assert len(requests) == 1


@pytest.mark.parametrize("explicit_scope", ["read", None])
@pytest.mark.parametrize("completion", ["connect", "cancel", "remove"])
async def test_dcr_login_uses_sdk_pkce_and_commits_before_mcp_probe(
    repository, tmp_path, monkeypatch, explicit_scope, completion
):
    import json
    from urllib.parse import parse_qs, urlsplit

    from a13n_harness_ui.composition.models import ResolvedMcpRecipe
    from a13n_harness_ui.configuration.models import (
        EnvironmentVariableSource,
        McpOAuthConfiguration,
        McpRemoteTransport,
    )
    from a13n_harness_ui.mcp_oauth.login import Logins

    monkeypatch.setenv("MCP_TENANT", "fictional-tenant")
    requests = []

    def handler(request):
        requests.append(request)
        url = str(request.url)
        if url == "https://mcp.example/mcp":
            assert request.headers["X-Tenant"] == "fictional-tenant"
            assert "Authorization" not in request.headers
            return httpx2.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example/metadata", scope="read write"'
                },
            )
        assert "X-Tenant" not in request.headers
        if url == "https://mcp.example/metadata":
            return httpx2.Response(
                200,
                json={
                    "resource": "https://mcp.example/mcp",
                    "authorization_servers": ["https://auth.example"],
                    "scopes_supported": ["read", "write"],
                },
            )
        if request.method == "GET" and ".well-known/" in url:
            return httpx2.Response(
                200,
                json={
                    "issuer": "https://auth.example",
                    "authorization_endpoint": "https://auth.example/authorize",
                    "token_endpoint": "https://auth.example/token",
                    "registration_endpoint": "https://auth.example/register",
                    "response_types_supported": ["code"],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if url == "https://auth.example/register":
            return httpx2.Response(307, headers={"Location": "/register/"})
        if url == "https://auth.example/register/":
            metadata = json.loads(request.content)
            if explicit_scope is not None:
                assert metadata["scope"] == explicit_scope
            assert metadata["token_endpoint_auth_method"] == "none"
            return httpx2.Response(
                201, json={**metadata, "client_id": "registered-client", "client_secret": "provider-issued-secret"}
            )
        if url == "https://auth.example/token":
            params = parse_qs(request.content.decode())
            assert params["grant_type"] == ["authorization_code"]
            assert params["code"] == ["one-time-code"]
            assert params["code_verifier"][0]
            return httpx2.Response(
                200, json={"access_token": "private-access", "refresh_token": "private-refresh", "expires_in": 600}
            )
        raise AssertionError(f"Unexpected request {request.method} {url}")

    service = McpOAuth(
        repository,
        tmp_path,
        tmp_path,
        client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    logins = Logins(service)
    recipe = await service.bind(
        ResolvedMcpRecipe(
            server_id="mcp-test",
            transport=McpRemoteTransport(
                url="https://mcp.example/mcp",
                oauth=McpOAuthConfiguration(scope=explicit_scope),
                headers={"X-Tenant": EnvironmentVariableSource(env="MCP_TENANT")},
            ),
        )
    )
    try:
        status, cookie = await logins.start(recipe, "http://127.0.0.1:8000")
        assert status.state == "waiting", status
        params = parse_qs(urlsplit(status.authorization_url).query)
        assert sum(str(request.url) == "https://auth.example/register" for request in requests) == 1
        assert sum(str(request.url) == "https://auth.example/register/" for request in requests) == 1
        assert params["code_challenge_method"] == ["S256"]
        assert params["resource"] == ["https://mcp.example/mcp"]
        if explicit_scope is not None:
            assert params["scope"] == [explicit_scope]
        state = params["state"][0]
        with pytest.raises(OAuthError):
            logins.receive(
                state=state, cookie="other-browser", origin="http://127.0.0.1:8000", code="bad", issuer=None, error=None
            )
        if completion != "connect":
            if completion == "cancel":
                await logins.cancel(status.attempt_id)
            else:
                await repository.remove(service.owner("mcp-test"))
                await logins.cancel_owner(service.owner("mcp-test"))
            with pytest.raises(OAuthError):
                logins.receive(
                    state=state, cookie=cookie, origin="http://127.0.0.1:8000", code="late", issuer=None, error=None
                )
            assert not any(str(request.url) == "https://auth.example/token" for request in requests)
            return
        logins.receive(
            state=state,
            cookie=cookie,
            origin="http://127.0.0.1:8000",
            code="one-time-code",
            issuer="https://auth.example",
            error=None,
        )
        with pytest.raises(OAuthError):
            logins.receive(
                state=state,
                cookie=cookie,
                origin="http://127.0.0.1:8000",
                code="replayed-code",
                issuer=None,
                error=None,
            )
        await logins._attempts[status.attempt_id].task
        assert logins.status(status.attempt_id).state == "connected"
        current = await service.bind(recipe.model_copy(update={"oauth_binding": None}))
        account = await repository.get(current.oauth_binding)
        assert account.bundle.client.client_secret == "provider-issued-secret"
        assert account.bundle.tokens.access_token == "private-access"
        assert account.bundle.expires_at > time.time() + 590
        assert sum(str(request.url) == "https://mcp.example/mcp" for request in requests) == 1
    finally:
        await logins.close()


async def test_missing_dcr_endpoint_fails_without_registration_post(repository, tmp_path):
    from a13n_harness_ui.composition.models import ResolvedMcpRecipe
    from a13n_harness_ui.configuration.models import McpOAuthConfiguration, McpRemoteTransport
    from a13n_harness_ui.mcp_oauth.login import Logins

    posted = []

    def handler(request):
        url = str(request.url)
        if request.method == "POST":
            posted.append(url)
            return httpx2.Response(401)
        if "oauth-protected-resource" in url:
            return httpx2.Response(404)
        return httpx2.Response(
            200,
            json={
                "issuer": "https://mcp.example",
                "authorization_endpoint": "https://mcp.example/authorize",
                "token_endpoint": "https://mcp.example/token",
                "response_types_supported": ["code"],
                "code_challenge_methods_supported": ["S256"],
            },
        )

    service = McpOAuth(
        repository,
        tmp_path,
        tmp_path,
        client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    logins = Logins(service)
    recipe = await service.bind(
        ResolvedMcpRecipe(
            server_id="mcp-test",
            transport=McpRemoteTransport(url="https://mcp.example/mcp", oauth=McpOAuthConfiguration()),
        )
    )
    try:
        status, _ = await logins.start(recipe, "http://127.0.0.1:8000")
        assert status.state == "failed"
        assert "DCR" in status.error
        assert posted == ["https://mcp.example/mcp"]
    finally:
        await logins.close()


async def test_separate_os_processes_refresh_one_rotating_grant(repository, tmp_path):
    import sys

    from anyio import run_process

    binding = await repository.bind("resource", "a" * 64)
    await repository.save(binding, bundle(), expected_version=0)
    # Both interpreters open independent database engines and independently load
    # the same expired token. Only the account FileLock spans external I/O.
    script = """
import asyncio, sys
from pathlib import Path
import httpx2
from a13n_harness_ui.mcp_oauth.models import OAuthBinding
from a13n_harness_ui.mcp_oauth.service import McpOAuth
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage.database import open_database
from a13n_harness_ui.storage.mcp_oauth import McpOAuthRepository
async def main():
    root = Path(sys.argv[1])
    async def handler(request):
        with (root / "refresh-calls").open("a") as output:
            output.write("refresh\\n")
        await asyncio.sleep(0.3)
        return httpx2.Response(200, json={"access_token": "rotated", "refresh_token": "rotated-refresh", "expires_in": 600})
    async with open_database(root / "metadata.sqlite3", StorageSettings(data_root=root)) as db:
        service = McpOAuth(McpOAuthRepository(db.sessions), root, root,
            client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        print(await service.access_token(OAuthBinding.model_validate_json(sys.argv[2])))
asyncio.run(main())
"""
    results = []

    async def obtain():
        result = await run_process([sys.executable, "-c", script, str(tmp_path), binding.model_dump_json()])
        results.append(result.stdout.decode().strip())

    async with create_task_group() as tasks:
        tasks.start_soon(obtain)
        tasks.start_soon(obtain)
    assert results == ["rotated", "rotated"]
    assert (tmp_path / "refresh-calls").read_text().splitlines() == ["refresh"]
    assert (await repository.get(binding)).bundle.tokens.refresh_token == "rotated-refresh"


async def test_multi_server_resources_bind_root_and_child_without_sharing_accounts(repository, tmp_path):
    import json

    from a13n_harness_ui.composition import AgentCompositionResolver
    from a13n_harness_ui.configuration import load_harness_ui_configuration

    from .test_composition import _catalog, _selection, _write_source

    root = _write_source(tmp_path)
    (tmp_path / "mcp/docs.yaml").write_text(
        json.dumps(
            {
                "schema_version": "2",
                "mcpServers": {
                    "docs": {"url": "https://mcp.example/mcp", "oauth": {"scope": "read"}},
                    "other": {"url": "https://mcp.example/mcp", "oauth": {}},
                },
            }
        )
    )
    source = await load_harness_ui_configuration(root)
    resolver = AgentCompositionResolver(_catalog())
    node = resolver.resolve_run(source, _selection()).root
    child = node.children[0]
    node = node.model_copy(
        update={
            "children": (
                child.model_copy(
                    update={"definition": child.definition.model_copy(update={"mcp_servers": node.mcp_servers})}
                ),
            )
        }
    )
    service = McpOAuth(repository, tmp_path, tmp_path)
    await service.reconcile(source)
    bound = await service.bind_node(node)
    first = bound.mcp_servers[0]
    assert first.oauth_binding is not None
    assert bound.children[0].definition.mcp_servers[0].oauth_binding == first.oauth_binding
    other = await service.bind(
        first.model_copy(
            update={
                "server_id": "mcp-other",
                "transport": source.mcp_servers["mcp-other"].transport,
                "oauth_binding": None,
            }
        )
    )
    assert other.oauth_binding != first.oauth_binding
    assert type(bound).model_validate_json(bound.model_dump_json()) == bound


@pytest.mark.parametrize(
    "transport",
    [
        {"url": "http://127.0.0.1/mcp", "oauth": {}},
        {"url": "https://mcp.example/mcp", "oauth": {"registration": "cimd"}},
        {"url": "https://mcp.example/mcp", "oauth": {}, "headers": {"authorization": {"env": "TOKEN"}}},
    ],
)
async def test_oauth_transport_requires_https_dcr_and_exclusive_authorization(transport):
    from a13n_harness_ui.configuration.models import McpRemoteTransport
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        McpRemoteTransport.model_validate(transport)


@pytest.mark.parametrize("credential", ["missing", "expired"])
async def test_skip_does_not_hide_initial_oauth_preparation_failure(repository, tmp_path, credential):
    from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
    from a13n_harness_ui.composition.models import ResolvedMcpRecipe
    from a13n_harness_ui.configuration.models import McpOAuthConfiguration, McpRemoteTransport
    from a13n_harness_ui.mcp_adapters import HarnessUiMCP
    from pydantic_ai.models.function import FunctionModel

    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx2.Response(400, json={"error": "invalid_grant"})

    oauth = McpOAuth(
        repository,
        tmp_path,
        tmp_path,
        client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    recipe = await oauth.bind(
        ResolvedMcpRecipe(
            server_id="mcp-test",
            on_error="skip",
            transport=McpRemoteTransport(url="https://mcp.example/mcp", oauth=McpOAuthConfiguration()),
        )
    )
    if credential == "expired":
        await repository.save(recipe.oauth_binding, bundle(), expected_version=0)

    async def model(messages, info):
        pytest.fail("Credential preparation must fail before the model runs")
        yield "unreachable"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(HarnessUiMCP(recipe, oauth=oauth),),
    )
    with pytest.raises(OAuthError):
        await executable.run("continue", bindings=RunBindings.embedded())
    assert requests == (["https://auth.example/token"] if credential == "expired" else [])
