"""Workspace Model Provider grants: encrypted state, one-shot login and cross-worker rotation."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import anyio
import pytest
from a13n_harness.providers.model.oauth.chatgpt import SCOPES, OpenAIChatGPTCredentials, OpenAIChatGPTOAuthFlow
from a13n_harness.providers.model.oauth.models import ModelAuthenticationError, RefreshNotDispatched
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.providers import callback as hosted_callback
from a13n_service.resources.providers import oauth
from a13n_service.resources.providers.tables import ModelProviderOAuthRow
from a13n_service.settings import Settings
from a13n_service.tenancy.tables import GrantRow, PrincipalRow
from sqlalchemy import event, select

from .test_providers import add_workspace, create, principal

pytestmark = pytest.mark.anyio


@pytest.fixture
def no_task_connection(service):
    """Track this task's borrowed connections, not the shared background-worker pool."""
    owners = {}
    pool = service.runtime.storage.engine.sync_engine.pool

    def checkout(connection, record, proxy):
        owners[id(record)] = asyncio.current_task()

    def checkin(connection, record):
        owners.pop(id(record), None)

    def assert_released():
        assert asyncio.current_task() not in owners.values()

    event.listen(pool, "checkout", checkout)
    event.listen(pool, "checkin", checkin)
    try:
        yield assert_released
    finally:
        event.remove(pool, "checkout", checkout)
        event.remove(pool, "checkin", checkin)


def grant(host):
    return OpenAIChatGPTCredentials(
        subject="subject-test",
        client_id="issued-client",
        ext_agent_host_id=host,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=SCOPES,
        access_token="synthetic-access",
        refresh_token="synthetic-refresh",
        id_token="synthetic-id",
        email="test@example.test",
    )


