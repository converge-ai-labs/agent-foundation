"""Request-fresh authentication around the upstream native Copilot provider."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx2
from pydantic import ValidationError
from pydantic_ai import UserError
from pydantic_ai.models.github_copilot import GitHubCopilotModel
from pydantic_ai.providers.github_copilot import GitHubCopilotProvider

from a13n_harness.http import outbound_tls_verify

from .copilot import (
    CopilotCredentials,
    CopilotCredentialSource,
    CopilotRefresh,
    refresh_copilot_credentials,
    validate_token_envelope,
)
from .models import CredentialRefreshError, ModelAuthenticationError
from .rotation import require_same_account
from .runtime import _OAuthAuth

_COPILOT_BASE_URL = "https://api.githubcopilot.com"


class _CopilotAuthentication:
    def __init__(self, source: CopilotCredentialSource, refresh: CopilotRefresh, refresh_window: timedelta):
        if refresh_window < timedelta(0):
            raise ValueError("refresh_window must not be negative")
        self._source = source
        self._refresh = refresh
        self._refresh_window = refresh_window
        self._bound: CopilotCredentials | None = None

    async def prepare(self) -> tuple[CopilotCredentials, Callable[[], Awaitable[CopilotCredentials]]]:
        try:
            current = await self._source.load()
        except ModelAuthenticationError:
            raise
        except Exception:
            raise ModelAuthenticationError(
                "github-copilot", "The Copilot account could not provide credentials."
            ) from None
        self._validate(current)
        if self._bound is not None:
            require_same_account(self._bound, current)
        self._bound = current
        if current.expires_at is not None and datetime.now(UTC) >= current.expires_at - self._refresh_window:
            current = await self._rotate(current)

        async def replay() -> CopilotCredentials:
            return await self._rotate(current)

        return current, replay

    async def _rotate(self, expected: CopilotCredentials) -> CopilotCredentials:
        async def exchange(current: CopilotCredentials) -> CopilotCredentials:
            try:
                value = await self._refresh(current)
            except ModelAuthenticationError:
                raise
            except Exception:
                raise CredentialRefreshError("github-copilot", "The Copilot credential refresh failed.") from None
            self._validate(value)
            require_same_account(current, value)
            if value.expires_at is not None and datetime.now(UTC) >= value.expires_at:
                raise CredentialRefreshError("github-copilot", "The refreshed Copilot credentials are expired.")
            return value

        result = await self._source.rotate(expected, exchange)
        self._validate(result)
        require_same_account(expected, result)
        return result

    @staticmethod
    def _validate(value: CopilotCredentials) -> None:
        try:
            if not isinstance(value, CopilotCredentials) or not (
                value.account_id
                and value.client_id
                and value.issuer == "https://github.com"
                and (value.expires_at is None or value.expires_at.tzinfo is not None)
                and (value.refresh_expires_at is None or value.refresh_expires_at.tzinfo is not None)
            ):
                raise ValueError
            validate_token_envelope(value.credentials)
        except (ValueError, ValidationError):
            raise ModelAuthenticationError(
                "github-copilot", "The Copilot credential source returned an invalid value."
            ) from None


class _CopilotProvider(GitHubCopilotProvider):
    def __init__(self, *, client: httpx2.AsyncClient, owns_client: bool, factory: Callable[[], httpx2.AsyncClient]):
        # Supplying the HTTP client preserves the upstream headers and profile;
        # supplying an OpenAI client would bypass the Copilot defaults.
        super().__init__(api_key="copilot-subscription-auth", base_url=_COPILOT_BASE_URL, http_client=client)
        self.client.max_retries = 0
        if owns_client:
            self._own_http_client = client
            self._http_client_factory = factory


def _build_copilot_provider(
    *,
    credential_source: CopilotCredentialSource,
    refresh: CopilotRefresh | None = None,
    refresh_window: timedelta = timedelta(minutes=5),
    http_client: httpx2.AsyncClient | None = None,
) -> _CopilotProvider:
    """Build without loading a source, resolving ambient secrets or making requests."""
    client = http_client or httpx2.AsyncClient(verify=outbound_tls_verify())
    if client.auth is not None:
        raise UserError("The Model OAuth HTTP client must not already have authentication configured.")
    client.follow_redirects = False

    async def selected_refresh(credentials: CopilotCredentials) -> CopilotCredentials:
        if refresh is not None:
            return await refresh(credentials)
        return await refresh_copilot_credentials(credentials, http_client=auth.client)

    manager = _CopilotAuthentication(credential_source, selected_refresh, refresh_window)
    auth = _OAuthAuth(manager, base_url=_COPILOT_BASE_URL, client=client)
    client.auth = auth
    client.event_hooks["response"].append(auth.protect_redirect)

    def factory() -> httpx2.AsyncClient:
        reopened = httpx2.AsyncClient(
            verify=outbound_tls_verify(),
            auth=auth,
            follow_redirects=False,
            event_hooks={"response": [auth.protect_redirect]},
        )
        auth.client = reopened
        return reopened

    return _CopilotProvider(client=client, owns_client=http_client is None, factory=factory)


def build_copilot_model(
    model_name: str,
    *,
    credential_source: CopilotCredentialSource,
    refresh: CopilotRefresh | None = None,
    refresh_window: timedelta = timedelta(minutes=5),
    http_client: httpx2.AsyncClient | None = None,
) -> GitHubCopilotModel:
    """Construct the native Chat Completions Model without credential I/O."""
    return GitHubCopilotModel(
        model_name,
        provider=_build_copilot_provider(
            credential_source=credential_source, refresh=refresh, refresh_window=refresh_window, http_client=http_client
        ),
    )


async def discover_copilot_models(
    *,
    credential_source: CopilotCredentialSource,
    http_client: httpx2.AsyncClient | None = None,
) -> tuple[str, ...]:
    """Explicit authenticated discovery, never inference or an entitlement guarantee."""
    provider = _build_copilot_provider(credential_source=credential_source, http_client=http_client)
    async with provider:
        page = await provider.client.models.list()
        # Do not traverse a server-supplied next-page URL with credentials.
        return tuple(
            sorted(
                {
                    entry.id
                    for entry in page.data
                    if isinstance(entry.id, str)
                    and entry.id
                    and not any(char.isspace() for char in entry.id)
                    and isinstance((entry.model_extra or {}).get("supported_endpoints"), list)
                    and "/chat/completions" in (entry.model_extra or {})["supported_endpoints"]
                }
            )
        )
