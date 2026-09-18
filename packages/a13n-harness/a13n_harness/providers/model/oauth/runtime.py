"""Request-fresh Model OAuth lifecycle and native Model constructors."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

import httpx2
from pydantic_ai import UserError
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from .models import (
    CredentialRefreshError,
    GrokCredentials,
    GrokCredentialSource,
    GrokRefresh,
    ModelAuthenticationError,
)
from .oauth import refresh_grok_credentials
from .source import require_same_account

_GROK_BASE_URL = "https://api.x.ai/v1"
_DEFAULT_REFRESH_WINDOW = timedelta(minutes=5)


class _GrokAuthentication:
    def __init__(self, source: GrokCredentialSource, refresh: GrokRefresh, refresh_window: timedelta):
        if refresh_window < timedelta(0):
            raise ValueError("refresh_window must not be negative")
        self._source = source
        self._refresh = refresh
        self._refresh_window = refresh_window
        self._bound: GrokCredentials | None = None

    async def prepare(self) -> tuple[GrokCredentials, Callable[[], Awaitable[GrokCredentials]]]:
        try:
            current = await self._source.load()
        except ModelAuthenticationError:
            raise
        except Exception:
            raise ModelAuthenticationError(
                "grok", "The Model credential source could not provide usable credentials."
            ) from None
        self._validate(current)
        if self._bound is not None:
            require_same_account(self._bound, current)
        self._bound = current
        if datetime.now(UTC) >= current.expires_at.astimezone(UTC) - self._refresh_window:
            current = await self._rotate(current)

        async def replay() -> GrokCredentials:
            return await self._rotate(current)

        return current, replay

    async def _rotate(self, expected: GrokCredentials) -> GrokCredentials:
        async def exchange(current: GrokCredentials) -> GrokCredentials:
            try:
                result = await self._refresh(current)
            except ModelAuthenticationError:
                raise
            except Exception:
                raise CredentialRefreshError("grok", "The Model OAuth refresh failed.") from None
            self._validate(result)
            require_same_account(current, result)
            if datetime.now(UTC) >= result.expires_at.astimezone(UTC):
                raise CredentialRefreshError("grok", "The Model OAuth refresh returned expired credentials.")
            return result

        result = await self._source.rotate(expected, exchange)
        self._validate(result)
        require_same_account(expected, result)
        return result

    @staticmethod
    def _validate(value: GrokCredentials) -> None:
        if not isinstance(value, GrokCredentials) or not (
            value.account_id
            and value.auth_mode
            and value.create_time.tzinfo is not None
            and value.expires_at.tzinfo is not None
            and value.issuer
            and urlsplit(value.issuer).scheme == "https"
            and urlsplit(value.issuer).hostname
            and value.client_id
            and value.access_token
            and (value.refresh_token is None or value.refresh_token)
        ):
            raise ModelAuthenticationError("grok", "The Model credential source returned an invalid value.")


class _GrokAuth(httpx2.Auth):
    def __init__(
        self,
        manager: _GrokAuthentication,
        *,
        base_url: str,
        headers: Callable[[GrokCredentials], Mapping[str, str]],
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

    def _apply(self, request: httpx2.Request, credentials: GrokCredentials) -> None:
        for name, value in self._headers(credentials).items():
            request.headers[name] = value


class _GrokProvider(OpenAIProvider):
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


def build_grok_model(
    model_name: str,
    *,
    credential_source: GrokCredentialSource,
    refresh: GrokRefresh | None = None,
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

    manager = _GrokAuthentication(credential_source, selected_refresh, refresh_window)
    auth = _GrokAuth(
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

    provider = _GrokProvider(
        provider_name="grok",
        base_url=_GROK_BASE_URL,
        http_client=client,
        owns_http_client=owns_http_client,
        http_client_factory=create_http_client,
    )
    return OpenAIResponsesModel(cast(Any, model_name), provider=provider)


__all__ = ["build_grok_model"]