async def connect(service, monkeypatch, no_task_connection, *, legacy_host=False):
    provider = await create(service, "model", {"type": "openai_chatgpt", "name": "ChatGPT"})
    path = f"{service.api}/model-providers/{provider['id']}"
    if legacy_host:
        async with transaction(service.runtime.storage) as session:
            session.add(
                ModelProviderOAuthRow(
                    provider_id=provider["id"],
                    organization_id=service.tenant.organization_id,
                    workspace_id=service.tenant.workspace_id,
                    host_id="host_legacy",
                    refresh_blocked=False,
                )
            )
    started = await service.client.post(path + "/authorize", json={})
    assert started.status_code == 200, started.text
    start = started.json()
    query = parse_qs(urlsplit(start["authorization_url"]).query)
    assert query["agent_name_hint"] == ["Agent Foundation OSS"]
    host_id = query["ext_agent_host_id"][0]
    assert UUID(host_id).version == 4 and UUID(host_id).urn == host_id
    url = (
        query["redirect_uri"][0]
        + "?"
        + urlencode(
            {
                "state": query["state"][0],
                "code": "synthetic-code",
                "client_id": "issued-client",
            }
        )
    )
    calls = []

    async def exchange(self, callback):
        self.validate_callback(callback)
        # The code is consumed durably, and the network boundary holds no connection.
        no_task_connection()
        async with short_session(service.runtime.storage) as session:
            row = await session.get(ModelProviderOAuthRow, provider["id"])
            assert row.pending is None and row.login_claim == start["attempt_id"]
        calls.append(callback)
        return grant(self.authorization.ext_agent_host_id)

    monkeypatch.setattr(OpenAIChatGPTOAuthFlow, "exchange_callback", exchange)
    body = {"attempt_id": start["attempt_id"], "callback_url": url}
    bad = await service.client.post(
        path + "/authorization/callback", json={**body, "callback_url": url.replace(query["state"][0], "wrong")}
    )
    assert bad.status_code == 400 and "synthetic-code" not in bad.text
    response = await service.client.post(path + "/authorization/callback", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "connected"
    assert "synthetic-" not in response.text
    replay = await service.client.post(path + "/authorization/callback", json=body)
    assert replay.status_code == 409 and len(calls) == 1
    source = oauth.ChatGPTCredentialSource(
        service.runtime.storage, service.runtime.keys, provider["id"], service.tenant.organization_id
    )
    return provider, path, source


@pytest.mark.parametrize("legacy_host", [False, True])
async def test_provider_login_is_shared_encrypted_and_does_not_change_resource_version(
    service, monkeypatch, no_task_connection, legacy_host
):
    provider, path, source = await connect(service, monkeypatch, no_task_connection, legacy_host=legacy_host)
    assert (await service.client.get(path)).json()["version"] == provider["version"]
    async with short_session(service.runtime.storage) as session:
        row = await session.get(ModelProviderOAuthRow, provider["id"])
        assert row and row.tokens and row.pending is None
        assert "synthetic-" not in json.dumps(row.tokens)
        location = SecretLocation(service.tenant.organization_id, "model_provider_oauth", "tokens", provider["id"])
        payload = service.runtime.keys.reveal(Envelope.model_validate(row.tokens), location)
        assert json.loads(payload)["access_token"] == "synthetic-access"
        with pytest.raises(ServiceError):
            service.runtime.keys.reveal(Envelope.model_validate(row.tokens), replace(location, column="pending"))
    viewer = principal(service, (service.tenant.workspace_id, "viewer"))
    assert (
        await oauth.status(service.runtime.storage, viewer, service.tenant.workspace_id, provider["id"])
    ).state == "connected"
    with pytest.raises(ServiceError):
        await oauth.authorize(
            service.runtime.storage,
            viewer,
            service.tenant.workspace_id,
            provider["id"],
            oauth.ProviderAuthorizationRequest(),
            keys=service.runtime.keys,
            settings=service.runtime.settings.providers,
            public_origin=service.runtime.settings.server.public_origin,
        )
    other = await add_workspace(service)
    response = await service.client.get(path + "/authorization", headers={"x-workspace-id": other})
    assert response.status_code == 404
    with pytest.raises(ModelAuthenticationError):
        await oauth.ChatGPTCredentialSource(
            service.runtime.storage, service.runtime.keys, provider["id"], "org_other"
        ).load()
    assert (await source.load()).subject == "subject-test"


async def test_logout_clears_tokens_before_revocation_and_retains_registration(
    service, monkeypatch, no_task_connection
):
    _, path, source = await connect(service, monkeypatch, no_task_connection)
    current = await source.load()

    async def revoke(credentials, **kwargs):
        assert credentials == current
        no_task_connection()
        with pytest.raises(ModelAuthenticationError):
            await source.load()
        raise OSError("revocation unavailable")

    monkeypatch.setattr(oauth, "revoke_chatgpt_credentials", revoke)
    disconnected = await service.client.delete(path + "/authorization")
    assert disconnected.status_code == 200 and disconnected.json()["revocation_confirmed"] is False
    started = (await service.client.post(path + "/authorize", json={})).json()
    params = parse_qs(urlsplit(started["authorization_url"]).query)
    assert params["client_id"] == [current.client_id]
    assert params["ext_agent_host_id"] == [current.ext_agent_host_id]
    fresh = (await service.client.post(path + "/authorize", json={"new_registration": True})).json()
    assert parse_qs(urlsplit(fresh["authorization_url"]).query)["client_id"] == ["dynamic_agent_client"]


async def test_cross_worker_rotation_exchanges_once_and_fresh_load_adopts_publication(
    service, monkeypatch, no_task_connection
):
    provider, _, source = await connect(service, monkeypatch, no_task_connection)
    expected = await source.load()
    calls, results = [], []

    async def exchange(current):
        calls.append(current)
        await anyio.sleep(0.03)
        return replace(current, access_token="rotated-access", refresh_token="rotated-refresh")

    async def rotate():
        worker = oauth.ChatGPTCredentialSource(
            service.runtime.storage, service.runtime.keys, provider["id"], service.tenant.organization_id
        )
        results.append(await worker.rotate(expected, exchange))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(rotate)
        tasks.start_soon(rotate)
    assert len(calls) == 1 and len(results) == 2
    assert results[0] == results[1] == await source.load()


@pytest.mark.parametrize("dispatched", [False, True])
async def test_refresh_failure_blocks_only_dispatched_grants(service, monkeypatch, no_task_connection, dispatched):
    _, _, source = await connect(service, monkeypatch, no_task_connection)
    expected = await source.load()

    async def exchange(current):
        no_task_connection()
        if dispatched:
            raise OSError("unknown response")
        raise RefreshNotDispatched("openai-chatgpt", "not sent")

    with pytest.raises(OSError if dispatched else RefreshNotDispatched):
        await source.rotate(expected, exchange)
    if dispatched:
        with pytest.raises(ModelAuthenticationError, match="unknown"):
            await source.load()
    else:
        assert await source.load() == expected


async def test_disconnect_fences_an_inflight_refresh_and_interrupted_claim_is_visible(
    service, monkeypatch, no_task_connection
):
    provider, path, source = await connect(service, monkeypatch, no_task_connection)
    expected = await source.load()

    async def revoke(*args, **kwargs):
        return None

    monkeypatch.setattr(oauth, "revoke_chatgpt_credentials", revoke)

    async def exchange(current):
        assert (await service.client.delete(path + "/authorization")).status_code == 200
        return replace(current, access_token="late-access")

    with pytest.raises(ModelAuthenticationError, match="changed"):
        await source.rotate(expected, exchange)
    with pytest.raises(ModelAuthenticationError):
        await source.load()
    async with transaction(service.runtime.storage) as session:
        row = await session.get(ModelProviderOAuthRow, provider["id"])
        row.tokens = oauth._protect(row, service.runtime.keys, "tokens", expected)
        row.refresh_claim = "grant-interrupted"
        row.refresh_started_at = datetime.now(UTC) - timedelta(minutes=4)
    with pytest.raises(ModelAuthenticationError, match="interrupted"):
        await source.load()
    assert (await service.client.get(path + "/authorization")).json()["state"] == "reauthentication_required"


@pytest.mark.parametrize("streaming", [False, True])
async def test_service_runtime_uses_provider_owned_source_and_native_required_stream(
    service, monkeypatch, no_task_connection, streaming
):
    from contextlib import asynccontextmanager

    import httpx2
    from a13n_harness.models.chatgpt import OpenAIChatGPTResponsesModel
    from a13n_service.resources.models import runtime as models_runtime
    from a13n_service.resources.models.service import resolve_model
    from a13n_service.tenancy.authorize import WorkspaceScope
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai.models import ModelRequestParameters

    provider, _, source = await connect(service, monkeypatch, no_task_connection)
    created = await service.client.post(
        service.api + "/models",
        json={
            "provider_id": provider["id"],
            "key": "chatgpt-test",
            "name": "ChatGPT Test",
            "config": {"model_name": "gpt-test", "model_api": "openai.responses"},
        },
    )
    assert created.status_code == 201, created.text
    async with short_session(service.runtime.storage) as session:
        resolved = await resolve_model(
            session,
            principal(service, (service.tenant.workspace_id, "admin")),
            WorkspaceScope(service.tenant.organization_id, service.tenant.workspace_id),
            "chatgpt-test",
        )
    payload = {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "model": "gpt-test",
        "status": "completed",
        "output": [],
        "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
    }
    events = [
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": {**payload, "status": "in_progress", "usage": None},
        },
        {"type": "response.completed", "sequence_number": 1, "response": payload},
    ]
    requests = []

    def respond(request):
        no_task_connection()
        assert request.headers["authorization"] == "Bearer synthetic-access"
        requests.append(json.loads(request.content))
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(event) + "\n\n" for event in events),
        )

    @asynccontextmanager
    async def http(*args, **kwargs):
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            yield client

    monkeypatch.setattr(models_runtime, "open_http", http)
    async with models_runtime.open_model(
        resolved,
        registry=service.runtime.registry,
        keys=service.runtime.keys,
        policy=service.runtime.endpoint_policy,
        settings=service.runtime.settings.providers,
        storage=service.runtime.storage,
    ) as model:
        assert isinstance(model, OpenAIChatGPTResponsesModel)
        messages = [ModelRequest(parts=[UserPromptPart("Hello")])]
        if streaming:
            async with model.request_stream(messages, None, ModelRequestParameters()) as response:
                async for _ in response:
                    pass
                result = response.get()
        else:
            result = await model.request(messages, None, ModelRequestParameters())
    assert result.usage.input_tokens == 1
    assert requests[0]["store"] is False and requests[0]["stream"] is True
    assert isinstance(requests[0]["input"], list) and "previous_response_id" not in requests[0]
    assert (await source.load()).subject == "subject-test"


