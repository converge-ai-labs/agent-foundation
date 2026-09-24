"""GitHub Copilot credential lifecycle; device authorization and inference stay upstream."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

import httpx2
from pydantic import TypeAdapter, ValidationError

if TYPE_CHECKING:
    from pydantic_ai.providers.github_copilot import GitHubCopilotCredentials

from .models import CredentialRefreshError, ModelAuthenticationError, RefreshNotDispatched


@dataclass(frozen=True, slots=True, kw_only=True)
class CopilotCredentials:
    """An upstream grant bound to an account and application with absolute expiry.

    Hosts normalize issuance durations once, before publication. Loading must not
    reset the expiry clock. The upstream value remains the sole token envelope.
    """

    account_id: str
    client_id: str
    credentials: GitHubCopilotCredentials = field(repr=False)
    expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    issuer: str = "https://github.com"
    source_id: str = "process"

    @property
    def provider(self) -> str:
        return "github-copilot"

    @property
    def access_token(self) -> str:
        return self.credentials.access_token

    @property
    def refresh_token(self) -> str | None:
        return self.credentials.refresh_token

    @classmethod
    def issued(
        cls,
        credentials: GitHubCopilotCredentials,
        *,
        account_id: str,
        client_id: str,
        now: datetime | None = None,
        source_id: str = "process",
    ) -> CopilotCredentials:
        credentials = validate_token_envelope(credentials)
        issued_at = now or datetime.now(UTC)
        if issued_at.tzinfo is None:
            raise ValueError("Credential issuance must have a timezone")
        return cls(
            account_id=account_id,
            client_id=client_id,
            source_id=source_id,
            credentials=credentials,
            expires_at=issued_at + timedelta(seconds=credentials.expires_in) if credentials.expires_in else None,
            refresh_expires_at=issued_at + timedelta(seconds=credentials.refresh_token_expires_in)
            if credentials.refresh_token_expires_in
            else None,
        )


def validate_token_envelope(value: GitHubCopilotCredentials) -> GitHubCopilotCredentials:
    """Validate even directly constructed upstream dataclass instances."""
    from pydantic_ai.providers.github_copilot import GitHubCopilotCredentials

    if not isinstance(value, GitHubCopilotCredentials):
        raise ValueError("Invalid Copilot credential envelope")
    return TypeAdapter(GitHubCopilotCredentials).validate_python(asdict(value))


type CopilotRefresh = Callable[[CopilotCredentials], Awaitable[CopilotCredentials]]


class CopilotCredentialSource(Protocol):
    async def load(self) -> CopilotCredentials: ...

    async def rotate(self, expected: CopilotCredentials, exchange: CopilotRefresh) -> CopilotCredentials: ...


async def copilot_account_id(credentials: GitHubCopilotCredentials, *, http_client: httpx2.AsyncClient) -> str:
    """Identify an explicitly authorized GitHub.com grant without making an inference request."""
    try:
        response = await http_client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {credentials.access_token}", "Accept": "application/vnd.github+json"},
            follow_redirects=False,
            auth=httpx2.Auth(),
            timeout=30,
        )
        data = response.json()
        if response.status_code != 200 or not isinstance(data, dict):
            raise ValueError
        login = data.get("login")
        if (
            not isinstance(login, str)
            or not login
            or not login.isascii()
            or not all(c.isalnum() or c == "-" for c in login)
        ):
            raise ValueError
        return login.casefold()
    except Exception:
        raise ModelAuthenticationError(
            "github-copilot", "The authorized GitHub account could not be identified."
        ) from None


async def refresh_copilot_credentials(
    credentials: CopilotCredentials, *, http_client: httpx2.AsyncClient
) -> CopilotCredentials:
    """Refresh a device-flow-issued grant without a client secret or transport retry.

    GitHub invalidates the old pair on rotation. Any failure after possible
    dispatch is uncertain; only the Host's publication makes a replacement usable.
    """
    from pydantic_ai.providers.github_copilot import GitHubCopilotCredentials

    if not credentials.refresh_token or (
        credentials.refresh_expires_at is not None and datetime.now(UTC) >= credentials.refresh_expires_at
    ):
        raise RefreshNotDispatched("github-copilot", "The Copilot account requires a new login.")
    try:
        response = await http_client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": credentials.client_id,
                "grant_type": "refresh_token",
                "refresh_token": credentials.refresh_token,
            },
            headers={"Accept": "application/json"},
            follow_redirects=False,
            auth=httpx2.Auth(),
            timeout=30,
        )
        if response.status_code != 200:
            raise ValueError
        value = TypeAdapter(GitHubCopilotCredentials).validate_json(response.content)
        # An expiring grant must return its complete replacement, not a usable
        # access token paired with a now-invalid old refresh token.
        if value.refresh_token is None or value.expires_in is None:
            raise ValueError
        return CopilotCredentials.issued(
            value, account_id=credentials.account_id, client_id=credentials.client_id, source_id=credentials.source_id
        )
    except (ValueError, ValidationError, httpx2.HTTPError):
        raise CredentialRefreshError("github-copilot", "The Copilot credential refresh failed.") from None
