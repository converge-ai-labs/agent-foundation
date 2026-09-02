from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx2
import pytest
from a13n_harness.model_auth import (
    CodexCredentials,
    CodexOAuthFlow,
    CodexSubscriptionModel,
    CredentialPersistenceError,
    CredentialRefreshError,
    GrokCredentials,
    ModelAuthenticationError,
    build_codex_model,
    build_grok_model,
)
from pydantic_ai.exceptions import UserError
from pydantic_ai.settings import ModelSettings

pytestmark = pytest.mark.anyio

_CODEX_ORIGIN = "https://chatgpt.com"
_CODEX_BASE_URL = f"{_CODEX_ORIGIN}/backend-api/codex"
_GROK_BASE_URL = "https://api.x.ai/v1"


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
        )
        return httpx2.Response(200)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        build_codex_model("gpt-5", credential_source=source, http_client=client)
        response = await client.get(f"{_CODEX_BASE_URL}/responses", follow_redirects=True)

    assert response.status_code == 200
    assert redirected_headers == {"authorization": None, "account": None, "originator": None}
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
            return httpx2.Response(200, json={"token_endpoint": "https://api.x.ai/oauth/token"})
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