def configure_client(service, client_id="approved-client", redirect_uri=None):
    values = service.runtime.settings.model_dump()
    values["server"]["public_url"] = "https://service.test"
    values["providers"].update(
        chatgpt_client_id=client_id,
        chatgpt_redirect_uri=redirect_uri or "https://service.test" + hosted_callback.CALLBACK_PATH,
    )
    service.runtime = replace(service.runtime, settings=Settings.model_validate(values))
    service.app.state.runtime = service.runtime
    # The shared fixture logged in while its public URL was HTTP. Preserve that
    # session under the HTTPS cookie name when configuring this test deployment.
    if session := service.client.cookies.get("a13n_session"):
        service.client.cookies.set("__Host-a13n_session", session, domain="service.test", path="/")


async def start_hosted(service, redirect_uri=None):
    configure_client(service, redirect_uri=redirect_uri)
    provider = await create(service, "model", {"type": "openai_chatgpt", "name": "Hosted ChatGPT"})
    path = f"{service.api}/model-providers/{provider['id']}"
    response = await service.client.post(path + "/authorize", json={})
    assert response.status_code == 200, response.text
    start = response.json()
    query = parse_qs(urlsplit(start["authorization_url"]).query)
    assert start["method"] == "browser_callback"
    assert query["client_id"] == ["approved-client"] and "agent_name_hint" not in query
    name = hosted_callback.cookie_name(query["state"][0])
    cookie = response.cookies[name]
    assert all(
        value in response.headers["set-cookie"] for value in ("HttpOnly", "Secure", "SameSite=lax", "Max-Age=600")
    )
    assert f"Path={urlsplit(query['redirect_uri'][0]).path}" in response.headers["set-cookie"]
    url = hosted_callback.CALLBACK_PATH + "?" + urlencode({"state": query["state"][0], "code": "synthetic-code"})
    return provider, path, start, url, name, cookie


