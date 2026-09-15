"""Run-fresh native Model construction with Host-owned credential sources."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from a13n_harness import AgentContext, infer_model
from a13n_harness.errors import ModelResolutionError
from a13n_harness.model_auth import GrokCredentials, GrokCredentialSource
from a13n_harness.models.inference import RequestHeadersModel
from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.providers import Provider, infer_provider_class

from a13n_harness_ui.configuration import (
    ApiKeyAuthentication,
    CodexSubscriptionAuthentication,
    GrokSubscriptionAuthentication,
)
from a13n_harness_ui.model_accounts.api_keys import ApiKeyStore
from a13n_harness_ui.model_presets import API_PROVIDER_BY_ROUTE

if TYPE_CHECKING:
    from pydantic_ai.providers.openai_codex import OpenAICodexCredentialSource

    from a13n_harness_ui.composition.models import ResolvedModelRecipe

_PROVIDER_ALIASES = {
    "gemini": "google-cloud",
    "google-gla": "google-cloud",
    "google-vertex": "google-cloud",
    "openai": "openai-responses",
    "grok": "openai-chat",
}
_GROK_BASE_URL = "https://api.x.ai/v1"


@dataclass(frozen=True, slots=True)
class CodexSubscriptionSource:
    """Host wiring for the official Codex provider credential source."""

    source: OpenAICodexCredentialSource


@dataclass(frozen=True, slots=True)
class GrokSubscriptionSource:
    """Host wiring for one Grok credential source and optional refresh override."""

    source: GrokCredentialSource
    refresh: Callable[[GrokCredentials], Awaitable[GrokCredentials]] | None = None


type SubscriptionSource = CodexSubscriptionSource | GrokSubscriptionSource


class HarnessUiModelResolver:
    """Resolve logical recipes with provider-owned OAuth lifecycles."""

    def __init__(
        self,
        recipes: Mapping[str, ResolvedModelRecipe],
        *,
        subscription_sources: Mapping[str, SubscriptionSource] | None = None,
        api_keys: ApiKeyStore | None = None,
    ) -> None:
        self._api_keys = api_keys
        self._recipes = MappingProxyType({key: value.model_copy(deep=True) for key, value in recipes.items()})
        self._subscription_sources = MappingProxyType(dict(subscription_sources or {}))

    def fresh(self) -> HarnessUiModelResolver:
        return HarnessUiModelResolver(
            self._recipes,
            subscription_sources=self._subscription_sources,
            api_keys=self._api_keys,
        )

    async def __call__(
        self,
        context: ModelResolutionContext[AgentContext],
        model_id: str,
    ) -> Model:
        recipe = self._recipes.get(model_id)
        if recipe is None:
            raise ModelResolutionError(
                "The requested Harness UI Model recipe is not present in this Run composition.",
                code="model_recipe_missing",
                details={"model_id": model_id},
            )
        authentication = recipe.authentication
        if isinstance(authentication, ApiKeyAuthentication):
            model = await self._api_key_model(recipe, authentication)
            header = recipe.model_configuration.get("session_affinity_header")
            if isinstance(header, str):
                return RequestHeadersModel(model, common_headers={header: context.deps.thread_id})
            return model
        if isinstance(authentication, CodexSubscriptionAuthentication):
            from a13n_harness.model_auth import CodexRequestModel

            from a13n_harness_ui.model_accounts.codex import BoundCodexCredentialSource

            source = self._required_subscription_source(
                "codex_subscription",
                CodexSubscriptionSource,
            )
            return CodexRequestModel(
                _model_name(recipe),
                credential_source=BoundCodexCredentialSource(source.source),
                thread_id=context.deps.thread_id,
            )
        if isinstance(authentication, GrokSubscriptionAuthentication):
            from a13n_harness.model_auth import build_grok_model

            source = self._required_subscription_source(
                "grok_subscription",
                GrokSubscriptionSource,
            )
            return build_grok_model(
                _model_name(recipe),
                credential_source=source.source,
                refresh=source.refresh,
            )
        raise ModelResolutionError(
            "The Model authentication kind is unsupported.",
            code="model_authentication_unsupported",
        )

    async def _api_key_model(self, recipe: ResolvedModelRecipe, authentication: ApiKeyAuthentication) -> Model:
        api_key = (
            os.environ.get(authentication.env)
            if authentication.env is not None
            else await self._api_keys.load(authentication.credential_ref)
            if self._api_keys is not None and authentication.credential_ref is not None
            else None
        )
        if not api_key:
            raise ModelResolutionError(
                "The required Harness UI Model credential source is unavailable.",
                code="model_credential_missing",
                details={"source": authentication.env or authentication.credential_ref},
            )
        route_provider, separator, model_name = recipe.route.partition(":")
        provider_name = _PROVIDER_ALIASES.get(route_provider, route_provider)
        route = f"{provider_name}:{model_name}" if separator else recipe.route
        base_url = recipe.model_configuration.get("base_url")
        if base_url is not None and not isinstance(base_url, str):
            raise ModelResolutionError("Invalid Model base URL.", code="model_reconstruction_failed")

        def provider_factory(requested_provider: str) -> Provider[Any]:
            if requested_provider != provider_name:
                raise ModelResolutionError(
                    "The native Model requested a Provider outside its resolved recipe.",
                    code="model_provider_mismatch",
                    details={"provider": requested_provider},
                )
            if route_provider == "grok":
                from openai import AsyncOpenAI
                from pydantic_ai.providers.openai import OpenAIProvider

                return OpenAIProvider(openai_client=AsyncOpenAI(api_key=api_key, base_url=base_url or _GROK_BASE_URL))
            provider_type = infer_provider_class(requested_provider)
            constructor = cast(Callable[..., Provider[Any]], provider_type)
            provider = API_PROVIDER_BY_ROUTE.get(provider_name)
            if base_url is not None and provider is not None and provider.transport == "openai-client":
                from openai import AsyncOpenAI

                return constructor(openai_client=AsyncOpenAI(api_key=api_key, base_url=base_url))
            return (
                constructor(api_key=api_key, base_url=base_url)
                if base_url is not None
                else constructor(api_key=api_key)
            )

        return _infer(recipe, route=route, provider_factory=provider_factory)

    def _required_subscription_source[SourceT: SubscriptionSource](
        self,
        authentication_kind: str,
        source_type: type[SourceT],
    ) -> SourceT:
        source = self._subscription_sources.get(authentication_kind)
        if source is None:
            raise ModelResolutionError(
                "The selected subscription account integration is unavailable.",
                code="model_account_integration_missing",
                details={"authentication_kind": authentication_kind},
            )
        if not isinstance(source, source_type):
            raise ModelResolutionError(
                "The selected subscription account integration is incompatible.",
                code="model_account_integration_invalid",
                details={"authentication_kind": authentication_kind},
            )
        return source


def _model_name(recipe: ResolvedModelRecipe) -> str:
    _provider, separator, model_name = recipe.route.partition(":")
    return model_name if separator else recipe.route


def _infer(
    recipe: ResolvedModelRecipe,
    *,
    route: str,
    provider_factory: Callable[[str], Provider[Any]],
) -> Model:
    try:
        return infer_model(route, provider_factory=provider_factory)
    except ModelResolutionError:
        raise
    except Exception as exc:
        raise ModelResolutionError(
            "The resolved Harness UI Model recipe could not be reconstructed.",
            code="model_reconstruction_failed",
            details={"model_id": recipe.model_id},
        ) from exc


def model_recipe_id(recipe: ResolvedModelRecipe) -> str:
    """Return a concise deterministic logical ID for one complete credential-free recipe."""

    encoded = json.dumps(
        recipe.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"a13n-harness-ui:model-{hashlib.sha256(encoded).hexdigest()[:24]}"


__all__ = [
    "CodexSubscriptionSource",
    "GrokSubscriptionSource",
    "HarnessUiModelResolver",
    "SubscriptionSource",
    "model_recipe_id",
]
