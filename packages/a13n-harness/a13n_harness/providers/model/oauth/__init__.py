"""SDK-first OAuth authentication for native Models."""
# ruff: noqa: F401  # `_EXPORTS` owns the surface; these imports serve type checkers.

from typing import TYPE_CHECKING, Any

from a13n_harness._exports import exported_names, load_export

if TYPE_CHECKING:
    from .codex_login import CodexDeviceAuthorization, CodexDeviceAuthorizationFlow, CodexLoginFlow
    from .copilot import (
        CopilotCredentials,
        CopilotCredentialSource,
        CopilotRefresh,
        copilot_account_id,
        refresh_copilot_credentials,
    )
    from .copilot_runtime import build_copilot_model, discover_copilot_models
    from .flow import OAuthFlow
    from .grok import (
        GrokDeviceAuthorization,
        GrokDeviceAuthorizationFlow,
        GrokOAuthFlow,
        refresh_grok_credentials,
    )
    from .models import (
        CodexLoginResult,
        CredentialPersistenceError,
        CredentialRefreshError,
        DeviceAuthorizationError,
        GrokCredentials,
        GrokCredentialSource,
        GrokRefresh,
        ModelAuthenticationError,
        RefreshNotDispatched,
    )
    from .runtime import build_grok_model
    from .source import ProcessCopilotCredentialSource, ProcessGrokCredentialSource

# Credential discovery does not need native provider Models or their SDKs.
_EXPORTS = {
    "a13n_harness.providers.model.oauth.codex_login": (
        "CodexDeviceAuthorization",
        "CodexDeviceAuthorizationFlow",
        "CodexLoginFlow",
    ),
    "a13n_harness.providers.model.oauth.models": (
        "CodexLoginResult",
        "CredentialPersistenceError",
        "CredentialRefreshError",
        "DeviceAuthorizationError",
        "GrokCredentialSource",
        "GrokCredentials",
        "GrokRefresh",
        "ModelAuthenticationError",
        "RefreshNotDispatched",
    ),
    "a13n_harness.providers.model.oauth.copilot": (
        "CopilotCredentials",
        "CopilotCredentialSource",
        "CopilotRefresh",
        "copilot_account_id",
        "refresh_copilot_credentials",
    ),
    "a13n_harness.providers.model.oauth.copilot_runtime": ("build_copilot_model", "discover_copilot_models"),
    "a13n_harness.providers.model.oauth.flow": ("OAuthFlow",),
    "a13n_harness.providers.model.oauth.grok": (
        "GrokDeviceAuthorization",
        "GrokDeviceAuthorizationFlow",
        "GrokOAuthFlow",
        "refresh_grok_credentials",
    ),
    "a13n_harness.providers.model.oauth.runtime": ("build_grok_model",),
    "a13n_harness.providers.model.oauth.source": ("ProcessGrokCredentialSource", "ProcessCopilotCredentialSource"),
}


def __getattr__(name: str) -> Any:
    return load_export(__name__, globals(), _EXPORTS, name)


__all__ = exported_names(_EXPORTS)  # pyright: ignore[reportUnsupportedDunderAll]