@pytest.mark.parametrize("client_id", ["issued-client", "another-client"])
async def test_configured_client_preserves_only_matching_registration(
    service, monkeypatch, no_task_connection, client_id
):
    _, path, _ = await connect(service, monkeypatch, no_task_connection)
    configure_client(service, client_id)
    started = (await service.client.post(path + "/authorize", json={})).json()
    params = parse_qs(urlsplit(started["authorization_url"]).query)
    assert params["client_id"] == [client_id] and "agent_name_hint" not in params
    assert ("id_token_hint" in params) == (client_id == "issued-client")
    fresh = (await service.client.post(path + "/authorize", json={"new_registration": True})).json()
    params = parse_qs(urlsplit(fresh["authorization_url"]).query)
    assert params["client_id"] == [client_id]
    assert "id_token_hint" not in params and "login_hint" not in params


async def test_hosted_callback_uses_only_browser_flow_cookie_without_session_or_workspace_header(
    service, monkeypatch, no_task_connection
):
    import httpx2

    provider, path, start, url, name, cookie = await start_hosted(service)
    calls = []

    async def exchange(self, callback):
        self.validate_callback(callback)
        no_task_connection()
        async with short_session(service.runtime.storage) as session:
            row = await session.get(ModelProviderOAuthRow, provider["id"])
            assert row.pending is None and row.login_claim == start["attempt_id"]
        calls.append(callback)
        return replace(grant(self.authorization.ext_agent_host_id), client_id="approved-client")

    monkeypatch.setattr(OpenAIChatGPTOAuthFlow, "exchange_callback", exchange)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test"
    ) as browser:
        # SameSite=Strict session cookies do not return on the issuer's redirect.
        response = await browser.get(url, headers={"cookie": f"{name}={cookie}", "host": "untrusted.test"})
        assert response.status_code == 200, response.text
        assert "ChatGPT sign-in complete" in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "Max-Age=0" in response.headers["set-cookie"]
        assert all(value not in response.text for value in ("synthetic-code", "approved-client", "subject-test"))
        assert len(calls) == 1
        assert calls[0].startswith("https://service.test" + hosted_callback.CALLBACK_PATH)
        replay = await browser.get(url, headers={"cookie": f"{name}={cookie}"})
        assert replay.status_code == 409 and len(calls) == 1
    status = (await service.client.get(path + "/authorization")).json()
    assert status["state"] == "connected" and not status["pending"]
    # Existing grants refresh with their saved client, independent of new login configuration.
    source = oauth.ChatGPTCredentialSource(
        service.runtime.storage, service.runtime.keys, provider["id"], service.tenant.organization_id
    )
    current = await source.load()
    configure_client(service, "changed-client")

    async def refresh(credentials):
        no_task_connection()
        assert credentials.client_id == "approved-client"
        return replace(credentials, access_token="rotated")

    assert (await source.rotate(current, refresh)).client_id == "approved-client"


