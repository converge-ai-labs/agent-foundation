"""Provider-specific credentials and Host-owned persistence boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from pydantic_ai.exceptions import ModelAPIError


class ModelAuthenticationError(ModelAPIError):
    """A bounded failure while preparing Model OAuth credentials."""

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(model_name=provider, message=message)


class CredentialRefreshError(ModelAuthenticationError):
    """A provider rejected or returned an invalid refresh response."""


class DeviceAuthorizationError(CredentialRefreshError):
    """A safe terminal outcome from a provider's device authorization protocol."""

    def __init__(self, provider: str, reason: Literal["expired", "denied", "unsupported"]) -> None:
        self.reason = reason
        super().__init__(provider, f"Device authorization {reason}.")


class CredentialPersistenceError(ModelAuthenticationError):
    """Rotated credentials could not be persisted before use."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CodexCredentials:
    """One complete OpenAI Codex subscription credential set."""

    account_id: str
    expires_at: datetime
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    id_token: str | None = field(default=None, repr=False)


class CodexCredentialSource(Protocol):
    """Application-owned storage for Codex credentials."""

    async def load(self) -> CodexCredentials: ...

    async def save(self, credentials: CodexCredentials) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class GrokCredentials:
    """One complete Grok OAuth credential set."""

    account_id: str
    auth_mode: str
    create_time: datetime
    expires_at: datetime
    issuer: str
    client_id: str
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)


class GrokCredentialSource(Protocol):
    """Application-owned storage for Grok credentials."""

    async def load(self) -> GrokCredentials: ...

    async def save(self, credentials: GrokCredentials) -> None: ...


__all__ = [
    "CodexCredentialSource",
    "CodexCredentials",
    "CredentialPersistenceError",
    "CredentialRefreshError",
    "DeviceAuthorizationError",
    "GrokCredentialSource",
    "GrokCredentials",
    "ModelAuthenticationError",
]
