"""Run-fresh native Model construction with Host-owned credential sources."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from a13n_harness import AgentContext
from a13n_harness.errors import ModelResolutionError
from a13n_harness.model_affinity import derive_model_affinity_id
from a13n_harness.models.inference import RequestHeadersModel
from a13n_harness.providers.model.credentials import ApiKeyCredential
from a13n_harness.providers.model.oauth import GrokCredentials, GrokCredentialSource
from a13n_harness.providers.model.routes import build_api_key_model
from pydantic_ai.models import Model, ModelResolutionContext

from a13n_harness_ui.configuration import (
    ApiKeyAuthentication,
    CodexSubscriptionAuthentication,
    GrokSubscriptionAuthentication,
)
from a13n_harness_ui.model_accounts.api_keys import ApiKeyStore

if TYPE_CHECKING:
    from pydantic_ai.providers.openai_codex import OpenAICodexCredentialSource

    from a13n_harness_ui.composition.models import ResolvedModelRecipe


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
        return await self.resolve(model_id, thread_id=context.deps.thread_id)

    async def resolve(self, model_id: str, *, thread_id: str) -> Model:
        """Construct a captured Model for either primary or auxiliary inference."""
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
                return RequestHeadersModel(model, common_headers={header: derive_model_affinity_id(thread_id)})
            return model
        if isinstance(authentication, CodexSubscriptionAuthentication):
            from a13n_harness.models.codex import CodexRequestModel

            from a13n_harness_ui.model_accounts.codex import BoundCodexCredentialSource

            source = self._required_subscription_source(
                "codex_subscription",
                CodexSubscriptionSource,
            )
            return CodexRequestModel(
                _model_name(recipe),
                credential_source=BoundCodexCredentialSource(source.source),
                thread_id=thread_id,
            )
        if isinstance(authentication, GrokSubscriptionAuthentication):
            from a13n_harness.providers.model.oauth import build_grok_model

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
        base_url = recipe.model_configuration.get("base_url")
        if base_url is not None and not isinstance(base_url, str):
            raise ModelResolutionError("Invalid Model base URL.", code="model_reconstruction_failed")
        try:
            return await build_api_key_model(
                recipe.route, ApiKeyCredential.model_validate({"api_key": api_key}), base_url=base_url
            )
        except Exception as exc:
            raise ModelResolutionError(
                "The resolved Harness UI Model recipe could not be reconstructed.",
                code="model_reconstruction_failed",
                details={"model_id": recipe.model_id},
            ) from exc

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


def model_recipe_id(recipe: ResolvedModelRecipe) -> str:
    """Return a concise deterministic logical ID for one complete credential-free recipe."""

    payload = recipe.model_dump(mode="json")
    # Sets can iterate differently after a deep copy or process restart.
    if recipe.model_characteristics is not None:
        payload["model_characteristics"]["capabilities"] = sorted(recipe.model_characteristics.capabilities)
    encoded = json.dumps(
        payload,
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
