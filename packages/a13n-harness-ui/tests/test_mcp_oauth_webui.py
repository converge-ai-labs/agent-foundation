from __future__ import annotations

import json
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.mcp_oauth.models import AuthorizationTarget, OAuthError
from a13n_harness_ui.webui import create_webui

from .test_app import _settings, _write_configuration
from .test_bot import ready

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]
PUBLIC = "https://ui.example"
AUTH = {"Authorization": "Bearer test-only-key"}


def configuration(tmp_path, origins):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "webui:\n  allowed_origins: " + json.dumps(origins) + "\n")
    directory = tmp_path / "mcp"
    directory.mkdir()
    (directory / "test.yaml").write_text(
        'schema_version: "1"\nkind: mcp_server\nid: mcp-test\nname: OAuth test\ntransport:\n  url: https://mcp.example/mcp\n  oauth: {scope: read}\n'
    )
    return root


def handler(request):
    url = str(request.url)
    if url == "https://mcp.example/mcp":
        return httpx2.Response(
            401, headers={"WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example/metadata"'}
        )
    if url == "https://mcp.example/metadata":
        return httpx2.Response(
            200, json={"resource": "https://mcp.example/mcp", "authorization_servers": ["https://auth.example"]}
        )
    if request.method == "GET":
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
        return httpx2.Response(201, json={**json.loads(request.content), "client_id": "client"})
    if url == "https://auth.example/token":
        return httpx2.Response(
            200,
            json={
                "access_token": "private-token",
                "refresh_token": "private-refresh",
                "expires_in": 600,
                "scope": "read",
            },
        )
    raise AssertionError(url)


@asynccontextmanager
async def webui(tmp_path, origins):
    root = configuration(tmp_path, origins)
    opened = None

    @asynccontextmanager
    async def factory():
        nonlocal opened
        async with open_harness_ui_app(
            _settings(tmp_path / "data"), configuration_path=root, host_mode="webui", instrumentation=None
        ) as app:
            opened = app
            app._mcp_oauth.client_factory = lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
            await ready(app)
            yield app

    server = create_webui(factory, api_key="test-only-key")
    async with server.router.lifespan_context(server):
        yield server, opened


async def test_narrow_callback_cookie_auth_and_admin_only_status(tmp_path):
    async with webui(tmp_path, [PUBLIC]) as (server, app):
        transport = httpx.ASGITransport(app=server)
        async with httpx.AsyncClient(transport=transport, base_url=PUBLIC, headers=AUTH) as browser:
            started = await browser.post("/api/mcp-authorization/start", json={"kind": "mcp", "id": "mcp-test"})
            assert started.status_code == 200, started.text
            login = started.json()
            assert login["state"] == "waiting"
            cookie = started.headers["set-cookie"]
            assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
            assert "Path=/oauth/mcp/callback" in cookie
            state = parse_qs(urlsplit(login["authorization_url"]).query)["state"][0]
            callback = "/oauth/mcp/callback?" + str(
                httpx.QueryParams({"state": state, "code": "code", "iss": "https://auth.example"})
            )
            async with httpx.AsyncClient(transport=transport, base_url=PUBLIC) as other:
                assert (await other.get(callback)).status_code == 400
                assert (await other.get("/api/mcp-authorization/status/mcp/mcp-test")).status_code == 401
                assert (await other.get(f"/api/mcp-authorization/attempts/{login['attempt_id']}")).status_code == 401
            result = await browser.get(callback, headers={"Authorization": ""})
            assert result.status_code == 200
            assert "no-store" in result.headers["cache-control"]
            assert "code" not in result.text
            await app.mcp_logins._attempts[login["attempt_id"]].task
            status = await browser.get("/api/mcp-authorization/status/mcp/mcp-test")
            assert status.json()["state"] == "connected"
            assert status.json()["scopes"] == ["read"]
            assert "private-token" not in status.text + started.text
            assert (await browser.get(callback)).status_code == 400
            recipe = await app.mcp_authorization_recipe(AuthorizationTarget(kind="mcp", id="mcp-test"))
            assert (
                await browser.post("/api/mcp-authorization/disconnect", json={"kind": "mcp", "id": "mcp-test"})
            ).status_code == 204
            with pytest.raises(OAuthError):
                await app._mcp_oauth.access_token(recipe.oauth_binding)


async def test_wildcard_origin_does_not_create_oauth_redirect_authority(tmp_path):
    async with webui(tmp_path, ["*"]) as (server, app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server), base_url=PUBLIC, headers=AUTH
        ) as browser:
            response = await browser.post("/api/mcp-authorization/start", json={"kind": "mcp", "id": "mcp-test"})
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "mcp_oauth_origin_required"
            assert not app.mcp_logins._attempts


