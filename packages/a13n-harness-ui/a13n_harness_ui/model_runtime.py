"""Run-fresh native Model construction with Host-owned credential sources."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Self

from a13n_harness import AgentContext
from a13n_harness.configuration import RunConfiguration
from a13n_harness.errors import ModelResolutionError
from a13n_harness.model_affinity import derive_model_affinity_id
from a13n_harness.models.inference import RequestHeadersModel
from a13n_harness.pricing import PricingCatalog, get_current_pricing_catalog
from a13n_harness.providers.model.credentials import ApiKeyCredential
from a13n_harness.providers.model.oauth import (
    CopilotCredentialSource,
    CopilotRefresh,
    GrokCredentials,
    GrokCredentialSource,
)
from a13n_harness.providers.model.routes import build_api_key_model
from a13n_harness.token_pricing import TokenPricingCapability
from anyio import CancelScope
from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.models.wrapper import WrapperModel

from a13n_harness_ui.configuration import (
    ApiKeyAuthentication,
    ChatGPTSubscriptionAuthentication,
    CodexSubscriptionAuthentication,
    CopilotSubscriptionAuthentication,
    GrokSubscriptionAuthentication,
    PluginSubscriptionAuthentication,
)
from a13n_harness_ui.model_accounts.api_keys import ApiKeyStore
from a13n_harness_ui.model_accounts.managed import SubscriptionAccount, SubscriptionAccounts
from a13n_harness_ui.model_accounts.routing import AccountSelectionEvent, SubscriptionGroupModel
from a13n_harness_ui.model_accounts.usage_cache import SubscriptionUsage
from a13n_harness_ui.model_presets import configured_price_entry
from a13n_harness_ui.model_providers import BuildModelContext
from a13n_harness_ui.model_providers.catalog import Registration
from a13n_harness_ui.model_providers.credentials import PluginAccountStore, PluginCredentialSource
from a13n_harness_ui.model_providers.transport import provider_http_client

if TYPE_CHECKING:
    from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentialSource
    from pydantic_ai.providers.openai_codex import OpenAICodexCredentialSource

    from a13n_harness_ui.composition.models import ResolvedModelRecipe


@dataclass(frozen=True, slots=True)
class ChatGPTSubscriptionSource:
    create_source: Callable[[], OpenAIChatGPTCredentialSource]


@dataclass(frozen=True, slots=True)
class CodexSubscriptionSource:
    """Host wiring for the official Codex provider credential source."""

    source: OpenAICodexCredentialSource


@dataclass(frozen=True, slots=True)
class GrokSubscriptionSource:
    """Host wiring for one Grok credential source and optional refresh override."""

    source: GrokCredentialSource
    refresh: Callable[[GrokCredentials], Awaitable[GrokCredentials]] | None = None


@dataclass(frozen=True, slots=True)
class CopilotSubscriptionSource:
    """Host wiring for request-fresh Copilot credentials."""

    source: CopilotCredentialSource
    refresh: CopilotRefresh | None = None


@dataclass(frozen=True, slots=True)
class ManagedSubscriptionSource:
    accounts: SubscriptionAccounts
    usage: SubscriptionUsage
    grok_refresh: Callable[[GrokCredentials], Awaitable[GrokCredentials]] | None = None
    selected: Callable[[str, str, AccountSelectionEvent], None] | None = None


@dataclass(frozen=True, slots=True)
class PluginSubscriptionSource:
    managed: ManagedSubscriptionSource
    registration: Registration


class _PluginModel(WrapperModel):
    def __init__(
        self,
        registration: Registration,
        recipe: ResolvedModelRecipe,
        credentials: PluginCredentialSource,
        configuration: RunConfiguration,
    ) -> None:
        self.registration = registration
        self.recipe = recipe
        self.credentials = credentials
        self.configuration = configuration
        self.client = provider_http_client(registration.declaration, configuration)
        super().__init__(self._build())
        self._resources = AsyncExitStack()

    def _build(self) -> Model:
        try:
            model = self.registration.plugin.build_model(
                BuildModelContext(
                    self.client,
                    self.credentials.bearer_auth,
                    _model_name(self.recipe),
                    self.registration.configuration(self.recipe.model_configuration),
                )
            )
            if not isinstance(model, Model):
                raise TypeError("Expected a native Model")
            return model
        except Exception:
            raise ModelResolutionError(
                "Model Provider construction failed.", code="model_reconstruction_failed"
            ) from None

    async def __aenter__(self) -> Self:
        async with AsyncExitStack() as resources:
            if self.client.is_closed:
                self.client = provider_http_client(self.registration.declaration, self.configuration)
                await resources.enter_async_context(self.client)
                self.wrapped = self._build()
            else:
                await resources.enter_async_context(self.client)
            await resources.enter_async_context(self.wrapped)
            self._resources = resources.pop_all()
        return self

    async def __aexit__(self, *exc: object) -> None:
        with CancelScope(shield=True):
            await self._resources.aclose()


type SubscriptionSource = (
    ChatGPTSubscriptionSource
    | CodexSubscriptionSource
    | GrokSubscriptionSource
    | CopilotSubscriptionSource
    | ManagedSubscriptionSource
    | PluginSubscriptionSource
)


class HarnessUiModelResolver:
    """Resolve logical recipes with provider-owned OAuth lifecycles."""

    def __init__(
        self,
        recipes: Mapping[str, ResolvedModelRecipe],
        *,
        subscription_sources: Mapping[str, SubscriptionSource] | None = None,
        api_keys: ApiKeyStore | None = None,
        configuration: RunConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or RunConfiguration()
        self._api_keys = api_keys
        self._recipes = MappingProxyType({key: value.model_copy(deep=True) for key, value in recipes.items()})
        self._subscription_sources = MappingProxyType(dict(subscription_sources or {}))

    @property
    def configured_models(self) -> Mapping[str, ResolvedModelRecipe]:
        return self._recipes

    def pricing(self, catalog: PricingCatalog | None) -> TokenPricingCapability:
        if catalog is None:
            catalog = get_current_pricing_catalog()
        entries = {}
        for key, recipe in self._recipes.items():
            identity = recipe.base_model or recipe.route
            entry = configured_price_entry(catalog, identity)
            # A Developer API tariff is not an explicit Vertex price.
            if identity.startswith("google-vertex:") and entry is not None and entry.provider == "google":
                continue
            entries[key] = entry
        return TokenPricingCapability(entries)

    def fresh(self) -> HarnessUiModelResolver:
        return HarnessUiModelResolver(
            self._recipes,
            subscription_sources=self._subscription_sources,
            api_keys=self._api_keys,
            configuration=self.configuration,
        )

    async def __call__(
        self,
        context: ModelResolutionContext[AgentContext],
        model_id: str,
    ) -> Model:
        return await self.resolve(model_id, thread_id=context.deps.thread_id)

    async def resolve(
        self, model_id: str, *, thread_id: str, prediction_account: str | None = None, prediction: bool = False
    ) -> Model:
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
            model = (
                await self._api_key_model(recipe, authentication, retry_http=False)
                if prediction
                else await self._api_key_model(recipe, authentication)
            )
            header = recipe.model_configuration.get("session_affinity_header")
            if isinstance(header, str):
                return RequestHeadersModel(model, common_headers={header: derive_model_affinity_id(thread_id)})
            return model
        if isinstance(authentication, PluginSubscriptionAuthentication):
            plugin_source = self._subscription_sources.get(f"plugin_subscription:{authentication.provider}")
            if (
                not isinstance(plugin_source, PluginSubscriptionSource)
                or recipe.model_provider != plugin_source.registration.provenance()
            ):
                raise ModelResolutionError(
                    "The captured Model Provider is unavailable or changed.", code="model_provider_unavailable"
                )
            return await self._managed_model(
                recipe,
                plugin_source.managed,
                thread_id=thread_id,
                prediction=prediction,
                prediction_account=prediction_account,
                registration=plugin_source.registration,
            )
        if self.configuration.allowed_hosts is not None:
            raise ModelResolutionError(
                "This subscription Model transport cannot enforce Run allowed hosts.",
                code="model_configuration_unsupported",
            )
        managed = self._subscription_sources.get(authentication.kind)
        if isinstance(managed, ManagedSubscriptionSource):
            return await self._managed_model(
                recipe, managed, thread_id=thread_id, prediction=prediction, prediction_account=prediction_account
            )
        if isinstance(authentication, ChatGPTSubscriptionAuthentication):
            from pydantic_ai.models.openai import OpenAIResponsesModel
            from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTProvider

            source = self._required_subscription_source("chatgpt_subscription", ChatGPTSubscriptionSource)
            provider = OpenAIChatGPTProvider(credential_source=source.create_source())
            provider.client.max_retries = 0
            return OpenAIResponsesModel(_model_name(recipe), provider=provider)
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
        if isinstance(authentication, CopilotSubscriptionAuthentication):
            from a13n_harness.providers.model.oauth import build_copilot_model

            source = self._required_subscription_source("copilot_subscription", CopilotSubscriptionSource)
            return build_copilot_model(_model_name(recipe), credential_source=source.source, refresh=source.refresh)
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

    async def _managed_model(
        self,
        recipe: ResolvedModelRecipe,
        source: ManagedSubscriptionSource,
        *,
        thread_id: str,
        prediction: bool,
        prediction_account: str | None,
        registration: Registration | None = None,
    ) -> Model:
        from a13n_harness.models.codex import CodexRequestModel
        from a13n_harness.providers.model.oauth import build_grok_model

        from a13n_harness_ui.configuration.models import SubscriptionGroupMember, SubscriptionGroupResource
        from a13n_harness_ui.model_accounts.codex import BoundCodexCredentialSource, CodexAccountStore
        from a13n_harness_ui.model_accounts.grok import GrokAccountStore

        provider = "codex" if isinstance(recipe.authentication, CodexSubscriptionAuthentication) else "grok"
        if registration is not None:
            provider = registration.declaration.provider_key
        # Historical recipes have no captured group; their former single identity remains native.
        group = recipe.subscription_group or SubscriptionGroupResource(
            schema_version="1",
            kind="subscription_group",
            id=f"{provider}-default",
            name=f"{provider.title()} default",
            provider=provider,
            accounts=(SubscriptionGroupMember(account=f"acct-{provider}-default"),),
        )

        def factory(account: SubscriptionAccount) -> Model:
            store = source.accounts.store(account)
            if isinstance(store, PluginAccountStore) and registration is not None:
                credentials = PluginCredentialSource(
                    store, registration, self.configuration, expected_generation=account.login_generation
                )
                return _PluginModel(registration, recipe, credentials, self.configuration)
            if isinstance(store, CodexAccountStore):
                return CodexRequestModel(
                    _model_name(recipe),
                    credential_source=BoundCodexCredentialSource(store, account_id=account.upstream_account_id),
                    thread_id=thread_id,
                    response_headers=lambda headers: source.usage.observe_headers(account, headers),
                )
            assert isinstance(store, GrokAccountStore)
            return build_grok_model(_model_name(recipe), credential_source=store, refresh=source.grok_refresh)

        if prediction:
            if prediction_account is None or prediction_account not in {member.account for member in group.accounts}:
                raise ModelResolutionError(
                    "The previous subscription account is unavailable.", code="subscription_group_unavailable"
                )
            account = await source.accounts.get(prediction_account, available=True)
            if (
                account.provider != group.provider
                or await source.usage.tier(account, _model_name(recipe), allow_credits=group.allow_credits) is None
            ):
                raise ModelResolutionError(
                    "The previous subscription account is unavailable.", code="subscription_group_unavailable"
                )
            return factory(account)

        def selected(event: AccountSelectionEvent) -> None:
            if source.selected is not None:
                source.selected(thread_id, recipe.model_id, event)

        return SubscriptionGroupModel(group, source.accounts, source.usage, factory, selected=selected)

    async def _api_key_model(
        self, recipe: ResolvedModelRecipe, authentication: ApiKeyAuthentication, *, retry_http: bool = True
    ) -> Model:
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
            credential = ApiKeyCredential.model_validate({"api_key": api_key})
            if retry_http:
                return await build_api_key_model(
                    recipe.route, credential, base_url=base_url, configuration=self.configuration
                )
            return await build_api_key_model(
                recipe.route, credential, base_url=base_url, configuration=self.configuration, http_retry=None
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
    "ChatGPTSubscriptionSource",
    "CodexSubscriptionSource",
    "CopilotSubscriptionSource",
    "GrokSubscriptionSource",
    "HarnessUiModelResolver",
    "SubscriptionSource",
    "model_recipe_id",
]
