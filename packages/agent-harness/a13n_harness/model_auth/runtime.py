"""Request-fresh Model OAuth lifecycle and native Model constructors."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from urllib.parse import urljoin, urlsplit

import httpx2
from anyio import CancelScope, Event, Lock
from pydantic_ai import RunContext, UserError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from .models import (
    CodexCredentials,
    CodexCredentialSource,
    CredentialPersistenceError,
    CredentialRefreshError,
    GrokCredentials,
    GrokCredentialSource,
    ModelAuthenticationError,
)
from .oauth import refresh_codex_credentials, refresh_grok_credentials

type Refresh[CredentialT] = Callable[[CredentialT], Awaitable[CredentialT]]
_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_GROK_BASE_URL = "https://api.x.ai/v1"
_DEFAULT_REFRESH_WINDOW = timedelta(minutes=5)
_CODEX_UNSUPPORTED_SETTINGS = frozenset(
    {"max_tokens", "temperature", "top_p", "openai_top_logprobs", "openai_truncation", "openai_user"}
)


class _CredentialSource[CredentialT](Protocol):
    async def load(self) -> CredentialT: ...

    async def save(self, credentials: CredentialT) -> None: ...


class _RefreshFlight[CredentialT]:
    def __init__(self, expected: CredentialT) -> None:
        self.expected = expected
        self.event = Event()
        self.result: CredentialT | None = None
        self.error: ModelAuthenticationError | None = None


class _CredentialManager[CredentialT]:
    def __init__(
        self,
        source: _CredentialSource[CredentialT],
        *,
        provider: str,
        credential_type: type[CredentialT],
        refresh: Refresh[CredentialT],
        identity: Callable[[CredentialT], tuple[str, ...]],
        expires_at: Callable[[CredentialT], datetime],
        validate: Callable[[CredentialT], bool],
        refresh_window: timedelta,
    ) -> None:
        if refresh_window < timedelta(0):
            raise ValueError("refresh_window must not be negative")
        self._source = source
        self._provider = provider
        self._credential_type = credential_type
        self._refresh = refresh
        self._identity = identity
        self._expires_at = expires_at
        self._validate = validate
        self._refresh_window = refresh_window
        self._current: CredentialT | None = None
        self._refresh_flight: _RefreshFlight[CredentialT] | None = None
        self._lock = Lock()

    async def prepare(self) -> tuple[CredentialT, Callable[[], Awaitable[CredentialT]]]:
        loaded = await self._load()
        flight: _RefreshFlight[CredentialT] | None = None
        leader = False
        async with self._lock:
            self._adopt(loaded)
            if self._stale(loaded):
                flight, leader = self._begin_refresh(loaded)
        used = loaded if flight is None else await self._resolve_flight(flight, leader=leader)

        async def replay() -> CredentialT:
            latest = await self._load()
            async with self._lock:
                self._require_same_account(used, latest)
                self._adopt(latest)
                if latest != used:
                    return latest
                replay_flight, replay_leader = self._begin_refresh(latest)
            return await self._resolve_flight(replay_flight, leader=replay_leader)

        return used, replay

    async def _load(self) -> CredentialT:
        try:
            value = await self._source.load()
        except ModelAuthenticationError:
            raise
        except Exception:
            raise ModelAuthenticationError(
                self._provider,
                "The Model credential source could not provide usable credentials.",
            ) from None
        if not isinstance(value, self._credential_type) or not self._valid(value):
            raise ModelAuthenticationError(
                self._provider,
                "The Model credential source returned an invalid value.",
            )
        return value

    def _begin_refresh(self, expected: CredentialT) -> tuple[_RefreshFlight[CredentialT], bool]:
        if self._refresh_flight is not None:
            return self._refresh_flight, False
        flight = _RefreshFlight(expected)
        self._refresh_flight = flight
        return flight, True

    async def _resolve_flight(self, flight: _RefreshFlight[CredentialT], *, leader: bool) -> CredentialT:
        if leader:
            await self._execute_flight(flight)
        else:
            await flight.event.wait()
        if flight.error is not None:
            raise flight.error
        if flight.result is None:
            raise ModelAuthenticationError(self._provider, "The Model OAuth refresh did not complete.")
        return flight.result

    async def _execute_flight(self, flight: _RefreshFlight[CredentialT]) -> None:
        try:
            result = await self._perform_refresh(flight.expected)
        except ModelAuthenticationError as exc:
            await self._finish_flight(flight, error=exc)
        except BaseException:
            error = ModelAuthenticationError(self._provider, "The Model OAuth refresh was interrupted.")
            with CancelScope(shield=True):
                await self._finish_flight(flight, error=error)
            raise
        else:
            await self._finish_flight(flight, result=result)

    async def _finish_flight(
        self,
        flight: _RefreshFlight[CredentialT],
        *,
        result: CredentialT | None = None,
        error: ModelAuthenticationError | None = None,
    ) -> None:
        async with self._lock:
            if self._refresh_flight is flight:
                self._refresh_flight = None
            if result is not None:
                self._current = result
            flight.result = result
            flight.error = error
            flight.event.set()

    async def _perform_refresh(self, expected: CredentialT) -> CredentialT:
        current = await self._load()
        self._require_same_account(expected, current)
        if current != expected:
            return current
        try:
            refreshed = await self._refresh(current)
        except ModelAuthenticationError:
            raise
        except Exception:
            raise CredentialRefreshError(self._provider, "The Model OAuth refresh failed.") from None
        if not isinstance(refreshed, self._credential_type) or not self._valid(refreshed):
            raise CredentialRefreshError(self._provider, "The Model OAuth refresh returned an invalid value.")
        if datetime.now(UTC) >= self._expires_at(refreshed).astimezone(UTC):
            raise CredentialRefreshError(self._provider, "The Model OAuth refresh returned expired credentials.")
        self._require_same_account(current, refreshed)
        try:
            await self._source.save(refreshed)
        except Exception:
            raise CredentialPersistenceError(
                self._provider,
                "The refreshed Model credentials could not be persisted.",
            ) from None
        return refreshed

    def _adopt(self, credentials: CredentialT) -> None:
        if self._current is not None:
            self._require_same_account(self._current, credentials)
        self._current = credentials

    def _require_same_account(self, expected: CredentialT, actual: CredentialT) -> None:
        if self._identity(expected) != self._identity(actual):
            raise ModelAuthenticationError(
                self._provider,
                "The active Model account changed during the request.",
            )

    def _stale(self, credentials: CredentialT) -> bool:
        return datetime.now(UTC) >= self._expires_at(credentials).astimezone(UTC) - self._refresh_window

    def _valid(self, credentials: CredentialT) -> bool:
        try:
            expires_at = self._expires_at(credentials)
            return isinstance(expires_at, datetime) and expires_at.tzinfo is not None and self._validate(credentials)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return False


class _ModelOAuthAuth[CredentialT](httpx2.Auth):
    def __init__(
        self,
        manager: _CredentialManager[CredentialT],
        *,
        base_url: str,
        headers: Callable[[CredentialT], Mapping[str, str]],
        protected_headers: tuple[str, ...],
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise ValueError("Model OAuth base_url must be an absolute HTTPS URL")
        self._manager = manager
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = parsed.port or 443
        self._headers = headers
        self._protected_headers = protected_headers

    def sync_auth_flow(self, request: httpx2.Request):
        del request
        raise UserError("Model OAuth requires an async HTTP client.")

    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        if not self._matches_origin(str(request.url)):
            self._strip_protected_headers(request)
            yield request
            return
        await request.aread()
        credentials, replay = await self._manager.prepare()
        self._apply(request, credentials)
        response = yield request
        if response.status_code != 401:
            return
        await response.aread()
        self._apply(request, await replay())
        yield request

    def _matches_origin(self, url: str) -> bool:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return parsed.scheme == self._scheme and parsed.hostname == self._host and port == self._port

    async def protect_redirect(self, response: httpx2.Response) -> None:
        if not response.has_redirect_location:
            return
        location = response.headers.get("location")
        if location is not None and not self._matches_origin(urljoin(str(response.request.url), location)):
            self._strip_protected_headers(response.request)

    def _strip_protected_headers(self, request: httpx2.Request) -> None:
        for name in self._protected_headers:
            request.headers.pop(name, None)

    def _apply(self, request: httpx2.Request, credentials: CredentialT) -> None:
        for name, value in self._headers(credentials).items():
            request.headers[name] = value


class _ModelOAuthOpenAIProvider(OpenAIProvider):
    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        http_client: httpx2.AsyncClient,
        owns_http_client: bool,
        http_client_factory: Callable[[], httpx2.AsyncClient],
    ) -> None:
        self._provider_name = provider_name
        super().__init__(
            base_url=base_url,
            api_key=f"{provider_name}-subscription-auth",
            http_client=http_client,
        )
        self._client.max_retries = 0
        if owns_http_client:
            self._own_http_client = http_client
            self._http_client_factory = http_client_factory

    @property
    def name(self) -> str:
        return self._provider_name


class CodexSubscriptionModel(WrapperModel):
    """Apply the Codex Responses dialect and Thread-derived affinity headers."""

    @staticmethod
    def _codex_settings(model_settings: ModelSettings | None) -> ModelSettings:
        settings: dict[str, Any] = dict(model_settings or {})
        for name in _CODEX_UNSUPPORTED_SETTINGS:
            settings.pop(name, None)
        settings["openai_store"] = False
        raw_headers = cast(Mapping[str, str] | None, settings.get("extra_headers"))
        headers = dict(raw_headers or {})
        lower = {name.lower() for name in headers}
        thread_id = next((value for name, value in headers.items() if name.lower() == "x-session-id"), None)
        if thread_id is not None:
            for name in ("session-id", "thread-id", "x-client-request-id"):
                if name not in lower:
                    headers[name] = thread_id
        settings["extra_headers"] = headers
        return cast(ModelSettings, settings)

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        async with self.request_stream(messages, model_settings, model_request_parameters) as response:
            async for _ in response:
                pass
            return response.get()

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        async with self.wrapped.request_stream(
            messages,
            self._codex_settings(model_settings),
            model_request_parameters,
            run_context,
        ) as response:
            yield response

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        del messages, model_settings, model_request_parameters
        raise UserError("Server-side token counting is unavailable for Codex subscription models.")


def build_codex_model(
    model_name: str,
    *,
    credential_source: CodexCredentialSource,
    refresh: Refresh[CodexCredentials] | None = None,
    refresh_window: timedelta = _DEFAULT_REFRESH_WINDOW,
    http_client: httpx2.AsyncClient | None = None,
    originator: str = "a13n-harness",
) -> CodexSubscriptionModel:
    """Build one Codex Responses Model backed by an application credential source."""

    owns_http_client = http_client is None
    client = http_client or httpx2.AsyncClient()
    if client.auth is not None:
        raise UserError("The Model OAuth HTTP client must not already have authentication configured.")
    client.follow_redirects = False
    client_ref = [client]

    async def selected_refresh(credentials: CodexCredentials) -> CodexCredentials:
        if refresh is not None:
            return await refresh(credentials)
        return await refresh_codex_credentials(credentials, http_client=client_ref[0])

    manager = _CredentialManager(
        credential_source,
        provider="openai-codex",
        credential_type=CodexCredentials,
        refresh=selected_refresh,
        identity=lambda value: (value.account_id,),
        expires_at=lambda value: value.expires_at,
        validate=lambda value: bool(
            value.account_id
            and value.access_token
            and value.refresh_token
            and (value.id_token is None or value.id_token)
        ),
        refresh_window=refresh_window,
    )
    auth = _ModelOAuthAuth(
        manager,
        base_url=_CODEX_BASE_URL,
        headers=lambda value: {
            "Authorization": f"Bearer {value.access_token}",
            "chatgpt-account-id": value.account_id,
            "originator": originator,
        },
        protected_headers=("Authorization", "chatgpt-account-id", "originator"),
    )
    client.auth = auth
    client.event_hooks["response"].append(auth.protect_redirect)

    def create_http_client() -> httpx2.AsyncClient:
        reopened = httpx2.AsyncClient(
            auth=auth,
            follow_redirects=False,
            event_hooks={"response": [auth.protect_redirect]},
        )
        client_ref[0] = reopened
        return reopened

    provider = _ModelOAuthOpenAIProvider(
        provider_name="openai-codex",
        base_url=_CODEX_BASE_URL,
        http_client=client,
        owns_http_client=owns_http_client,
        http_client_factory=create_http_client,
    )
    return CodexSubscriptionModel(OpenAIResponsesModel(cast(Any, model_name), provider=provider))


def build_grok_model(
    model_name: str,
    *,
    credential_source: GrokCredentialSource,
    refresh: Refresh[GrokCredentials] | None = None,
    refresh_window: timedelta = _DEFAULT_REFRESH_WINDOW,
    http_client: httpx2.AsyncClient | None = None,
) -> OpenAIResponsesModel:
    """Build one Grok Responses Model backed by an application credential source."""

    owns_http_client = http_client is None
    client = http_client or httpx2.AsyncClient()
    if client.auth is not None:
        raise UserError("The Model OAuth HTTP client must not already have authentication configured.")
    client.follow_redirects = False
    client_ref = [client]

    async def selected_refresh(credentials: GrokCredentials) -> GrokCredentials:
        if refresh is not None:
            return await refresh(credentials)
        return await refresh_grok_credentials(credentials, http_client=client_ref[0])

    manager = _CredentialManager(
        credential_source,
        provider="grok",
        credential_type=GrokCredentials,
        refresh=selected_refresh,
        identity=lambda value: (value.account_id, value.issuer, value.client_id),
        expires_at=lambda value: value.expires_at,
        validate=lambda value: bool(
            value.account_id
            and value.auth_mode
            and value.create_time.tzinfo is not None
            and value.issuer
            and urlsplit(value.issuer).scheme == "https"
            and urlsplit(value.issuer).hostname
            and value.client_id
            and value.access_token
            and (value.refresh_token is None or value.refresh_token)
        ),
        refresh_window=refresh_window,
    )
    auth = _ModelOAuthAuth(
        manager,
        base_url=_GROK_BASE_URL,
        headers=lambda value: {"Authorization": f"Bearer {value.access_token}"},
        protected_headers=("Authorization",),
    )
    client.auth = auth
    client.event_hooks["response"].append(auth.protect_redirect)

    def create_http_client() -> httpx2.AsyncClient:
        reopened = httpx2.AsyncClient(
            auth=auth,
            follow_redirects=False,
            event_hooks={"response": [auth.protect_redirect]},
        )
        client_ref[0] = reopened
        return reopened

    provider = _ModelOAuthOpenAIProvider(
        provider_name="grok",
        base_url=_GROK_BASE_URL,
        http_client=client,
        owns_http_client=owns_http_client,
        http_client_factory=create_http_client,
    )
    return OpenAIResponsesModel(cast(Any, model_name), provider=provider)


__all__ = ["CodexSubscriptionModel", "build_codex_model", "build_grok_model"]