async def test_hosted_callback_rejects_missing_tampered_and_other_attempt_cookies_before_consumption(
    service, monkeypatch
):
    import httpx2

    provider, path, _, url, name, cookie = await start_hosted(service)
    _, _, _, _, _, other_cookie = await start_hosted(service)
    calls = []

    async def exchange(*args):
        calls.append(args)
        raise AssertionError("must not reach the provider")

    monkeypatch.setattr(OpenAIChatGPTOAuthFlow, "exchange_callback", exchange)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test"
    ) as browser:
        for supplied, target in (
            (None, url),
            ("tampered", url),
            (other_cookie, url),
            (cookie, url + "&state=duplicate"),
            (cookie, url.replace("code=synthetic-code", "error=access_denied")),
        ):
            headers = {"cookie": f"{name}={supplied}"} if supplied is not None else {}
            response = await browser.get(target, headers=headers)
            assert response.status_code == 400, response.text
            assert "synthetic-code" not in response.text
            assert response.headers["referrer-policy"] == "no-referrer"
    assert calls == []
    assert (await service.client.get(path + "/authorization")).json()["pending"]
    async with short_session(service.runtime.storage) as session:
        row = await session.get(ModelProviderOAuthRow, provider["id"])
        assert row.pending is not None and row.login_claim is None


@pytest.mark.parametrize("change", ["disabled", "viewer"])
async def test_hosted_callback_rechecks_initiator_status_and_provider_write(service, monkeypatch, change):
    import httpx2

    provider, _, _, url, name, cookie = await start_hosted(service)
    async with transaction(service.runtime.storage) as session:
        if change == "disabled":
            row = await session.get(PrincipalRow, service.tenant.principal_id)
            row.status = "disabled"
        else:
            rows = (
                await session.scalars(select(GrantRow).where(GrantRow.principal_id == service.tenant.principal_id))
            ).all()
            for row in rows:
                await session.delete(row)
            await session.flush()
            session.add(
                GrantRow(
                    id=new_object_id("rb"),
                    organization_id=service.tenant.organization_id,
                    workspace_id=service.tenant.workspace_id,
                    principal_id=service.tenant.principal_id,
                    role="viewer",
                    created_by_id=service.tenant.principal_id,
                )
            )

    async def exchange(*args):
        raise AssertionError("must not reach the provider")

    monkeypatch.setattr(OpenAIChatGPTOAuthFlow, "exchange_callback", exchange)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test"
    ) as browser:
        response = await browser.get(url, headers={"cookie": f"{name}={cookie}"})
        assert response.status_code == (401 if change == "disabled" else 403), response.text
    async with short_session(service.runtime.storage) as session:
        row = await session.get(ModelProviderOAuthRow, provider["id"])
        assert row.pending is not None and row.tokens is None


@pytest.mark.parametrize("operation", ["replace", "disconnect", "configure"])
async def test_hosted_callback_publication_is_fenced_by_replacement_or_disconnect(
    service, monkeypatch, no_task_connection, operation
):
    provider, path, _, url, name, cookie = await start_hosted(service)

    async def exchange(self, callback):
        self.validate_callback(callback)
        no_task_connection()
        assert (await service.client.get(path + "/authorization")).json()["pending"]
        if operation == "replace":
            assert (await service.client.post(path + "/authorize", json={})).status_code == 200
        elif operation == "disconnect":
            assert (await service.client.delete(path + "/authorization")).status_code == 200
        else:
            changed = await service.client.patch(
                path,
                json={"config": {"client_id": "new-client"}},
                headers={"if-match": f'"{provider["id"]}:{provider["version"]}"'},
            )
            assert changed.status_code == 200, changed.text
        return replace(grant(self.authorization.ext_agent_host_id), client_id="approved-client")

    monkeypatch.setattr(OpenAIChatGPTOAuthFlow, "exchange_callback", exchange)
    response = await service.client.get(url, headers={"cookie": f"{name}={cookie}"})
    assert response.status_code == 409, response.text
    async with short_session(service.runtime.storage) as session:
        row = await session.get(ModelProviderOAuthRow, provider["id"])
        assert row.tokens is None


async def test_hosted_exchange_failure_does_not_report_old_credentials_as_completed_login(
    service, monkeypatch, no_task_connection
):
    _, path, _ = await connect(service, monkeypatch, no_task_connection)
    configure_client(service, "issued-client")
    start = (await service.client.post(path + "/authorize", json={})).json()
    state = parse_qs(urlsplit(start["authorization_url"]).query)["state"][0]

    async def exchange(*args):
        no_task_connection()
        raise OSError("unknown outcome")

    monkeypatch.setattr(OpenAIChatGPTOAuthFlow, "exchange_callback", exchange)
    url = hosted_callback.CALLBACK_PATH + "?" + urlencode({"state": state, "code": "synthetic-code"})
    assert (await service.client.get(url)).status_code == 503
    status = (await service.client.get(path + "/authorization")).json()
    assert status["state"] == "connected" and status["pending"] and status["message"]
    assert (await service.client.post(path + "/authorize", json={})).status_code == 200


