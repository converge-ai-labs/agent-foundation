"""Synthetic GitHub grants only: no real credential stores or provider requests."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import anyio
import httpx2
import pytest
from a13n_harness.providers.model.oauth import (
    CopilotCredentials,
    CredentialPersistenceError,
    ModelAuthenticationError,
    ProcessCopilotCredentialSource,
    RefreshNotDispatched,
    build_copilot_model,
    refresh_copilot_credentials,
)
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.providers.github_copilot import GitHubCopilotCredentials

pytestmark = pytest.mark.anyio
BASE = "https://api.githubcopilot.com"


def grant(marker="old", *, expires=3600):
    return CopilotCredentials.issued(
        GitHubCopilotCredentials(
            access_token=f"synthetic-access-{marker}",
            token_type="bearer",
            scope="",
            refresh_token=f"synthetic-refresh-{marker}",
            expires_in=expires,
        ),
        account_id="test-user",
        client_id="test-client",
    )


class MemoryStore:
    def __init__(self, current=None, *, fail=False):
        self.current = current or grant()
        self.loads = 0
        self.saved = []
        self.fail = fail

    async def load(self):
        self.loads += 1
        return self.current

    async def save(self, value):
        if self.fail:
            raise OSError("synthetic failure")
        self.saved.append(value)
        self.current = value


async def test_issuance_normalizes_once_and_repr_never_contains_tokens():
    now = datetime(2026, 9, 24, tzinfo=UTC)
    value = CopilotCredentials.issued(grant().credentials, account_id="test-user", client_id="test-client", now=now)
    assert value.expires_at == now + timedelta(hours=1)
    assert "synthetic-access" not in repr(value) and "synthetic-refresh" not in repr(value)
    assert replace(value).expires_at == value.expires_at


async def test_native_provider_preserves_protocol_headers_and_loads_only_on_request():
    store = MemoryStore()
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "claude-sonnet-5",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        model = build_copilot_model(
            "claude-sonnet-5", credential_source=ProcessCopilotCredentialSource(store), http_client=client
        )
        assert store.loads == 0
        async with model:
            await model.request([ModelRequest(parts=[UserPromptPart("test")])], None, ModelRequestParameters())
        assert not client.is_closed
        assert model.profile["openai_chat_thinking_field"] == "reasoning_text"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == f"{BASE}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {store.current.access_token}"
    assert request.headers["copilot-integration-id"] == "vscode-chat"
    assert "editor-version" in request.headers
    assert json.loads(request.content)["model"] == "claude-sonnet-5"


@pytest.mark.parametrize("changed", [{"account_id": "other-user"}, {"source_id": "other-source"}])
async def test_parallel_requests_rotate_once_publish_before_use_and_pin_the_account(changed):
    store = MemoryStore(grant(expires=1))
    source = ProcessCopilotCredentialSource(store)
    exchanges = []
    requests = []

    async def refresh(current):
        exchanges.append(current)
        await anyio.sleep(0.01)
        return grant("new")

    async def respond(request):
        assert store.saved
        requests.append(request)
        assert request.headers["authorization"] == f"Bearer {store.current.access_token}"
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        build_copilot_model("test", credential_source=source, refresh=refresh, http_client=client)
        async with anyio.create_task_group() as tasks:
            for _ in range(4):
                tasks.start_soon(client.get, f"{BASE}/models")
        assert len(exchanges) == len(store.saved) == 1
        assert len(requests) == 4
        store.current = replace(store.current, **changed)
        with pytest.raises(ModelAuthenticationError, match="account changed"):
            await client.get(f"{BASE}/models")
        assert len(requests) == 4


async def test_publication_failure_blocks_retry_even_in_a_new_model():
    store = MemoryStore(grant(expires=1), fail=True)
    source = ProcessCopilotCredentialSource(store)
    exchanges = []

    async def refresh(current):
        exchanges.append(current)
        return grant("new")

    async def respond(request):
        pytest.fail("Unpublished credentials must never be sent")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        build_copilot_model("test", credential_source=source, refresh=refresh, http_client=client)
        with pytest.raises(CredentialPersistenceError):
            await client.get(f"{BASE}/models")
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        build_copilot_model("test", credential_source=source, refresh=refresh, http_client=client)
        with pytest.raises(ModelAuthenticationError, match="unknown"):
            await client.get(f"{BASE}/models")
    assert len(exchanges) == 1


async def test_401_replays_only_once_and_does_not_start_login():
    store = MemoryStore()
    requests = []
    exchanges = []

    async def refresh(current):
        exchanges.append(current)
        return grant("new")

    async def respond(request):
        requests.append(request.headers["authorization"])
        return httpx2.Response(401)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        build_copilot_model(
            "test", credential_source=ProcessCopilotCredentialSource(store), refresh=refresh, http_client=client
        )
        assert (await client.get(f"{BASE}/models")).status_code == 401
    assert len(requests) == 2 and len(exchanges) == 1
    assert requests == ["Bearer synthetic-access-old", "Bearer synthetic-access-new"]


async def test_headers_cannot_follow_an_explicit_cross_origin_redirect():
    store = MemoryStore()
    foreign = []

    async def respond(request):
        if str(request.url).startswith(BASE):
            return httpx2.Response(302, headers={"Location": "https://unrelated.example/final"})
        foreign.append(request.headers.get("authorization"))
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond), follow_redirects=True) as client:
        build_copilot_model("test", credential_source=ProcessCopilotCredentialSource(store), http_client=client)
        assert not client.follow_redirects
        await client.get(f"{BASE}/models", follow_redirects=True)
        await client.get("http://api.githubcopilot.com/models", headers={"Authorization": "Bearer synthetic"})
    assert foreign == [None, None]
    assert store.loads == 1


async def test_native_refresh_uses_complete_rotating_pair_without_secret_and_hides_errors():
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "access_token": "synthetic-new",
                "refresh_token": "synthetic-new-refresh",
                "token_type": "bearer",
                "scope": "",
                "expires_in": 3600,
                "refresh_token_expires_in": 86400,
            },
        )

    current = grant()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        result = await refresh_copilot_credentials(current, http_client=client)
    assert len(requests) == 1
    assert str(requests[0].url) == "https://github.com/login/oauth/access_token"
    assert parse_qs(requests[0].content.decode()) == {
        "client_id": ["test-client"],
        "grant_type": ["refresh_token"],
        "refresh_token": [current.refresh_token],
    }
    assert result.refresh_token == "synthetic-new-refresh"
    assert result.account_id == current.account_id
    assert result.refresh_expires_at is not None


async def test_nonrefreshable_credentials_require_explicit_login_without_dispatch():
    current = replace(
        grant(), credentials=GitHubCopilotCredentials(access_token="synthetic", token_type="bearer", scope="")
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: pytest.fail("Must not dispatch"))) as client:
        with pytest.raises(RefreshNotDispatched):
            await refresh_copilot_credentials(current, http_client=client)


async def test_owned_client_closes_and_reopens_without_loading_credentials():
    store = MemoryStore()
    model = build_copilot_model("test", credential_source=ProcessCopilotCredentialSource(store))
    async with model:
        original = model.client._client
        async with model:
            assert not original.is_closed
        assert not original.is_closed
    assert original.is_closed
    async with model:
        assert model.client._client is not original
        assert not model.client._client.is_closed
    assert model.client._client.is_closed
    assert store.loads == 0


async def test_malformed_upstream_instance_is_rejected_before_publication():
    store = MemoryStore(grant(expires=1))
    requests = []

    async def malformed(current):
        return replace(current, credentials=replace(current.credentials, access_token="", refresh_token=""))

    def respond(request):
        requests.append(request)
        return httpx2.Response(200, json={})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        build_copilot_model(
            "test", credential_source=ProcessCopilotCredentialSource(store), refresh=malformed, http_client=client
        )
        with pytest.raises(ModelAuthenticationError, match="invalid"):
            await client.get(f"{BASE}/models")
    assert not requests
    assert not store.saved


async def test_explicit_discovery_filters_chat_endpoints_without_model_inference():
    from a13n_harness.providers.model.oauth import discover_copilot_models

    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path == "/models"
        assert request.headers["copilot-integration-id"] == "vscode-chat"
        return httpx2.Response(
            200,
            json={
                "data": [
                    {"id": "chat-model", "supported_endpoints": ["/chat/completions"]},
                    {"id": "responses-only", "supported_endpoints": ["/responses"]},
                    {"id": "unknown"},
                    {"id": "chat-model", "supported_endpoints": ["/chat/completions", "/responses"]},
                ]
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        ids = await discover_copilot_models(
            credential_source=ProcessCopilotCredentialSource(MemoryStore()), http_client=client
        )
        assert ids == ("chat-model",)
        assert not client.is_closed
    assert len(requests) == 1
