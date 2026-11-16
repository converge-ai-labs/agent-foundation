"""Secret-free account references and application-private OAuth bundles."""

from __future__ import annotations

from typing import Literal

from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken, ProtectedResourceMetadata
from pydantic import BaseModel, ConfigDict, Field

from a13n_harness_ui.errors import HarnessUiError


class OAuthError(HarnessUiError):
    """A safe, actionable authorization error; never contains upstream response bodies."""


class OAuthBinding(BaseModel):
    """Account generation captured before publication; rotation does not change it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    binding_id: str
    generation: str


class OAuthBundle(BaseModel):
    """Private durable SDK state, committed as one unit with rotated credentials."""

    model_config = ConfigDict(extra="forbid")
    resource_url: str
    issuer: str
    metadata: OAuthMetadata
    resource_metadata: ProtectedResourceMetadata | None = None
    protocol_version: str | None = None
    client: OAuthClientInformationFull
    tokens: OAuthToken | None = None
    expires_at: float | None = None
    requested_scope: str | None = None


class OAuthAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binding: OAuthBinding
    owner: str
    configuration_digest: str
    credential_version: int = 0
    refresh_pending: bool = False
    bundle: OAuthBundle | None = Field(default=None, repr=False)


def require_scopes(tokens: OAuthToken, requested: str | None) -> None:
    """An explicit scope is a ceiling; an omitted scope inherits the request."""
    if requested is not None:
        if tokens.scope is None:
            tokens.scope = requested
        if not set(tokens.scope.split()).issubset(requested.split()):
            raise OAuthError("The provider granted unexpected scopes; reconnect.", code="mcp_oauth_scope_mismatch")


class AuthorizationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["mcp", "connector"]
    id: str = Field(min_length=1, max_length=128)


class OAuthLoginStatus(BaseModel):
    attempt_id: str
    state: Literal["connecting", "waiting", "connected", "failed", "cancelled"]
    authorization_url: str | None = None
    error: str | None = None


class AuthorizationStatus(BaseModel):
    state: Literal["disconnected", "connected", "expired", "reconnect_required"]
    login: OAuthLoginStatus | None = None
    scopes: tuple[str, ...] = ()
    expires_at: float | None = None