async def test_configured_loopback_path_remains_manual_and_api_keys_cannot_start_hosted_flow(service):
    configure_client(service, redirect_uri="http://127.0.0.1:18888/custom/callback")
    provider = await create(service, "model", {"type": "openai_chatgpt", "name": "Custom"})
    path = f"{service.api}/model-providers/{provider['id']}"
    response = await service.client.post(path + "/authorize", json={})
    assert response.status_code == 200 and response.json()["method"] == "manual_callback"
    assert "__Secure-a13n_chatgpt" not in response.headers.get("set-cookie", "")
    params = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    assert params["redirect_uri"] == ["http://127.0.0.1:18888/custom/callback"]
    configure_client(service)
    actor = principal(service, (service.tenant.workspace_id, "admin"), confined_to=service.tenant.workspace_id)
    with pytest.raises(ServiceError, match="login session"):
        await oauth.authorize(
            service.runtime.storage,
            actor,
            service.tenant.workspace_id,
            provider["id"],
            oauth.ProviderAuthorizationRequest(),
            keys=service.runtime.keys,
            settings=service.runtime.settings.providers,
            public_origin=service.runtime.settings.server.public_origin,
        )


async def test_custom_public_callback_path_keeps_registered_uri_after_proxy_rewrite(
    service, monkeypatch, no_task_connection
):
    _, _, _, url, name, cookie = await start_hosted(service, "https://service.test/custom/chatgpt/callback")

    async def exchange(self, callback):
        self.validate_callback(callback)
        no_task_connection()
        assert callback.startswith("https://service.test/custom/chatgpt/callback?")
        return replace(grant(self.authorization.ext_agent_host_id), client_id="approved-client")

    monkeypatch.setattr(OpenAIChatGPTOAuthFlow, "exchange_callback", exchange)
    # A proxy rewrites the path, not the registered exchange URI or browser cookie.
    response = await service.client.get(
        url, headers={"cookie": f"{name}={cookie}", "x-forwarded-host": "untrusted.test"}
    )
    assert response.status_code == 200, response.text
    assert "Path=/custom/chatgpt/callback" in response.headers["set-cookie"]


@pytest.mark.parametrize("expired", ["cookie", "pending"])
async def test_hosted_callback_expiry_never_consumes_the_grant(service, expired):
    provider, _, start, url, name, cookie = await start_hosted(service)
    if expired == "cookie":
        actor = principal(service, (service.tenant.workspace_id, "admin"))
        _, cookie = hosted_callback.browser_cookie(
            oauth.AuthorizationStart.model_validate({**start, "expires_at": datetime.now(UTC) - timedelta(seconds=1)}),
            actor,
            service.tenant.workspace_id,
            provider["id"],
            keys=service.runtime.keys,
        )
    else:
        async with transaction(service.runtime.storage) as session:
            row = await session.get(ModelProviderOAuthRow, provider["id"])
            pending = oauth._PENDING.validate_json(
                service.runtime.keys.reveal(Envelope.model_validate(row.pending), oauth._location(row, "pending"))
            )
            row.pending = oauth._protect(
                row,
                service.runtime.keys,
                "pending",
                replace(pending, expires_at=datetime.now(UTC) - timedelta(seconds=1)),
            )
    response = await service.client.get(url, headers={"cookie": f"{name}={cookie}"})
    assert response.status_code == 400, response.text
    async with short_session(service.runtime.storage) as session:
        row = await session.get(ModelProviderOAuthRow, provider["id"])
        assert row.pending is not None and row.login_claim is None


