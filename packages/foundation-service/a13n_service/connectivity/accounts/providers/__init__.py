"""Built-in Account tools: provider-owned target validation and action construction."""

import httpx2

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.native_actions import NativeAction
from a13n_service.endpoint_policy import EndpointPolicy

from . import github, lark, slack

_PROVIDERS = {"slack": slack.PROVIDER, "lark": lark.PROVIDER, "github": github.PROVIDER}


def validate_scope(provider: str, scope: JsonObject, tools: tuple[str, ...]) -> None:
    definition = _PROVIDERS.get(provider)
    if definition is None or not set(tools) <= definition.tools:
        raise ValueError("unsupported_account_tools")
    definition.scope.model_validate(scope)


def account_actions(
    provider: str,
    configuration: JsonObject,
    credentials: JsonObject,
    target_scope: JsonObject,
    http: httpx2.AsyncClient,
    endpoints: EndpointPolicy,
) -> dict[str, NativeAction]:
    definition = _PROVIDERS.get(provider)
    if definition is None:
        raise ValueError("account_provider_unavailable")
    return definition.actions(configuration, credentials, target_scope, http, endpoints)


__all__ = ["account_actions", "validate_scope"]
