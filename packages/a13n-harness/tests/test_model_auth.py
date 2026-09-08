from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx2
import jwt
import pytest
from a13n_harness.model_auth import (
    CodexCredentials,
    CodexOAuthFlow,
    CodexSubscriptionModel,
    CredentialPersistenceError,
    CredentialRefreshError,
    GrokCredentials,
    GrokDeviceAuthorizationFlow,
    GrokOAuthFlow,
    ModelAuthenticationError,
    build_codex_model,
    build_grok_model,
    refresh_grok_credentials,
)
from a13n_harness.model_auth import runtime as model_auth_runtime
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic_ai import RunContext
from pydantic_ai.exceptions import UserError
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage

pytestmark = pytest.mark.anyio

_CODEX_ORIGIN = "https://chatgpt.com"
_CODEX_BASE_URL = f"{_CODEX_ORIGIN}/backend-api/codex"
_GROK_BASE_URL = "https://api.x.ai/v1"
_CODEX_ROUTING_HINT_HEADER = "x-codex-routing-hint"
_CODEX_TURN_STATE_HEADER = "x-codex-turn-state"


def _response(status: str) -> dict[str, object]:
    return {
        "id": "resp_1",
        "created_at": 1,
        "model": "gpt-5",
        "object": "response",
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "status": status,
    }


def _responses_sse() -> bytes:
    events = [
        {"type": "response.created", "sequence_number": 0, "response": _response("in_progress")},
        {"type": "response.completed", "sequence_number": 1, "response": _response("completed")},
    ]
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


def _codex_credentials(*, marker: str, expires_at: datetime) -> CodexCredentials:
    return CodexCredentials(
        account_id="account-1",
        expires_at=expires_at,
        access_token=f"codex-access-{marker}",
        refresh_token=f"codex-refresh-{marker}",
        id_token=f"codex-id-{marker}",
    )


def _grok_credentials(*, marker: str, expires_at: datetime) -> GrokCredentials:
    return GrokCredentials(
        account_id="account-1",
        auth_mode="oidc",
        create_time=datetime.now(UTC),
        expires_at=expires_at,
        issuer="https://issuer.example",
        client_id="client-id",
        access_token=f"grok-access-{marker}",
        refresh_token=f"grok-refresh-{marker}",
    )


class _CodexSource:
    def __init__(self, current: CodexCredentials, *, fail_save: bool = False) -> None:
        self.current = current
        self.fail_save = fail_save
        self.loads = 0
        self.saved: list[CodexCredentials] = []
        self.events: list[str] = []

    async def load(self) -> CodexCredentials:
        self.loads += 1
        self.events.append("load")
        return self.current

    async def save(self, credentials: CodexCredentials) -> None:
        self.events.append("save")
        self.saved.append(credentials)
        if self.fail_save:
            raise OSError("credential store unavailable")
        self.current = credentials


class _RepeatedValueHeaders(Mapping[str, str]):
    def __getitem__(self, name: str) -> str:
        if name == "set-cookie":
            raise LookupError("multiple values")
        if name == _CODEX_TURN_STATE_HEADER:
            return "turn-state"
        raise KeyError(name)

    def __iter__(self) -> Iterator[str]:
        return iter(("set-cookie", _CODEX_TURN_STATE_HEADER))

    def __len__(self) -> int:
        return 2


class _GrokSource:
    def __init__(self, current: GrokCredentials) -> None:
        self.current = current
        self.loads = 0
        self.saved: list[GrokCredentials] = []

    async def load(self) -> GrokCredentials:
        self.loads += 1
        return self.current

    async def save(self, credentials: GrokCredentials) -> None:
        self.saved.append(credentials)
        self.current = credentials


async def test_source_is_loaded_for_each_request_and_headers_stay_on_exact_origin() -> None:
    credentials = _codex_credentials(marker="current", expires_at=datetime.now(UTC) + timedelta(hours=1))
    source = _CodexSource(credentials)
    seen: list[tuple[str, str | None, str | None, str | None]] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(
            (
                request.url.host,
                request.headers.get("authorization"),
                request.headers.get("chatgpt-account-id"),
                request.headers.get("originator"),
            )
        )
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_codex_model("gpt-5", credential_source=source, http_client=client)
        await client.get(f"{_CODEX_BASE_URL}/models")
        await client.get(f"{_CODEX_BASE_URL}/responses")
        await client.get("https://example.com/not-the-provider")

    assert source.loads == 2
    assert seen == [
        ("chatgpt.com", "Bearer codex-access-current", "account-1", "a13n-harness"),
        ("chatgpt.com", "Bearer codex-access-current", "account-1", "a13n-harness"),
        ("example.com", None, None, None),
    ]