@pytest.mark.parametrize("stage", ["register", "token"])
@pytest.mark.parametrize("status_code", [200, 400])
async def test_login_rejects_private_error_responses_before_sdk_logging(
    tmp_path, monkeypatch, caplog, stage, status_code
):
    import sys

    original = handler

    def private_response(request):
        if str(request.url) == f"https://auth.example/{stage}":
            return httpx2.Response(status_code, json={"refresh_token": "fictional-sensitive-response"})
        return original(request)

    monkeypatch.setattr(sys.modules[__name__], "handler", private_response)
    async with webui(tmp_path, [PUBLIC]) as (server, app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server), base_url=PUBLIC, headers=AUTH
        ) as browser:
            started = await browser.post("/api/mcp-authorization/start", json={"kind": "mcp", "id": "mcp-test"})
            login = started.json()
            if stage == "token":
                state = parse_qs(urlsplit(login["authorization_url"]).query)["state"][0]
                await browser.get(
                    "/oauth/mcp/callback", params={"state": state, "code": "code", "iss": "https://auth.example"}
                )
            await app.mcp_logins._attempts[login["attempt_id"]].task
            result = app.mcp_logins.status(login["attempt_id"])
            assert result.state == "failed"
            assert "fictional-sensitive-response" not in result.model_dump_json() + caplog.text
            assert (await browser.get("/api/mcp-authorization/status/unknown/mcp-test")).status_code == 422


async def test_oauth_reconciliation_holds_configuration_acceptance_lock(tmp_path, monkeypatch):
    from anyio import Event, create_task_group, wait_all_tasks_blocked

    async with webui(tmp_path, [PUBLIC]) as (_, app):
        entered, release = Event(), Event()
        original = app._retire_mcp_bindings
        first = True

        async def paused(source):
            nonlocal first
            assert app._configuration_lock.locked()
            if first:
                first = False
                entered.set()
                await release.wait()
            await original(source)

        monkeypatch.setattr(app, "_retire_mcp_bindings", paused)
        target = AuthorizationTarget(kind="mcp", id="mcp-test")
        async with create_task_group() as tasks:
            tasks.start_soon(app.start_mcp_authorization, target, PUBLIC)
            await entered.wait()
            resource = tmp_path / "mcp/test.yaml"
            resource.write_text(resource.read_text().replace("https://mcp.example/mcp", "https://new.example/mcp"))
            tasks.start_soon(app.reload_configuration)
            await wait_all_tasks_blocked()
            release.set()
        recipe = await app.mcp_authorization_recipe(target)
        assert recipe.transport.url == "https://new.example/mcp"
        await app.reload_configuration()
        assert (await app.mcp_authorization_recipe(target)).oauth_binding == recipe.oauth_binding