@pytest.mark.parametrize("confidential", [False, True])
async def test_provider_registration_overrides_defaults_and_freezes_client_authentication(
    service, monkeypatch, no_task_connection, confidential
):
    import httpx2

    configure_client(service)
    # Deployment defaults differ from this provider's registered application.
    config = {
        "client_id": "provider-client",
        "redirect_uri": "https://service.test/provider-specific/callback",
        "token_endpoint_auth_method": "client_secret_basic" if confidential else "none",
    }
    secret = "synthetic-provider-secret"
    provider = await create(
        service,
        "model",
        {
            "type": "openai_chatgpt",
            "name": "Custom registration",
            "config": config,
            **({"credential": {"client_secret": secret}} if confidential else {}),
        },
    )
    assert provider["config"] == {key: value for key, value in config.items() if value != "none"}
    assert provider["credential_configured"] == confidential
    path = f"{service.api}/model-providers/{provider['id']}"
    assert secret not in (await service.client.get(path)).text
    response = await service.client.post(path + "/authorize", json={})
    assert response.status_code == 200, response.text
    start = response.json()
    params = parse_qs(urlsplit(start["authorization_url"]).query)
    assert params["client_id"] == ["provider-client"]
    assert params["redirect_uri"] == [config["redirect_uri"]]
    assert secret not in response.text and "client_secret" not in params
    name = hosted_callback.cookie_name(params["state"][0])
    cookie = response.cookies[name]
    assert secret not in cookie and config["redirect_uri"] not in cookie
    calls = []

    async def exchange(self, callback):
        no_task_connection()
        self.validate_callback(callback)
        assert self.authorization.client_secret == (secret if confidential else None)
        assert self.authorization.token_endpoint_auth_method == config["token_endpoint_auth_method"]
        assert callback.startswith(config["redirect_uri"] + "?")
        calls.append(callback)
        return replace(
            grant(self.authorization.ext_agent_host_id),
            client_id="provider-client",
            token_endpoint_auth_method=self.authorization.token_endpoint_auth_method,
            client_secret=self.authorization.client_secret,
        )

    monkeypatch.setattr(OpenAIChatGPTOAuthFlow, "exchange_callback", exchange)
    query = urlencode({"state": params["state"][0], "code": "synthetic-code"})
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test"
    ) as browser:
        completed = await browser.get(
            hosted_callback.CALLBACK_PATH + "?" + query, headers={"cookie": f"{name}={cookie}"}
        )
    assert completed.status_code == 200 and len(calls) == 1
    assert "Path=/provider-specific/callback" in completed.headers["set-cookie"]
    source = oauth.ChatGPTCredentialSource(
        service.runtime.storage, service.runtime.keys, provider["id"], service.tenant.organization_id
    )
    saved = await source.load()
    assert saved.client_secret == (secret if confidential else None)
    # Frozen renewable credentials do not read a later provider or deployment secret.
    if confidential:
        patched = await service.client.patch(
            path,
            json={"credential": {"client_secret": "replacement-secret"}},
            headers={"if-match": f'"{provider["id"]}:{provider["version"]}"'},
        )
        assert patched.status_code == 200, patched.text
        assert (await source.load()).client_secret == secret


@pytest.mark.parametrize(
    "config,credential",
    [
        ({"client_id": "client", "token_endpoint_auth_method": "client_secret_basic"}, None),
        ({"token_endpoint_auth_method": "client_secret_basic"}, {"client_secret": "secret"}),
        ({"client_id": "client"}, {"client_secret": "secret"}),
        ({"client_id": "client", "redirect_uri": "http://external.test/callback"}, None),
        ({"client_id": "client", "token_endpoint_auth_method": "client_secret_post"}, {"client_secret": "secret"}),
    ],
)
async def test_provider_registration_rejects_missing_or_inconsistent_client_inputs(service, config, credential):
    response = await service.client.post(
        f"{service.api}/model-providers",
        json={"type": "openai_chatgpt", "name": "Invalid", "config": config, "credential": credential},
    )
    assert response.status_code == 400, response.text
    assert '"secret"' not in response.text


async def test_provider_callback_must_share_public_origin_and_never_widens_oss_registration(service):
    provider = await create(
        service,
        "model",
        {
            "type": "openai_chatgpt",
            "name": "Custom",
            "config": {"client_id": "client", "redirect_uri": "https://other.test/callback"},
        },
    )
    path = f"{service.api}/model-providers/{provider['id']}"
    assert (await service.client.post(path + "/authorize", json={})).status_code == 400
    provider = await create(
        service,
        "model",
        {"type": "openai_chatgpt", "name": "OSS", "config": {"redirect_uri": "https://service.test/callback"}},
    )
    path = f"{service.api}/model-providers/{provider['id']}"
    assert (await service.client.post(path + "/authorize", json={})).status_code == 400


