"""SDK-first OAuth authentication for native Models."""

from importlib import import_module
from typing import TYPE_CHECKING

from .models import (
    CodexCredentials,
    CodexCredentialSource,
    CredentialPersistenceError,
    CredentialRefreshError,
    DeviceAuthorizationError,
    GrokCredentials,
    GrokCredentialSource,
    ModelAuthenticationError,
)
from .oauth import (
    CodexDeviceAuthorization,
    CodexDeviceAuthorizationFlow,
    CodexOAuthFlow,
    GrokDeviceAuthorization,
    GrokDeviceAuthorizationFlow,
    GrokOAuthFlow,
    OAuthFlow,
    refresh_codex_credentials,
    refresh_grok_credentials,
)

if TYPE_CHECKING:
    from .runtime import CodexSubscriptionModel, build_codex_account_auth, build_codex_model, build_grok_model


def __getattr__(name: str) -> object:
    # Credential discovery does not need native provider Models or their SDKs.
    if name in {"CodexSubscriptionModel", "build_codex_account_auth", "build_codex_model", "build_grok_model"}:
        value = vars(import_module(".runtime", __name__))[name]
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CodexCredentialSource",
    "CodexCredentials",
    "CodexDeviceAuthorization",
    "CodexDeviceAuthorizationFlow",
    "CodexOAuthFlow",
    "CodexSubscriptionModel",
    "CredentialPersistenceError",
    "CredentialRefreshError",
    "DeviceAuthorizationError",
    "GrokCredentialSource",
    "GrokCredentials",
    "GrokDeviceAuthorization",
    "GrokDeviceAuthorizationFlow",
    "GrokOAuthFlow",
    "ModelAuthenticationError",
    "OAuthFlow",
    "build_codex_account_auth",
    "build_codex_model",
    "build_grok_model",
    "refresh_codex_credentials",
    "refresh_grok_credentials",
]
