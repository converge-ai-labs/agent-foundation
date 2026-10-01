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
from a13n_service.resources.providers import oauth
from a13n_service.resources.providers.tables import ModelProviderOAuthRow
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