async def test_discovery_route_keeps_wire_shape_and_account_order_without_borrowing_database(
    service, monkeypatch, no_task_connection
):
    from contextlib import asynccontextmanager

    import httpx2
    from a13n_service.resources.providers import discovery

    provider, path, _ = await connect(service, monkeypatch, no_task_connection)
    requests = []

    def respond(request):
        no_task_connection()
        requests.append(request)
        assert str(request.url) == "https://api.openai.com/v1/models"
        return httpx2.Response(
            200,
            json={
                "models": [
                    {"slug": "plan-z", "display_name": "Z plan", "visibility": "list"},
                    {"slug": "hidden", "display_name": "Hidden", "visibility": "hidden"},
                    {"slug": "plan-a", "display_name": "A plan", "visibility": "list"},
                ]
            },
        )

    @asynccontextmanager
    async def open_http(policy, *, timeout, max_bytes):
        no_task_connection()
        assert timeout == service.runtime.settings.providers.model_timeout
        assert max_bytes == service.runtime.settings.providers.response_bytes
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            yield client

    monkeypatch.setattr(discovery, "open_http", open_http)
    response = await service.client.get(path + "/models")
    assert response.status_code == 200, response.text
    assert response.json() == [
        {"slug": "plan-z", "display_name": "Z plan"},
        {"slug": "plan-a", "display_name": "A plan"},
    ]
    assert len(requests) == 1
    current = (await service.client.get(path)).json()
    assert current["version"] == provider["version"]
    assert current["credential_configured"] is False
    types = (await service.client.get(service.api + "/provider-types/model")).json()["items"]
    chatgpt = next(item for item in types if item["type"] == "openai_chatgpt")
    assert chatgpt["supports_model_discovery"] is True
    assert chatgpt["supports_test"] is False and chatgpt["catalog_providers"] == []
    assert next(item for item in types if item["type"] == "openai")["supports_model_discovery"] is False


async def test_discovery_checks_capability_scope_and_run_permission_before_network(service, monkeypatch):
    from a13n_service.resources.providers import discovery

    def no_network(*args, **kwargs):
        pytest.fail("Unauthorized or unsupported discovery must not open a client")

    monkeypatch.setattr(discovery, "open_http", no_network)
    provider = await create(
        service, "model", {"type": "openai", "name": "API", "credential": {"api_key": "synthetic-key"}}
    )
    path = f"{service.api}/model-providers/{provider['id']}/models"
    response = await service.client.get(path)
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_argument"
    other = await add_workspace(service)
    response = await service.client.get(path, headers={"x-workspace-id": other})
    assert response.status_code == 404
    workspace = service.tenant.workspace_id
    with pytest.raises(ServiceError) as failure:
        await discovery.discover_models(
            service.runtime.storage,
            principal(service, (workspace, "viewer")),
            workspace,
            provider["id"],
            registry=service.runtime.registry,
            keys=service.runtime.keys,
            policy=service.runtime.endpoint_policy,
            settings=service.runtime.settings.providers,
        )
    assert failure.value.code == "forbidden"


async def test_discovery_without_authorization_is_safe_and_does_not_block_manual_model_creation(service):
    provider = await create(service, "model", {"type": "openai_chatgpt", "name": "Plan"})
    response = await service.client.get(f"{service.api}/model-providers/{provider['id']}/models")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"
    response = await service.client.post(
        service.api + "/models",
        json={
            "provider_id": provider["id"],
            "name": "Manual",
            "config": {"model_name": "manual-plan", "model_api": "openai.responses"},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["catalog_ref"] is None and response.json()["pricing"] is None


async def test_discovery_dispatches_declared_operation_without_a_vendor_table(service, monkeypatch):
    from a13n_harness.providers.model.definition import DiscoveredModel
    from a13n_service.providers.registry import Registry
    from a13n_service.resources.providers import discovery

    provider = await create(
        service, "model", {"type": "openai", "name": "API", "credential": {"api_key": "synthetic-key"}}
    )
    calls = []

    async def list_models(connection, source, client):
        calls.append(connection)
        assert source is None
        return (DiscoveredModel(model_name="custom-upstream", display_name="Custom upstream"),)

    definition = replace(service.runtime.registry.get("model", "openai"), model_discovery=list_models)
    registry = Registry.of([definition])
    choices = await discovery.discover_models(
        service.runtime.storage,
        principal(service, (service.tenant.workspace_id, "admin")),
        service.tenant.workspace_id,
        provider["id"],
        registry=registry,
        keys=service.runtime.keys,
        policy=service.runtime.endpoint_policy,
        settings=service.runtime.settings.providers,
    )
    assert [choice.model_dump() for choice in choices] == [
        {"slug": "custom-upstream", "display_name": "Custom upstream"}
    ]
    assert len(calls) == 1
