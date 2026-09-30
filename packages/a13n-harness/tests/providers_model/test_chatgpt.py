"""Synthetic SIWC issuer and Responses transport; never read real credentials."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import anyio
import httpx2
import jwt
import pytest
from a13n_harness.models.chatgpt import OpenAIChatGPTResponsesModel
from a13n_harness.providers.model.chatgpt import OpenAIChatGPTProvider
from a13n_harness.providers.model.oauth.chatgpt import (
    ISSUER,
    RESOURCE,
    SCOPES,
    ChatGPTAuthorization,
    OpenAIChatGPTCredentials,
    OpenAIChatGPTOAuthFlow,
)
from a13n_harness.providers.model.oauth.models import CredentialRefreshError, ModelAuthenticationError
from a13n_harness.providers.model.oauth.source import ProcessChatGPTCredentialSource
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import TypeAdapter
from pydantic_ai.exceptions import ModelAPIError, UserError
from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition

pytestmark = pytest.mark.anyio


def grant(marker="old", expires=3600):
    return OpenAIChatGPTCredentials(
        subject="subject-1",
        client_id="oaiapp_test",
        ext_agent_host_id="urn:uuid:39c5c744-23fa-4e9d-8be9-350c2fcaf520",
        expires_at=datetime.now(UTC) + timedelta(seconds=expires),
        scopes=SCOPES,
        access_token=f"synthetic-access-{marker}",
        refresh_token=f"synthetic-refresh-{marker}",
        id_token="synthetic-id",
    )


class MemoryStore:
    def __init__(self, value=None):
        self.value = value or grant()
        self.saved = []

    async def load(self):
        return self.value

    async def save(self, value):
        self.saved.append(value)
        self.value = value


def callback(flow, **changes):
    values = {"state": flow.authorization.state, "code": "synthetic-code", "client_id": "oaiapp_test", **changes}
    return flow.authorization.redirect_uri + "?" + urlencode(values)


def flow(client=None, credentials=None):
    return OpenAIChatGPTOAuthFlow.start(
        ext_agent_host_id="urn:uuid:39c5c744-23fa-4e9d-8be9-350c2fcaf520",
        agent_name="Test Agent",
        redirect_uri="http://127.0.0.1:18455/auth/callback",
        http_client=client,
        credentials=credentials,
    )


async def test_registration_roundtrip_preserves_pkce_state_and_host():
    current = flow()
    params = parse_qs(urlsplit(current.authorization_url()).query)
    assert params["client_id"] == ["dynamic_agent_client"]
    assert params["agent_name_hint"] == ["Test Agent"]
    assert params["resource"] == [RESOURCE]
    assert params["scope"] == [" ".join(SCOPES)]
    assert params["ext_agent_host_id"] == ["urn:uuid:39c5c744-23fa-4e9d-8be9-350c2fcaf520"]
    restored = OpenAIChatGPTOAuthFlow(
        TypeAdapter(ChatGPTAuthorization).validate_json(
            TypeAdapter(ChatGPTAuthorization).dump_json(current.authorization)
        )
    )
    assert restored.authorization_url() == current.authorization_url()
    assert restored.validate_callback(callback(current)).client_id == "oaiapp_test"
    assert current.authorization.code_verifier not in repr(current.authorization)
    assert grant().access_token not in repr(grant())


@pytest.mark.parametrize(
    "change",
    [
        {"state": "other"},
        {"client_id": ""},
        {"client_id": "dynamic_agent_client"},
        {"code": ""},
        {"error": "access_denied"},
    ],
)
async def test_invalid_callback_is_rejected_without_network(change):
    current = flow()
    with pytest.raises(UserError):
        await current.exchange_callback(callback(current, **change))


@pytest.mark.parametrize(
    "replace_part",
    [
        ("127.0.0.1", "localhost"),
        (":18455", ":18456"),
        ("/auth/callback", "/callback"),
    ],
)
async def test_callback_exact_target_is_required(replace_part):
    current = flow()
    with pytest.raises(UserError):
        current.validate_callback(callback(current).replace(*replace_part))
    with pytest.raises(UserError):
        current.validate_callback(callback(current) + "&state=another")
    with pytest.raises(UserError):
        current.validate_callback(callback(current) + "#fragment")


async def test_reauthorization_uses_saved_client_hints_and_rejects_registration_change():
    current = flow(credentials=replace(grant(), email="test@example.com"))
    params = parse_qs(urlsplit(current.authorization_url()).query)
    assert "agent_name_hint" not in params
    assert params["client_id"] == ["oaiapp_test"] and params["id_token_hint"] == ["synthetic-id"]
    assert current.validate_callback(callback(current, client_id="")).client_id == "oaiapp_test"
    with pytest.raises(UserError, match="changed"):
        current.validate_callback(callback(current, client_id="other"))
    expired = OpenAIChatGPTOAuthFlow(
        replace(current.authorization, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    with pytest.raises(UserError, match="expired"):
        expired.validate_callback(callback(current))


@pytest.mark.parametrize("invalid", [None, "signature", "nonce", "aud", "iss", "exp", "sub", "scope"])
async def test_signed_identity_and_direct_scope_are_required(invalid):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())), "kid": "key-1", "use": "sig"}
    requests = []
    current = flow()

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("openid-configuration"):
            return httpx2.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "jwks_uri": ISSUER + "/.well-known/jwks.json",
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
            )
        if request.url.path.endswith("jwks.json"):
            return httpx2.Response(200, json={"keys": [jwk]})
        assert request.url.path == "/api/accounts/oauth/token"
        form = parse_qs(request.content.decode())
        assert form["client_id"] == ["oaiapp_test"] and form["resource"] == [RESOURCE]
        assert form["code_verifier"] == [current.authorization.code_verifier]
        claims = {
            "sub": "subject-1",
            "iss": ISSUER,
            "aud": "oaiapp_test",
            "nonce": current.authorization.nonce,
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        }
        if invalid in ("nonce", "aud", "iss", "sub"):
            claims[invalid] = "" if invalid == "sub" else "other"
        if invalid == "exp":
            claims["exp"] = 1
        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if invalid == "signature" else key
        return httpx2.Response(
            200,
            json={
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh",
                "token_type": "Bearer",
                "scope": "openid" if invalid == "scope" else " ".join(SCOPES),
                "expires_in": 3600,
                "id_token": jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "key-1"}),
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        current = flow(client)
        if invalid:
            with pytest.raises(CredentialRefreshError):
                await current.exchange_callback(callback(current))
        else:
            result = await current.exchange_callback(callback(current))
            assert result.subject == "subject-1" and result.client_id == "oaiapp_test"
            assert (
                result.ext_agent_host_id == "urn:uuid:39c5c744-23fa-4e9d-8be9-350c2fcaf520" and result.scopes == SCOPES
            )
    assert all(request.url.host == "auth.openai.com" for request in requests)


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
        assert request.headers["authorization"] == "Bearer synthetic-access-old"
        assert str(request.url) == RESOURCE + "/responses"
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=stream_events())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        provider = OpenAIChatGPTProvider(credential_source=ProcessChatGPTCredentialSource(store), http_client=client)
        model = OpenAIChatGPTResponsesModel("gpt-6.1-sol", provider=provider)
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
    assert not {"temperature", "max_output_tokens", "top_p", "tools", "previous_response_id"} & body.keys()
    assert body["input"][0]["type"] == "additional_tools"
    assert body["input"][0]["tools"][0]["name"] == "test"
    assert all(item.get("role") != "system" for item in body["input"])


@pytest.mark.parametrize("terminal", [None, "response.failed", "response.incomplete"])
async def test_partial_stream_is_not_successful_inference(terminal):
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, content=stream_events(terminal)
            )
        )
    ) as client:
        model = OpenAIChatGPTResponsesModel(
            "gpt-6.1-sol",
            provider=OpenAIChatGPTProvider(
                credential_source=ProcessChatGPTCredentialSource(MemoryStore()), http_client=client
            ),
        )
        with pytest.raises(ModelAPIError, match="ChatGPT"):
            await model.request([], None, ModelRequestParameters())


async def test_concurrent_models_publish_one_refresh_and_never_switch_accounts():
    store = MemoryStore(grant(expires=1))
    source = ProcessChatGPTCredentialSource(store)
    exchanges = []

    async def refresh(current):
        exchanges.append(current)
        await anyio.sleep(0.01)
        return grant("new")

    def respond(request):
        assert store.saved and request.headers["authorization"] == "Bearer synthetic-access-new"
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        OpenAIChatGPTProvider(credential_source=source, refresh=refresh, http_client=client)
        async with anyio.create_task_group() as group:
            for _ in range(4):
                group.start_soon(client.get, RESOURCE + "/models")
        assert len(exchanges) == len(store.saved) == 1
        store.value = replace(store.value, subject="different")
        with pytest.raises(ModelAuthenticationError, match="account changed"):
            await client.get(RESOURCE + "/models")


async def test_uncertain_refresh_is_not_replayed():
    store = MemoryStore(grant(expires=1))
    source = ProcessChatGPTCredentialSource(store)
    exchanges = []

    async def refresh(current):
        exchanges.append(current)
        raise TimeoutError("lost token response")

    with pytest.raises(TimeoutError):
        await source.rotate(store.value, refresh)
    with pytest.raises(ModelAuthenticationError, match="unknown"):
        await source.rotate(store.value, refresh)
    assert len(exchanges) == 1


async def test_account_catalog_uses_public_models_wire_shape_and_preserves_visible_order():
    from a13n_harness.providers.model.chatgpt import discover_chatgpt_models

    requests = []

    def respond(request):
        requests.append(request)
        assert str(request.url) == RESOURCE + "/models"
        assert request.headers["authorization"] == "Bearer synthetic-access-old"
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
        models = await discover_chatgpt_models(
            credential_source=ProcessChatGPTCredentialSource(MemoryStore()), http_client=client
        )
    assert [(model.slug, model.display_name) for model in models] == [
        ("gpt-visible-second", "Second"),
        ("gpt-visible-first", "First"),
    ]
    assert len(requests) == 1


async def test_unauthorized_request_replays_once_after_persisted_rotation():
    store = MemoryStore()
    requests, exchanges = [], []

    async def refresh(current):
        exchanges.append(current)
        return grant("new")

    def respond(request):
        requests.append(request.headers["authorization"])
        return httpx2.Response(401, json={"error": {"message": "unauthorized"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        OpenAIChatGPTProvider(
            credential_source=ProcessChatGPTCredentialSource(store), refresh=refresh, http_client=client
        )
        result = await client.get(RESOURCE + "/models")
    assert result.status_code == 401 and len(exchanges) == len(store.saved) == 1
    assert requests == ["Bearer synthetic-access-old", "Bearer synthetic-access-new"]
