"""Host adapters and the official ChatGPT integration, with synthetic HTTP only."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx2
import jwt
import pytest
from a13n_harness.providers.model.oauth.chatgpt import ChatGPTAuthorization, StoredChatGPTCredentials
from a13n_harness.providers.model.openai_chatgpt import Config
from pydantic import TypeAdapter, ValidationError
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai_chatgpt import (
    CredentialsPersistenceError,
    CredentialsRefreshError,
    OpenAIChatGPTCredentials,
    OpenAIChatGPTOAuthFlow,
    OpenAIChatGPTProvider,
)
from pydantic_ai.tools import ToolDefinition

pytestmark = pytest.mark.anyio
RESOURCE = "https://api.openai.com/v1"


def grant(*, expired=False):
    return OpenAIChatGPTCredentials(
        client_id="issued-client",
        access_token=jwt.encode(
            {"exp": (datetime.now(UTC) + timedelta(hours=-1 if expired else 1)).timestamp()}, "", algorithm="none"
        ),
        refresh_token="synthetic-refresh",
        scope="resource.invoke chatgpt.tokens.use.direct",
    )


class MemoryStore:
    def __init__(self, *, expired=False):
        self.current = grant(expired=expired)
        self.saved = []

    async def load(self):
        return self.current

    async def save(self, credentials):
        self.saved.append(credentials)
        self.current = credentials


async def test_pending_roundtrip_and_official_code_exchange(monkeypatch):
    pending = ChatGPTAuthorization.start(host_id=uuid4().urn, redirect_uri="http://127.0.0.1:1456/auth/callback")
    restored = TypeAdapter(ChatGPTAuthorization).validate_json(TypeAdapter(ChatGPTAuthorization).dump_json(pending))
    assert restored.authorization_url() == pending.authorization_url()
    query = parse_qs(urlsplit(pending.authorization_url()).query)
    assert query["client_id"] == ["dynamic_agent_client"]
    callback = (
        pending.redirect_uri
        + "?"
        + urlencode({"state": pending.state, "code": "synthetic-code", "client_id": "issued-client"})
    )

    async def exchange(self, code, *, client_id=None):
        assert code == "synthetic-code" and client_id == "issued-client"
        assert (self.nonce, self.code_verifier, self.state) == (pending.nonce, pending.code_verifier, pending.state)
        return grant()

    monkeypatch.setattr(OpenAIChatGPTOAuthFlow, "exchange_code", exchange)
    assert (await restored.exchange_callback(callback)).client_id == "issued-client"
    for bad in (
        callback.replace(pending.state, "wrong"),
        callback + "&state=other",
        callback.replace("127.0.0.1", "evil.test"),
    ):
        with pytest.raises(UserError):
            restored.validate_callback(bad)
    with pytest.raises(UserError, match="expired"):
        replace(restored, expires_at=datetime.now(UTC) - timedelta(seconds=1)).validate_callback(callback)


def test_legacy_credentials_and_removed_configurations():
    assert Config.model_validate({"token_endpoint_auth_method": "none"}) == Config()
    original = grant()
    legacy = TypeAdapter(OpenAIChatGPTCredentials).dump_python(original)
    legacy["scopes"] = legacy.pop("scope").split()
    legacy.update(subject="display-only", expires_at="2026-01-01T00:00:00Z", token_endpoint_auth_method="none")
    assert TypeAdapter(StoredChatGPTCredentials).validate_python(legacy) == original
    legacy["token_endpoint_auth_method"] = "client_secret_basic"
    with pytest.raises(ValidationError, match="Confidential"):
        TypeAdapter(StoredChatGPTCredentials).validate_python(legacy)
    for config in (
        {"redirect_uri": "https://host.test/callback"},
        {"token_endpoint_auth_method": "client_secret_basic"},
    ):
        with pytest.raises(ValidationError):
            Config.model_validate(config)


@pytest.mark.parametrize("expired", [False, True])
async def test_official_refresh_persists_and_next_provider_reloads(expired):
    store = MemoryStore(expired=expired)
    original = store.current.access_token
    calls = []
    replacement = grant()
    replacement.access_token += "new"

    def respond(request):
        calls.append(request)
        if request.url.host == "auth.openai.com":
            assert request.headers.get("authorization") is None
            assert b"grant_type=refresh_token" in request.content
            return httpx2.Response(200, json={"access_token": replacement.access_token, "refresh_token": "rotated"})
        if request.headers["authorization"] == f"Bearer {original}":
            return httpx2.Response(401)
        assert store.saved and request.headers["authorization"] == f"Bearer {replacement.access_token}"
        return httpx2.Response(200, json={"models": []})

    for _ in range(2):
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            provider = OpenAIChatGPTProvider(credential_source=store, http_client=client)
            await provider.client.get("/models", cast_to=dict)
    assert len(store.saved) == 1 and store.current.refresh_token == "rotated"
    assert sum(call.url.host == "auth.openai.com" for call in calls) == 1
    assert sum(call.url.host == "api.openai.com" for call in calls) == (2 if expired else 3)


async def test_official_rejected_refresh_is_actionable_and_never_saved():
    store = MemoryStore()

    def respond(request):
        return (
            httpx2.Response(400, json={"error": "invalid_grant"})
            if request.url.host == "auth.openai.com"
            else httpx2.Response(401)
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        provider = OpenAIChatGPTProvider(credential_source=store, http_client=client)
        with pytest.raises(CredentialsRefreshError):
            await provider.client.get("/models", cast_to=dict)
    assert not store.saved


async def test_official_persistence_failure_surfaces(monkeypatch):
    store = MemoryStore()

    async def failed(credentials):
        raise OSError("write failed")

    monkeypatch.setattr(store, "save", failed)

    def respond(request):
        return (
            httpx2.Response(200, json={"access_token": "rotated", "refresh_token": "rotated"})
            if request.url.host == "auth.openai.com"
            else httpx2.Response(401)
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        provider = OpenAIChatGPTProvider(credential_source=store, http_client=client)
        with pytest.raises(CredentialsPersistenceError):
            await provider.client.get("/models", cast_to=dict)


def stream_events(terminal="response.completed"):
    payload = {
        "id": "resp-test",
        "object": "response",
        "created_at": 1,
        "model": "gpt-6.1-sol",
        "status": "completed",
        "output": [],
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    }
    events = [
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": {**payload, "status": "in_progress", "usage": None},
        },
        {
            "type": "response.output_text.delta",
            "sequence_number": 1,
            "item_id": "msg-test",
            "output_index": 0,
            "content_index": 0,
            "delta": "Hello",
        },
    ]
    if terminal:
        events.append({"type": terminal, "sequence_number": 2, "response": payload})
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"


@pytest.mark.parametrize("streaming", [False, True])
async def test_native_streaming_collector_dialect_tools_and_usage(streaming):
    requests = []
    store = MemoryStore()

    def respond(request):
        requests.append(json.loads(request.content))
        assert request.headers["authorization"].startswith("Bearer ")
        assert str(request.url) == RESOURCE + "/responses"
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=stream_events())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        provider = OpenAIChatGPTProvider(credential_source=store, http_client=client)
        model = OpenAIResponsesModel("gpt-6.1-sol", provider=provider)
        messages = [ModelRequest(parts=[SystemPromptPart("Be helpful"), UserPromptPart("Hi")])]
        parameters = ModelRequestParameters(
            function_tools=[ToolDefinition(name="test", parameters_json_schema={"type": "object"})]
        )
        settings = {"openai_store": True, "temperature": 0.4, "max_tokens": 10, "top_p": 1.0}
        async with model:
            if streaming:
                async with model.request_stream(messages, settings, parameters) as stream:
                    async for _ in stream:
                        pass
                    response = stream.get()
            else:
                response = await model.request(messages, settings, parameters)
        assert response.parts[0].content == "Hello"
        assert response.usage.input_tokens == 3 and response.usage.output_tokens == 2
        assert not client.is_closed
    body = requests[0]
    assert body["stream"] is True and body["store"] is False
    assert not {"temperature", "max_output_tokens", "top_p", "previous_response_id"} & body.keys()
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["name"] == "test"
    assert all(item.get("role") != "system" for item in body["input"])


async def test_account_catalog_uses_public_models_wire_shape_and_preserves_visible_order():
    from a13n_harness.providers.model.chatgpt import discover_chatgpt_models

    requests = []

    def respond(request):
        requests.append(request)
        assert str(request.url) == RESOURCE + "/models"
        assert request.headers["authorization"].startswith("Bearer ")
        return httpx2.Response(
            200,
            json={
                "models": [
                    {"slug": "gpt-visible-second", "display_name": "Second", "visibility": "list"},
                    {"slug": "gpt-hidden", "display_name": "Hidden", "visibility": "hidden"},
                    {"slug": "gpt-visible-first", "display_name": "First", "visibility": "list"},
                ]
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        models = await discover_chatgpt_models(credential_source=MemoryStore(), http_client=client)
    assert [(model.slug, model.display_name) for model in models] == [
        ("gpt-visible-second", "Second"),
        ("gpt-visible-first", "First"),
    ]
    assert len(requests) == 1


async def test_definition_discovery_declares_capability_and_uses_host_source_and_headers():
    from a13n_harness.providers.model.openai_chatgpt import DEFINITION

    assert DEFINITION.supports_model_discovery and not DEFINITION.supports_connection_probe
    assert DEFINITION.catalog_providers == ("openai",)
    assert not replace(DEFINITION, model_discovery=None).supports_model_discovery
    calls = []

    def respond(request):
        calls.append(request)
        assert str(request.url) == RESOURCE + "/models"
        assert request.headers["x-test"] == "discovery"
        return httpx2.Response(
            200,
            json={
                "models": [
                    {"slug": "z-plan", "display_name": "Z plan", "visibility": "list"},
                    {"slug": "hidden", "display_name": "Hidden", "visibility": "hidden"},
                    {"slug": "a-plan", "display_name": "A plan", "visibility": "list"},
                ]
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        choices = await DEFINITION.discover_models(
            configuration={},
            credential_source=MemoryStore(),
            http_client=client,
            extra_headers={"x-test": "discovery"},
        )
    assert [(choice.model_name, choice.display_name) for choice in choices] == [
        ("z-plan", "Z plan"),
        ("a-plan", "A plan"),
    ]
    assert len(calls) == 1


async def test_definition_discovery_refuses_missing_capability_and_auth_before_network():
    from a13n_harness.providers.model.definition import ProviderOperationError
    from a13n_harness.providers.model.openai_chatgpt import DEFINITION

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: pytest.fail("No request expected"))
    ) as client:
        with pytest.raises(ProviderOperationError, match="not supported"):
            await replace(DEFINITION, model_discovery=None).discover_models(configuration={}, http_client=client)
        with pytest.raises(ProviderOperationError, match="authentication source"):
            await DEFINITION.discover_models(configuration={}, http_client=client)


async def test_definition_discovery_normalizes_upstream_errors_without_secret_bodies():
    from a13n_harness.providers.model.definition import ProviderOperationError
    from a13n_harness.providers.model.openai_chatgpt import DEFINITION

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(403, json={"error": {"message": "synthetic-secret"}}))
    ) as client:
        with pytest.raises(ProviderOperationError) as failure:
            await DEFINITION.discover_models(configuration={}, credential_source=MemoryStore(), http_client=client)
    assert "synthetic-secret" not in str(failure.value)


@pytest.mark.parametrize("terminal", [None, "response.incomplete"])
async def test_official_collector_keeps_partial_text_without_completed_event(terminal):
    """Characterize the upstream boundary; Harness adds no terminal-event guard."""
    content = stream_events(terminal)
    if terminal:
        content = content.replace(
            '"status": "completed"', '"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}'
        )
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=content)
        )
    ) as client:
        model = OpenAIResponsesModel(
            "gpt-test", provider=OpenAIChatGPTProvider(credential_source=MemoryStore(), http_client=client)
        )
        async with model:
            response = await model.request([ModelRequest(parts=[UserPromptPart("Hi")])], None, ModelRequestParameters())
        assert response.parts[0].content == "Hello"
