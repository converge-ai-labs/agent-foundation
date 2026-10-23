"""Workspace Model Provider grants: encrypted state, one-shot login and cross-worker rotation."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import httpx2
import jwt
import pytest
from a13n_harness.providers.model.oauth.chatgpt import ChatGPTAuthorization, credential_metadata
from a13n_harness.providers.model.oauth.models import ModelAuthenticationError
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.providers import oauth
from a13n_service.resources.providers.tables import ModelProviderOAuthRow
from a13n_service.settings import Settings
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentials, OpenAIChatGPTProvider
from sqlalchemy import event

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
        client_id="issued-client",
        scope="resource.invoke chatgpt.tokens.use.direct",
        access_token="synthetic-access",
        refresh_token="synthetic-refresh",
        id_token=jwt.encode({"sub": "subject-test", "email": "test@example.test"}, "", algorithm="none"),
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
        return grant(self.ext_agent_host_id)

    monkeypatch.setattr(ChatGPTAuthorization, "exchange_callback", exchange)
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
        await oauth.status(
            service.runtime.storage, viewer, service.tenant.workspace_id, provider["id"], keys=service.runtime.keys
        )
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
        )
    other = await add_workspace(service)
    response = await service.client.get(path + "/authorization", headers={"x-workspace-id": other})
    assert response.status_code == 404
    with pytest.raises(ModelAuthenticationError):
        await oauth.ChatGPTCredentialSource(
            service.runtime.storage, service.runtime.keys, provider["id"], "org_other"
        ).load()
    assert credential_metadata(await source.load())[0] == "subject-test"


@pytest.mark.parametrize("streaming", [False, True])
async def test_service_runtime_uses_provider_owned_source_and_native_required_stream(
    service, monkeypatch, no_task_connection, streaming
):
    from contextlib import asynccontextmanager

    import httpx2
    from a13n_service.resources.models import runtime as models_runtime
    from a13n_service.resources.models.service import resolve_model
    from a13n_service.tenancy.authorize import WorkspaceScope
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.models.openai import OpenAIResponsesModel

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
        assert isinstance(model, OpenAIResponsesModel)
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
    assert credential_metadata(await source.load())[0] == "subject-test"


async def test_discovery_route_prefills_metadata_without_changing_account_order_or_borrowing_database(
    service, monkeypatch, no_task_connection
):
    from contextlib import asynccontextmanager

    import httpx2
    from a13n_service.resources.models.schemas import ModelCatalog
    from a13n_service.resources.providers import discovery

    provider, path, _ = await connect(service, monkeypatch, no_task_connection)
    metadata = service.app.state.model_catalog
    metadata.catalog = ModelCatalog.model_validate(
        {
            "status": "ready",
            "items": [
                {
                    "ref": {"provider": "openai", "model": "plan-z"},
                    "identity": "openai/plan-z",
                    "name": "Public label",
                    "provider_name": "OpenAI",
                    "release_date": "2026-09-29",
                    "characteristics": {
                        "capabilities": ["image_understanding", "document_understanding"],
                        "context_window_tokens": 765432,
                    },
                    "pricing": None,
                    "pricing_warning": None,
                }
            ],
        }
    )
    metadata.refresh_after = float("inf")
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
                    {"slug": "gpt-6.1-sol", "display_name": "Account Sol", "visibility": "list"},
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
    choices = response.json()
    assert [(item["slug"], item["display_name"]) for item in choices] == [
        ("plan-z", "Z plan"),
        ("plan-a", "A plan"),
        ("gpt-6.1-sol", "Account Sol"),
    ]
    assert choices[0]["characteristics"]["context_window_tokens"] == 765432
    assert set(choices[0]["characteristics"]["capabilities"]) == {"image_understanding", "document_understanding"}
    assert choices[1]["characteristics"] is None
    assert choices[2]["characteristics"]["context_window_tokens"] == 1050000
    assert choices[2]["characteristics"]["capabilities"] == ["image_understanding"]
    assert all("pricing" not in choice and "catalog_ref" not in choice for choice in choices)
    assert len(requests) == 1
    current = (await service.client.get(path)).json()
    assert current["version"] == provider["version"]
    assert current["credential_configured"] is False
    types = (await service.client.get(service.api + "/provider-types/model")).json()["items"]
    chatgpt = next(item for item in types if item["type"] == "openai_chatgpt")
    assert chatgpt["supports_model_discovery"] is True
    assert chatgpt["supports_test"] is False and chatgpt["catalog_providers"] == ["openai"]
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
            catalog=service.app.state.model_catalog,
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
        catalog=service.app.state.model_catalog,
    )
    assert [choice.model_dump() for choice in choices] == [
        {"slug": "custom-upstream", "display_name": "Custom upstream", "characteristics": None}
    ]
    assert len(calls) == 1


@pytest.mark.parametrize("expired", [False, True])
async def test_official_refresh_saves_encrypted_tokens_and_next_run_reloads(
    service, monkeypatch, no_task_connection, expired
):
    provider, path, source = await connect(service, monkeypatch, no_task_connection)
    original = await source.load()
    original = replace(
        original,
        access_token=jwt.encode(
            {"exp": (datetime.now(UTC) + timedelta(hours=-1 if expired else 1)).timestamp()}, "", algorithm="none"
        ),
    )
    await source.save(original)
    calls = []

    def respond(request):
        no_task_connection()
        calls.append(request.url.host)
        if request.url.host == "auth.openai.com":
            assert b"grant_type=refresh_token" in request.content
            return httpx2.Response(
                200, json={"access_token": "replacement-access", "refresh_token": "replacement-refresh"}
            )
        if request.headers["authorization"] != "Bearer replacement-access":
            return httpx2.Response(401)
        return httpx2.Response(200, json={"models": []})

    for _ in range(2):
        fresh = oauth.ChatGPTCredentialSource(
            service.runtime.storage, service.runtime.keys, provider["id"], service.tenant.organization_id
        )
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            official = OpenAIChatGPTProvider(credential_source=fresh, http_client=client)
            await official.client.get("/models", cast_to=dict)
    assert calls.count("auth.openai.com") == 1
    assert (await source.load()).refresh_token == "replacement-refresh"
    assert (await service.client.get(path)).json()["version"] == provider["version"]


async def test_save_rejects_stale_publication_and_disconnect_retains_registration(
    service, monkeypatch, no_task_connection
):
    provider, path, source = await connect(service, monkeypatch, no_task_connection)
    original = await source.load()
    other = oauth.ChatGPTCredentialSource(
        service.runtime.storage, service.runtime.keys, provider["id"], service.tenant.organization_id
    )
    await other.load()
    await other.save(replace(original, access_token="new"))
    with pytest.raises(ModelAuthenticationError, match="changed"):
        await source.save(replace(original, refresh_token="stale"))
    await source.load()
    result = await service.client.delete(path + "/authorization")
    assert result.status_code == 200 and result.json()["revocation_confirmed"] is None
    with pytest.raises(ModelAuthenticationError):
        await source.save(original)
    with pytest.raises(ModelAuthenticationError):
        await source.load()
    started = (await service.client.post(path + "/authorize", json={})).json()
    assert started["method"] == "manual_callback"
    assert parse_qs(urlsplit(started["authorization_url"]).query)["client_id"] == ["issued-client"]
    fresh = (await service.client.post(path + "/authorize", json={"new_registration": True})).json()
    assert parse_qs(urlsplit(fresh["authorization_url"]).query)["client_id"] == ["dynamic_agent_client"]


async def test_removed_hosted_and_confidential_configurations_are_rejected(service):
    for config in (
        {"redirect_uri": "https://service.test/callback"},
        {"client_id": "confidential", "token_endpoint_auth_method": "client_secret_basic"},
    ):
        response = await service.client.post(
            service.api + "/model-providers", json={"type": "openai_chatgpt", "name": "Unsupported", "config": config}
        )
        assert response.status_code == 400
    with pytest.raises(ValueError):
        Settings.model_validate({"providers": {"chatgpt_redirect_uri": "https://service.test/callback"}})


async def test_disconnect_fences_inflight_login_publication(service, monkeypatch, no_task_connection):
    _, path, _ = await connect(service, monkeypatch, no_task_connection)
    start = (await service.client.post(path + "/authorize", json={})).json()
    query = parse_qs(urlsplit(start["authorization_url"]).query)

    async def exchange(self, callback):
        no_task_connection()
        response = await service.client.delete(path + "/authorization")
        assert response.status_code == 200
        return grant(self.ext_agent_host_id)

    monkeypatch.setattr(ChatGPTAuthorization, "exchange_callback", exchange)
    response = await service.client.post(
        path + "/authorization/callback",
        json={
            "attempt_id": start["attempt_id"],
            "callback_url": query["redirect_uri"][0] + "?" + urlencode({"state": query["state"][0], "code": "private"}),
        },
    )
    assert response.status_code == 409
    assert (await service.client.get(path + "/authorization")).json()["state"] == "disconnected"


async def test_declined_plan_usage_is_not_connected_and_reauthorization_requests_consent(
    service, monkeypatch, no_task_connection
):
    _, path, source = await connect(service, monkeypatch, no_task_connection)
    original = await source.load()
    await source.save(replace(original, scope="openid profile"))
    status = await service.client.get(path + "/authorization")
    assert status.status_code == 200
    assert status.json()["state"] == "reauthentication_required"
    started = await service.client.post(path + "/authorize", json={})
    assert started.status_code == 200
    assert parse_qs(urlsplit(started.json()["authorization_url"]).query)["prompt"] == ["consent"]