@pytest.mark.parametrize(
    ("kind", "settings", "endpoint", "scope"),
    [
        ("linear", {}, "/mcp", None),
        ("linear", {"access": "read_only"}, "/mcp/readonly", "read"),
        ("notion", {}, "/mcp", None),
    ],
)
@pytest.mark.parametrize("codeact", [False, True])
async def test_thin_oauth_provider_native_and_typed_paths_share_account(
    tmp_path, monkeypatch, kind, settings, endpoint, scope, codeact
):
    import time

    from a13n_harness_ui import mcp_adapters
    from a13n_harness_ui.connector_models import ConnectorConfiguration
    from a13n_harness_ui.connectors.remote_mcp import RemoteMcpService
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from fastmcp import FastMCP
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from .test_mcp_oauth import bundle

    root = _write_configuration(tmp_path)
    root.write_text(
        root.read_text() + f"tools: {{enable_codeact: {str(codeact).lower()}, execution: {{mode: inline}}}}\n"
    )
    server = FastMCP("oauth-native")
    calls = []
    authenticated = []

    @server.tool()
    def lookup(query: str) -> str:
        calls.append(query)
        return "Found " + query

    upstream = server.http_app(path=endpoint, stateless_http=True, json_response=True)

    async def check(request):
        authenticated.append(request.headers.get("Authorization"))
        assert request.headers["Authorization"] == "Bearer fictional-native-access"

    def client_factory(**kwargs):
        kwargs.pop("follow_redirects", None)
        kwargs.pop("configuration", None)
        return httpx2.AsyncClient(
            **kwargs, transport=httpx2.ASGITransport(app=upstream), event_hooks={"request": [check]}
        )

    monkeypatch.setattr(mcp_adapters, "_no_redirect_client", client_factory)
    connector_id = "connector-oauth"
    group = "oauth-documents"
    source = f"""found = await discover_connector_tools(group={group!r}, query='lookup', detail='full')
assert found['tools'][0]['codeact_eligible']
await call_connector_tool(group={group!r}, tool='lookup', arguments={{'query': 'native'}})"""
    commands = [
        ("discover_connector_tools", {"group": group, "query": ""}),
        ("run_code", {"code": source})
        if codeact
        else ("call_connector_tool", {"group": group, "tool": "lookup", "arguments": {"query": "native"}}),
    ]

    async def model(messages, info):
        index = sum(isinstance(part, ToolReturnPart) for message in messages for part in message.parts)
        if index < len(commands):
            name, arguments = commands[index]
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id=f"call-{index}")}
        else:
            yield "done"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with upstream.router.lifespan_context(upstream):
        async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
            await ready(app)
            await app.configure_connector(
                ConnectorConfiguration(connector_id=connector_id, kind=kind, group_name=group, settings=settings),
                expected_version=None,
            )
            controller = app.connectors()._controllers[connector_id]
            assert await controller.capture() is None
            setup = await app.begin_connector_setup(connector_id)
            assert setup.step.actions[0].kind == "oauth"
            recipe = await controller.authorization_recipe()
            assert recipe.transport.url.endswith(endpoint)
            assert recipe.transport.oauth.scope == scope
            private = bundle(expires_at=time.time() + 600).model_copy(update={"resource_url": recipe.transport.url})
            private.tokens.access_token = "fictional-native-access"
            await app._mcp_oauth.repository.save(recipe.oauth_binding, private, expected_version=0)
            captured = await controller.capture()
            assert "fictional-native-access" not in json.dumps(captured)
            thread = await app.create_thread()
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Find the document")
            outcome = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=20)
            assert outcome.status == "completed", outcome
            service = app.connectors().service(connector_id, "mcp", api_version=1, expected_type=RemoteMcpService)
            assert [tool.name for tool in await service.list_tools()] == ["lookup"]
            await service.call_tool("lookup", {"query": "typed"})
            assert calls == ["native", "typed"]
            assert authenticated and set(authenticated) == {"Bearer fictional-native-access"}
            # Rotation changes private credentials, not an immutable account binding.
            await app._mcp_oauth.repository.save(recipe.oauth_binding, private, expected_version=1)
            assert await controller.capture() == captured
            await app.delete_connector(connector_id, expected_version=1)
            with pytest.raises(OAuthError):
                await app._mcp_oauth.repository.get(recipe.oauth_binding)


@pytest.mark.parametrize("initial", ["read_write", "read_only"])
async def test_linear_access_change_requires_fresh_authorization(tmp_path, initial):
    import time

    from a13n_harness_ui.connector_models import ConnectorConfiguration
    from a13n_harness_ui.connectors.linear import Settings

    from .test_mcp_oauth import bundle

    assert Settings().access == "read_write"
    assert Settings.model_json_schema()["properties"]["access"]["default"] == "read_write"
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        await ready(app)
        candidate = ConnectorConfiguration(
            connector_id="connector-linear", kind="linear", group_name="linear", settings={"access": initial}
        )
        await app.configure_connector(candidate, expected_version=None)
        controller = app.connectors()._controllers[candidate.connector_id]
        recipe = await controller.authorization_recipe()
        private = bundle(expires_at=time.time() + 600).model_copy(update={"resource_url": recipe.transport.url})
        await app._mcp_oauth.repository.save(recipe.oauth_binding, private, expected_version=0)
        assert (await controller.status()).state == "ready"
        assert await controller.capture() is not None

        selected = "read_only" if initial == "read_write" else "read_write"
        await app.configure_connector(
            candidate.model_copy(update={"settings": {"access": selected}}), expected_version=1
        )
        controller = app.connectors()._controllers[candidate.connector_id]
        replacement = await controller.authorization_recipe()
        assert replacement.transport.url == (
            "https://mcp.linear.app/mcp/readonly" if selected == "read_only" else "https://mcp.linear.app/mcp"
        )
        assert replacement.transport.oauth.scope == ("read" if selected == "read_only" else None)
        assert replacement.oauth_binding != recipe.oauth_binding
        with pytest.raises(OAuthError):
            await app._mcp_oauth.repository.get(recipe.oauth_binding)
        assert (await controller.status()).state == "unconfigured"
        assert await controller.capture() is None
        setup = await app.begin_connector_setup(candidate.connector_id)
        assert setup.step.actions[0].kind == "oauth"
        assert setup.step.actions[0].label == "Connect"
        assert ("Read-only" if selected == "read_only" else "Read and write") in setup.step.description

        private = private.model_copy(update={"resource_url": replacement.transport.url})
        await app._mcp_oauth.repository.save(replacement.oauth_binding, private, expected_version=0)
        assert (await controller.status()).state == "ready"
        assert await controller.capture() is not None
