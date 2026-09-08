"""SDK-first OAuth authentication for native Models."""

from importlib import import_module
from typing import TYPE_CHECKING

from .models import (
    CodexLoginResult,
    CredentialPersistenceError,
    CredentialRefreshError,
    DeviceAuthorizationError,
    GrokCredentials,
    GrokCredentialSource,
    ModelAuthenticationError,
)
from .oauth import (
    GrokDeviceAuthorization,
    GrokDeviceAuthorizationFlow,
    GrokOAuthFlow,
    OAuthFlow,
    refresh_grok_credentials,
)

if TYPE_CHECKING:
    from .codex import CodexRequestModel
    from .codex_login import CodexDeviceAuthorization, CodexDeviceAuthorizationFlow, CodexLoginFlow
    from .runtime import build_grok_model


def __getattr__(name: str) -> object:
    # Credential discovery does not need native provider Models or their SDKs.
    modules = {
        "CodexRequestModel": ".codex",
        "CodexLoginFlow": ".codex_login",
        "CodexDeviceAuthorization": ".codex_login",
        "CodexDeviceAuthorizationFlow": ".codex_login",
        "build_grok_model": ".runtime",
    }
    if (module := modules.get(name)) is not None:
        value = vars(import_module(module, __name__))[name]
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CodexDeviceAuthorization",
    "CodexDeviceAuthorizationFlow",
    "CodexLoginFlow",
    "CodexLoginResult",
    "CodexRequestModel",
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
    "build_grok_model",
    "refresh_grok_credentials",
]