async def test_model_authentication_disables_redirect_following() -> None:
    source = _CodexSource(_codex_credentials(marker="current", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    requested_hosts: list[str] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        requested_hosts.append(request.url.host)
        return httpx2.Response(302, headers={"location": "https://example.com/final"})

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handle),
        follow_redirects=True,
    ) as client:
        build_codex_model("gpt-5", credential_source=source, http_client=client)
        response = await client.get(f"{_CODEX_BASE_URL}/responses")

    assert response.status_code == 302
    assert requested_hosts == ["chatgpt.com"]
    assert source.loads == 1


async def test_per_request_redirect_override_cannot_forward_protected_headers() -> None:
    source = _CodexSource(_codex_credentials(marker="current", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    redirected_headers: dict[str, str | None] = {}

    async def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "chatgpt.com":
            return httpx2.Response(302, headers={"location": "https://example.com/final"})
        redirected_headers.update(
            authorization=request.headers.get("authorization"),
            account=request.headers.get("chatgpt-account-id"),
            originator=request.headers.get("originator"),
            routing_hint=request.headers.get(_CODEX_ROUTING_HINT_HEADER),
            turn_state=request.headers.get(_CODEX_TURN_STATE_HEADER),
        )
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_codex_model("gpt-5", credential_source=source, http_client=client)
        response = await client.get(
            f"{_CODEX_BASE_URL}/responses",
            headers={
                _CODEX_ROUTING_HINT_HEADER: "model=caller-stale",
                _CODEX_TURN_STATE_HEADER: "caller-stale-state",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert redirected_headers == {
        "authorization": None,
        "account": None,
        "originator": None,
        "routing_hint": None,
        "turn_state": None,
    }
    assert source.loads == 1


async def test_stale_credentials_are_saved_before_the_rotated_token_is_sent() -> None:
    source = _CodexSource(_codex_credentials(marker="old", expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    refreshed = _codex_credentials(marker="new", expires_at=datetime.now(UTC) + timedelta(hours=1))

    async def refresh(credentials: CodexCredentials) -> CodexCredentials:
        assert credentials.access_token == "codex-access-old"
        source.events.append("refresh")
        return refreshed

    async def handle(request: httpx2.Request) -> httpx2.Response:
        source.events.append("send")
        assert request.headers["authorization"] == "Bearer codex-access-new"
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_codex_model("gpt-5", credential_source=source, refresh=refresh, http_client=client)
        response = await client.post(f"{_CODEX_BASE_URL}/responses", content=b"request-body")

    assert response.status_code == 200
    assert source.saved == [refreshed]
    assert source.events == ["load", "load", "refresh", "save", "send"]


async def test_refresh_adopts_a_newer_same_account_source_value() -> None:
    old = _codex_credentials(marker="old", expires_at=datetime.now(UTC) - timedelta(minutes=1))
    sibling = _codex_credentials(marker="sibling", expires_at=datetime.now(UTC) + timedelta(hours=1))

    class SequencedSource(_CodexSource):
        async def load(self) -> CodexCredentials:
            credentials = old if self.loads == 0 else sibling
            self.current = credentials
            return await super().load()

    source = SequencedSource(old)
    refreshes = 0

    async def refresh(credentials: CodexCredentials) -> CodexCredentials:
        nonlocal refreshes
        del credentials
        refreshes += 1
        return _codex_credentials(marker="callback", expires_at=datetime.now(UTC) + timedelta(hours=1))

    async def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["authorization"] == "Bearer codex-access-sibling"
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_codex_model(
            "gpt-5",
            credential_source=source,
            refresh=refresh,
            refresh_window=timedelta(hours=2),
            http_client=client,
        )
        response = await client.get(f"{_CODEX_BASE_URL}/responses")

    assert response.status_code == 200
    assert refreshes == 0
    assert source.saved == []
    assert source.loads == 2


async def test_live_model_rejects_a_source_account_switch() -> None:
    source = _CodexSource(_codex_credentials(marker="first", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    sends = 0

    async def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal sends
        del request
        sends += 1
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_codex_model("gpt-5", credential_source=source, http_client=client)
        await client.get(f"{_CODEX_BASE_URL}/responses")
        source.current = CodexCredentials(
            account_id="account-2",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            access_token="account-2-access",
            refresh_token="account-2-refresh",
        )
        with pytest.raises(ModelAuthenticationError, match="active Model account changed"):
            await client.get(f"{_CODEX_BASE_URL}/responses")

    assert sends == 1


async def test_persistence_failure_prevents_the_rotated_token_from_being_sent() -> None:
    source = _CodexSource(
        _codex_credentials(marker="old", expires_at=datetime.now(UTC) - timedelta(minutes=1)),
        fail_save=True,
    )
    sends = 0

    async def refresh(credentials: CodexCredentials) -> CodexCredentials:
        del credentials
        return _codex_credentials(marker="new", expires_at=datetime.now(UTC) + timedelta(hours=1))

    async def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal sends
        del request
        sends += 1
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_codex_model("gpt-5", credential_source=source, refresh=refresh, http_client=client)
        with pytest.raises(CredentialPersistenceError) as failed:
            await client.get(f"{_CODEX_BASE_URL}/responses")

    assert str(failed.value) == "The refreshed Model credentials could not be persisted."
    assert failed.value.__cause__ is None
    assert sends == 0
    assert source.current.access_token == "codex-access-old"


async def test_later_request_retries_a_transient_proactive_refresh_failure() -> None:
    source = _CodexSource(_codex_credentials(marker="old", expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    refreshes = 0
    sends = 0

    async def refresh(credentials: CodexCredentials) -> CodexCredentials:
        nonlocal refreshes
        del credentials
        refreshes += 1
        if refreshes == 1:
            raise OSError("temporary refresh failure")
        return _codex_credentials(marker="new", expires_at=datetime.now(UTC) + timedelta(hours=1))

    async def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal sends
        sends += 1
        assert request.headers["authorization"] == "Bearer codex-access-new"
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_codex_model("gpt-5", credential_source=source, refresh=refresh, http_client=client)
        with pytest.raises(CredentialRefreshError):
            await client.get(f"{_CODEX_BASE_URL}/responses")
        response = await client.get(f"{_CODEX_BASE_URL}/responses")

    assert response.status_code == 200
    assert refreshes == 2
    assert sends == 1


async def test_concurrent_stale_requests_share_failure_and_a_later_request_retries() -> None:
    source = _CodexSource(_codex_credentials(marker="old", expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    refreshes = 0
    failures: list[CredentialRefreshError] = []

    async def refresh(credentials: CodexCredentials) -> CodexCredentials:
        nonlocal refreshes
        del credentials
        refreshes += 1
        await anyio.sleep(0.02)
        if refreshes == 1:
            raise OSError("temporary refresh failure")
        return _codex_credentials(marker="new", expires_at=datetime.now(UTC) + timedelta(hours=1))

    async def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["authorization"] == "Bearer codex-access-new"
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_codex_model("gpt-5", credential_source=source, refresh=refresh, http_client=client)

        async def send() -> None:
            try:
                await client.get(f"{_CODEX_BASE_URL}/responses")
            except CredentialRefreshError as exc:
                failures.append(exc)

        async with anyio.create_task_group() as tasks:
            for _ in range(5):
                tasks.start_soon(send)

        assert refreshes == 1
        recovered = await client.get(f"{_CODEX_BASE_URL}/responses")

    assert recovered.status_code == 200
    assert refreshes == 2
    assert len(failures) == 5


async def test_invalid_refreshed_credentials_are_not_saved_or_sent() -> None:
    source = _CodexSource(_codex_credentials(marker="old", expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    sends = 0

    async def refresh(credentials: CodexCredentials) -> CodexCredentials:
        return CodexCredentials(
            account_id=credentials.account_id,
            expires_at=datetime.now(),
            access_token="new-access",
            refresh_token="new-refresh",
        )

    async def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal sends
        del request
        sends += 1
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_codex_model("gpt-5", credential_source=source, refresh=refresh, http_client=client)
        with pytest.raises(CredentialRefreshError):
            await client.get(f"{_CODEX_BASE_URL}/responses")

    assert source.saved == []
    assert sends == 0


async def test_one_401_refreshes_and_replays_the_same_request_body_once() -> None:
    source = _CodexSource(_codex_credentials(marker="old", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    refreshed = _codex_credentials(marker="new", expires_at=datetime.now(UTC) + timedelta(hours=1))
    requests: list[tuple[str, bytes]] = []
    refreshes = 0

    async def refresh(credentials: CodexCredentials) -> CodexCredentials:
        nonlocal refreshes
        refreshes += 1
        assert credentials.access_token == "codex-access-old"
        return refreshed

    async def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append((request.headers["authorization"], await request.aread()))
        return httpx2.Response(401)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_codex_model("gpt-5", credential_source=source, refresh=refresh, http_client=client)
        response = await client.post(f"{_CODEX_BASE_URL}/responses", content=b"request-body")

    assert response.status_code == 401
    assert requests == [
        ("Bearer codex-access-old", b"request-body"),
        ("Bearer codex-access-new", b"request-body"),
    ]
    assert refreshes == 1
    assert source.saved == [refreshed]


async def test_grok_refresh_bypasses_model_auth_when_oidc_uses_model_origin() -> None:
    credentials = GrokCredentials(
        account_id="account-1",
        auth_mode="oidc",
        create_time=datetime.now(UTC) - timedelta(hours=1),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        issuer="https://api.x.ai",
        client_id="client-id",
        access_token="grok-access-old",
        refresh_token="grok-refresh-old",
    )
    source = _GrokSource(credentials)
    events: list[str] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/.well-known/openid-configuration":
            assert request.headers.get("authorization") is None
            events.append("discovery")
            return httpx2.Response(
                200,
                json={
                    "issuer": "https://api.x.ai",
                    "token_endpoint": "https://api.x.ai/oauth/token",
                },
            )
        if request.url.path == "/oauth/token":
            assert request.headers.get("authorization") is None
            events.append("token")
            return httpx2.Response(
                200,
                json={
                    "access_token": "grok-access-new",
                    "refresh_token": "grok-refresh-new",
                    "expires_in": 3600,
                },
            )
        events.append("model")
        assert request.headers["authorization"] == "Bearer grok-access-new"
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_grok_model("grok-code", credential_source=source, http_client=client)
        with anyio.fail_after(1):
            response = await client.get(f"{_GROK_BASE_URL}/models")

    assert response.status_code == 200
    assert events == ["discovery", "token", "model"]
    assert len(source.saved) == 1
    assert source.saved[0].access_token == "grok-access-new"


async def test_grok_refresh_rejects_mismatched_discovery_issuer_before_token_exchange() -> None:
    credentials = _grok_credentials(marker="old", expires_at=datetime.now(UTC) - timedelta(minutes=1))
    requests: list[httpx2.Request] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "issuer": "https://other.example",
                "token_endpoint": "https://other.example/oauth/token",
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        with pytest.raises(CredentialRefreshError, match="different issuer"):
            await refresh_grok_credentials(credentials, http_client=client)

    assert [request.method for request in requests] == ["GET"]


async def test_concurrent_401s_share_failure_and_a_later_request_retries() -> None:
    source = _CodexSource(_codex_credentials(marker="old", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    refreshes = 0
    initial_requests = 0
    all_started = anyio.Event()
    request_lock = anyio.Lock()
    failures: list[CredentialRefreshError] = []

    async def refresh(credentials: CodexCredentials) -> CodexCredentials:
        nonlocal refreshes
        del credentials
        refreshes += 1
        if refreshes == 1:
            raise OSError("temporary refresh failure")
        return _codex_credentials(marker="new", expires_at=datetime.now(UTC) + timedelta(hours=1))

    async def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal initial_requests
        if request.headers["authorization"] == "Bearer codex-access-new":
            return httpx2.Response(200)
        async with request_lock:
            initial_requests += 1
            if initial_requests == 5:
                all_started.set()
        await all_started.wait()
        return httpx2.Response(401)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_codex_model("gpt-5", credential_source=source, refresh=refresh, http_client=client)

        async def send() -> None:
            try:
                await client.get(f"{_CODEX_BASE_URL}/responses")
            except CredentialRefreshError as exc:
                failures.append(exc)

        async with anyio.create_task_group() as tasks:
            for _ in range(5):
                tasks.start_soon(send)

        assert refreshes == 1
        recovered = await client.get(f"{_CODEX_BASE_URL}/responses")

    assert recovered.status_code == 200
    assert refreshes == 2
    assert initial_requests == 6
    assert len(failures) == 5
    assert all(isinstance(error, CredentialRefreshError) for error in failures)


async def test_concurrent_stale_requests_share_one_process_local_refresh() -> None:
    source = _GrokSource(_grok_credentials(marker="old", expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    refreshed = _grok_credentials(marker="new", expires_at=datetime.now(UTC) + timedelta(hours=1))
    refreshes = 0
    authorizations: list[str] = []

    async def refresh(credentials: GrokCredentials) -> GrokCredentials:
        nonlocal refreshes
        assert credentials.access_token == "grok-access-old"
        refreshes += 1
        await anyio.sleep(0.01)
        return refreshed

    async def handle(request: httpx2.Request) -> httpx2.Response:
        authorizations.append(request.headers["authorization"])
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_grok_model("grok-code", credential_source=source, refresh=refresh, http_client=client)

        async def send() -> None:
            response = await client.get(f"{_GROK_BASE_URL}/models")
            assert response.status_code == 200

        async with anyio.create_task_group() as tasks:
            for _ in range(8):
                tasks.start_soon(send)

    assert refreshes == 1
    assert source.saved == [refreshed]
    assert authorizations == ["Bearer grok-access-new"] * 8


async def test_builder_owned_client_closes_and_reopens_with_model_lifecycle() -> None:
    source = _CodexSource(_codex_credentials(marker="current", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    model = build_codex_model("gpt-5", credential_source=source)
    provider = model.provider
    assert provider is not None
    assert provider.name == "openai-codex"
    assert provider.client.max_retries == 0
    original = provider.client._client  # pyright: ignore[reportPrivateUsage]

    async with model:
        assert original.is_closed is False
    assert original.is_closed is True

    async with model:
        reopened = provider.client._client  # pyright: ignore[reportPrivateUsage]
        assert reopened is not original
        assert reopened.is_closed is False
    assert reopened.is_closed is True


async def test_caller_owned_client_stays_open_after_model_lifecycle() -> None:
    source = _GrokSource(_grok_credentials(marker="current", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    client = httpx2.AsyncClient()
    try:
        model = build_grok_model("grok-code", credential_source=source, http_client=client)
        assert model.provider is not None
        assert model.provider.name == "grok"
        async with model:
            pass
        assert client.is_closed is False
    finally:
        await client.aclose()


async def test_builder_rejects_a_client_that_already_has_authentication() -> None:
    source = _CodexSource(_codex_credentials(marker="current", expires_at=datetime.now(UTC) + timedelta(hours=1)))

    async with httpx2.AsyncClient(auth=httpx2.BasicAuth("name", "secret")) as client:
        with pytest.raises(UserError, match="must not already have authentication"):
            build_codex_model("gpt-5", credential_source=source, http_client=client)


def test_codex_credentials_hide_all_tokens_from_representations() -> None:
    credentials = _codex_credentials(marker="secret", expires_at=datetime.now(UTC) + timedelta(hours=1))
    rendered = repr(credentials)

    assert "codex-access-secret" not in rendered
    assert "codex-refresh-secret" not in rendered
    assert "codex-id-secret" not in rendered
    assert "account-1" in rendered


def test_grok_credentials_hide_all_tokens_from_representations() -> None:
    credentials = _grok_credentials(marker="secret", expires_at=datetime.now(UTC) + timedelta(hours=1))
    rendered = repr(credentials)

    assert "grok-access-secret" not in rendered
    assert "grok-refresh-secret" not in rendered
    assert "account-1" in rendered


def test_codex_pkce_authorization_url_contains_challenge_but_not_verifier() -> None:
    flow = CodexOAuthFlow(state="state-value")
    query = parse_qs(urlsplit(flow.authorization_url()).query)
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(flow.code_verifier.encode()).digest()).rstrip(b"=").decode()
    )

    assert query["state"] == ["state-value"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [expected_challenge]
    assert query["redirect_uri"] == [flow.redirect_uri]
    assert flow.code_verifier not in flow.authorization_url()


def test_oauth_extra_params_cannot_replace_flow_security_parameters() -> None:
    flow = CodexOAuthFlow(state="state-value")

    with pytest.raises(UserError, match="cannot override OAuth parameter: state"):
        flow.authorization_url(extra_params={"state": "replacement"})


def test_codex_turn_state_capture_skips_unrelated_repeated_headers() -> None:
    state = model_auth_runtime._CodexTurnState()  # pyright: ignore[reportPrivateUsage]

    state.capture(_RepeatedValueHeaders())

    assert state.value == "turn-state"


async def test_codex_routing_hint_and_turn_state_follow_effective_run() -> None:
    source = _CodexSource(_codex_credentials(marker="current", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    request_headers: list[httpx2.Headers] = []
    response_states = iter(
        ("turn-one", "ignored", "ignored-again", "turn-two", "reused-one", "direct-one", "direct-two")
    )

    async def handle(request: httpx2.Request) -> httpx2.Response:
        request_headers.append(request.headers.copy())
        return httpx2.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                _CODEX_TURN_STATE_HEADER: next(response_states),
            },
            content=_responses_sse(),
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handle),
        headers={
            _CODEX_ROUTING_HINT_HEADER: "model=client-stale",
            _CODEX_TURN_STATE_HEADER: "client-stale-state",
        },
    ) as client:
        model = build_codex_model("gpt-5", credential_source=source, http_client=client)
        run_one_usage = RunUsage()
        contexts = [
            RunContext(deps=object(), model=model, usage=run_one_usage, run_id="run-one"),
            RunContext(deps=object(), model=model, usage=run_one_usage, run_id="run-one"),
            RunContext(deps=object(), model=model, usage=run_one_usage, run_id="run-one"),
            RunContext(deps=object(), model=model, usage=RunUsage(), run_id="run-two"),
            RunContext(deps=object(), model=model, usage=RunUsage(), run_id="run-one"),
        ]
        settings_by_request = [
            OpenAIResponsesModelSettings(
                service_tier="flex",
                openai_service_tier="priority",
                extra_headers={
                    "X-Codex-Routing-Hint": "model=caller-stale",
                    "X-Codex-Turn-State": "caller-stale-state",
                },
            ),
            OpenAIResponsesModelSettings(service_tier="flex"),
            OpenAIResponsesModelSettings(),
            OpenAIResponsesModelSettings(),
            OpenAIResponsesModelSettings(),
        ]
        for context, settings in zip(contexts, settings_by_request, strict=True):
            async with model.request_stream([], settings, ModelRequestParameters(), context) as response:
                async for _ in response:
                    pass
        for _ in range(2):
            async with model.request_stream([], None, ModelRequestParameters()) as response:
                async for _ in response:
                    pass

    assert [headers[_CODEX_ROUTING_HINT_HEADER] for headers in request_headers] == [
        "model=gpt-5;tier=priority",
        "model=gpt-5;tier=flex",
        "model=gpt-5",
        "model=gpt-5",
        "model=gpt-5",
        "model=gpt-5",
        "model=gpt-5",
    ]
    assert [headers.get(_CODEX_TURN_STATE_HEADER) for headers in request_headers] == [
        None,
        "turn-one",
        "turn-one",
        None,
        None,
        None,
        None,
    ]


async def test_codex_turn_state_ignores_401_and_captures_successful_replay() -> None:
    source = _CodexSource(_codex_credentials(marker="old", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    refreshed = _codex_credentials(marker="new", expires_at=datetime.now(UTC) + timedelta(hours=1))
    request_headers: list[httpx2.Headers] = []

    async def refresh(credentials: CodexCredentials) -> CodexCredentials:
        assert credentials.access_token == "codex-access-old"
        return refreshed

    async def handle(request: httpx2.Request) -> httpx2.Response:
        request_headers.append(request.headers.copy())
        if len(request_headers) == 1:
            return httpx2.Response(401, headers={_CODEX_TURN_STATE_HEADER: "unauthorized-state"})
        state = "valid-state" if len(request_headers) == 2 else "ignored-state"
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream", _CODEX_TURN_STATE_HEADER: state},
            content=_responses_sse(),
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handle),
        headers={_CODEX_TURN_STATE_HEADER: "client-stale-state"},
    ) as client:
        model = build_codex_model(
            "gpt-5",
            credential_source=source,
            refresh=refresh,
            http_client=client,
        )
        usage = RunUsage()
        context = RunContext(deps=object(), model=model, usage=usage, run_id="run-one")
        settings = OpenAIResponsesModelSettings(extra_headers={_CODEX_TURN_STATE_HEADER: "request-stale-state"})
        for _ in range(2):
            async with model.request_stream([], settings, ModelRequestParameters(), context) as response:
                async for _ in response:
                    pass

    assert [headers["authorization"] for headers in request_headers] == [
        "Bearer codex-access-old",
        "Bearer codex-access-new",
        "Bearer codex-access-new",
    ]
    assert [headers[_CODEX_ROUTING_HINT_HEADER] for headers in request_headers] == ["model=gpt-5"] * 3
    assert [headers.get(_CODEX_TURN_STATE_HEADER) for headers in request_headers] == [
        None,
        None,
        "valid-state",
    ]
    assert source.saved == [refreshed]


async def test_codex_turn_state_is_isolated_between_concurrent_runs() -> None:
    source = _CodexSource(_codex_credentials(marker="current", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    first_started = {"one": anyio.Event(), "two": anyio.Event()}
    seen: dict[tuple[str, str], httpx2.Headers] = {}

    async def handle(request: httpx2.Request) -> httpx2.Response:
        run = request.headers["x-test-run"]
        phase = request.headers["x-test-phase"]
        seen[(run, phase)] = request.headers.copy()
        if phase == "first":
            first_started[run].set()
            other = "two" if run == "one" else "one"
            await first_started[other].wait()
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream", _CODEX_TURN_STATE_HEADER: f"state-{run}"},
            content=_responses_sse(),
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        model = build_codex_model("gpt-5", credential_source=source, http_client=client)
        contexts = {
            run: RunContext(deps=object(), model=model, usage=RunUsage(), run_id=f"run-{run}") for run in ("one", "two")
        }

        async def consume(run: str, phase: str) -> None:
            settings = OpenAIResponsesModelSettings(extra_headers={"x-test-run": run, "x-test-phase": phase})
            async with model.request_stream([], settings, ModelRequestParameters(), contexts[run]) as response:
                async for _ in response:
                    pass

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(consume, "one", "first")
            tasks.start_soon(consume, "two", "first")
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(consume, "one", "continuation")
            tasks.start_soon(consume, "two", "continuation")

    assert seen[("one", "first")].get(_CODEX_TURN_STATE_HEADER) is None
    assert seen[("two", "first")].get(_CODEX_TURN_STATE_HEADER) is None
    assert seen[("one", "continuation")][_CODEX_TURN_STATE_HEADER] == "state-one"
    assert seen[("two", "continuation")][_CODEX_TURN_STATE_HEADER] == "state-two"
    assert all(headers[_CODEX_ROUTING_HINT_HEADER] == "model=gpt-5" for headers in seen.values())


async def test_grok_browser_oauth_discovers_endpoints_and_verifies_identity() -> None:
    issuer = "https://issuer.example"
    client_id = "public-client"
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk["kid"] = "key-1"
    requests: list[httpx2.Request] = []
    flow: GrokOAuthFlow | None = None

    async def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx2.Response(
                200,
                json={
                    "issuer": issuer,
                    "authorization_endpoint": f"{issuer}/authorize",
                    "token_endpoint": f"{issuer}/oauth2/token",
                    "jwks_uri": f"{issuer}/.well-known/jwks.json",
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
            )
        if request.url.path == "/oauth2/token":
            assert flow is not None
            id_token = jwt.encode(
                {
                    "iss": issuer,
                    "aud": client_id,
                    "sub": "user-1",
                    "nonce": flow.nonce,
                    "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
                },
                private_key,
                algorithm="RS256",
                headers={"kid": "key-1"},
            )
            return httpx2.Response(
                200,
                json={
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "id_token": id_token,
                    "expires_in": 3600,
                },
            )
        if request.url.path.endswith("/jwks.json"):
            return httpx2.Response(200, json={"keys": [jwk]})
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        flow = await GrokOAuthFlow.discover(
            issuer=issuer,
            client_id=client_id,
            scopes=("openid", "offline_access"),
            redirect_uri="http://127.0.0.1:43210/callback",
            referrer="a13n-harness-ui",
            http_client=client,
        )
        query = parse_qs(urlsplit(flow.authorization_url()).query)
        credentials = await flow.exchange_code("authorization-code")

    assert query == {
        "response_type": ["code"],
        "client_id": [client_id],
        "redirect_uri": ["http://127.0.0.1:43210/callback"],
        "scope": ["openid offline_access"],
        "state": [flow.state],
        "nonce": [flow.nonce],
        "code_challenge": [flow.code_challenge],
        "code_challenge_method": ["S256"],
        "referrer": ["a13n-harness-ui"],
    }
    assert credentials.account_id == "user-1"
    assert credentials.access_token == "access-secret"
    assert credentials.refresh_token == "refresh-secret"
    token_form = parse_qs(requests[1].content.decode())
    assert token_form["code"] == ["authorization-code"]
    assert token_form["code_verifier"] == [flow.code_verifier]


async def test_grok_browser_oauth_rejects_mismatched_discovery_issuer() -> None:
    async def handle(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(200, json={"issuer": "https://other.example"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        with pytest.raises(CredentialRefreshError, match="different issuer"):
            await GrokOAuthFlow.discover(
                issuer="https://issuer.example",
                client_id="public-client",
                scopes=("openid",),
                redirect_uri="http://127.0.0.1:43210/callback",
                http_client=client,
            )


async def test_grok_device_oauth_polls_pending_and_slow_down_without_exposing_device_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issuer = "https://issuer.example"
    token_attempts = 0
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal token_attempts
        if request.url.path == "/oauth2/device/code":
            return httpx2.Response(
                200,
                json={
                    "device_code": "device-secret",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://accounts.example/device",
                    "verification_uri_complete": "https://accounts.example/device?code=ABCD-1234",
                    "expires_in": 600,
                    "interval": 2,
                },
            )
        token_attempts += 1
        if token_attempts == 1:
            return httpx2.Response(400, json={"error": "authorization_pending"})
        if token_attempts == 2:
            return httpx2.Response(400, json={"error": "slow_down"})
        return httpx2.Response(
            200,
            json={
                "access_token": _unsigned_jwt({"sub": "user-1"}),
                "refresh_token": "refresh-secret",
                "id_token": _unsigned_jwt({"sub": "user-1"}),
                "expires_in": 3600,
            },
        )

    monkeypatch.setattr("a13n_harness.model_auth.oauth.anyio.sleep", fake_sleep)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        authorization = await GrokDeviceAuthorizationFlow.start(
            issuer=issuer,
            client_id="public-client",
            scopes=("openid", "offline_access"),
            referrer="a13n-harness-ui",
            http_client=client,
        )
        credentials = await authorization.wait_for_credentials()

    assert "device-secret" not in repr(authorization)
    assert authorization.user_code == "ABCD-1234"
    assert sleeps == [2.0, 2.0, 7.0]
    assert credentials.account_id == "user-1"
    assert credentials.refresh_token == "refresh-secret"


async def test_grok_device_oauth_lifetime_starts_with_authorization_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0

    async def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path != "/oauth2/device/code":
            raise AssertionError("an expired device code must not be polled")
        return httpx2.Response(
            200,
            json={
                "device_code": "device-secret",
                "user_code": "ABCD-1234",
                "verification_uri": "https://accounts.example/device",
                "expires_in": 1,
            },
        )

    monkeypatch.setattr("a13n_harness.model_auth.oauth.time.monotonic", lambda: now)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        authorization = await GrokDeviceAuthorizationFlow.start(
            issuer="https://issuer.example",
            client_id="public-client",
            scopes=("openid",),
            http_client=client,
        )
        now = 101.0
        with pytest.raises(CredentialRefreshError, match="Device authorization expired"):
            await authorization.wait_for_credentials()


async def test_grok_device_oauth_bounds_each_token_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sleep(delay: float) -> None:
        del delay

    async def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/oauth2/device/code":
            return httpx2.Response(
                200,
                json={
                    "device_code": "device-secret",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://accounts.example/device",
                    "expires_in": 600,
                },
            )
        await anyio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr("a13n_harness.model_auth.oauth.anyio.sleep", fake_sleep)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        authorization = await GrokDeviceAuthorizationFlow.start(
            issuer="https://issuer.example",
            client_id="public-client",
            scopes=("openid",),
            http_client=client,
        )
        authorization = replace(authorization, _expires_at=time.monotonic() + 0.01)
        with pytest.raises(CredentialRefreshError, match="Device authorization expired"):
            await authorization.wait_for_credentials()


async def test_grok_device_oauth_rejects_untrusted_verification_url() -> None:
    async def handle(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(
            200,
            json={
                "device_code": "device-secret",
                "user_code": "ABCD-1234",
                "verification_uri": "javascript:alert(1)",
                "expires_in": 600,
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        with pytest.raises(CredentialRefreshError, match="invalid verification_uri"):
            await GrokDeviceAuthorizationFlow.start(
                issuer="https://issuer.example",
                client_id="public-client",
                scopes=("openid",),
                http_client=client,
            )


def _unsigned_jwt(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{encoded}.signature"


def test_codex_subscription_settings_use_harness_thread_affinity() -> None:
    original = ModelSettings(
        max_tokens=123,
        temperature=0.4,
        top_p=0.8,
        openai_store=True,
        openai_user="unsupported",
        extra_headers={
            "X-Session-ID": "thread-1",
            "Thread-ID": "explicit-thread",
            "X-Custom": "custom",
        },
    )

    settings = CodexSubscriptionModel._codex_settings(original)

    assert settings["openai_store"] is False
    assert "max_tokens" not in settings
    assert "temperature" not in settings
    assert "top_p" not in settings
    assert "openai_user" not in settings
    assert settings["extra_headers"] == {
        "X-Session-ID": "thread-1",
        "Thread-ID": "explicit-thread",
        "X-Custom": "custom",
        "session-id": "thread-1",
        "x-client-request-id": "thread-1",
    }
    assert original["openai_store"] is True
    assert original["extra_headers"] == {
        "X-Session-ID": "thread-1",
        "Thread-ID": "explicit-thread",
        "X-Custom": "custom",
    }


async def test_codex_device_authorization_uses_vendor_protocol_and_device_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from a13n_harness.model_auth import CodexDeviceAuthorizationFlow

    attempts = 0

    async def fake_sleep(delay: float) -> None:
        assert delay == 2

    async def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        assert "authorization" not in request.headers
        if request.url.path.endswith("/usercode"):
            assert json.loads(request.content)["client_id"]
            return httpx2.Response(
                200, json={"device_auth_id": "device-secret", "user_code": "ABCD-1234", "interval": "2"}
            )
        if request.url.path.endswith("deviceauth/token"):
            assert json.loads(request.content) == {"device_auth_id": "device-secret", "user_code": "ABCD-1234"}
            attempts += 1
            if attempts < 3:
                return httpx2.Response(403 if attempts == 1 else 404)
            return httpx2.Response(200, json={"authorization_code": "auth-secret", "code_verifier": "pkce-secret"})
        assert request.url.path == "/oauth/token"
        form = parse_qs(request.content.decode())
        assert form["redirect_uri"] == ["https://auth.openai.com/deviceauth/callback"]
        assert form["code"] == ["auth-secret"]
        assert form["code_verifier"] == ["pkce-secret"]
        return httpx2.Response(
            200,
            json={
                "access_token": _unsigned_jwt({"exp": 2000000000}),
                "refresh_token": "refresh-secret",
                "account_id": "account-device",
            },
        )

    monkeypatch.setattr("a13n_harness.model_auth.oauth.anyio.sleep", fake_sleep)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle), auth=("ambient", "secret")) as client:
        grant = await CodexDeviceAuthorizationFlow.start(http_client=client)
        assert "device-secret" not in repr(grant)
        assert grant.verification_uri == "https://auth.openai.com/codex/device"
        credentials = await grant.wait_for_credentials()
        assert credentials.account_id == "account-device"
    assert attempts == 3


async def test_codex_device_unsupported_expiry_and_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    from a13n_harness.model_auth import CodexDeviceAuthorizationFlow, DeviceAuthorizationError

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: httpx2.Response(404))) as client:
        with pytest.raises(DeviceAuthorizationError) as caught:
            await CodexDeviceAuthorizationFlow.start(http_client=client)
        assert caught.value.reason == "unsupported"
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(
                200, json={"device_auth_id": "private-device", "user_code": "ABC-123", "interval": 1}
            )
        )
    ) as client:
        grant = await CodexDeviceAuthorizationFlow.start(http_client=client)
        with pytest.raises(DeviceAuthorizationError) as caught:
            await replace(grant, _expires_at=time.monotonic() - 1).wait_for_credentials()
        assert caught.value.reason == "expired"
        with anyio.move_on_after(0.01) as scope:
            await grant.wait_for_credentials()
        assert scope.cancel_called
