"""Harness Web definitions, safe account descriptions, and lazy Run-local adapters."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any, Literal

from a13n_harness.capabilities.web import (
    WebScrapeBackendBinding,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchBackendBinding,
    WebSearchRequest,
    WebSearchResponse,
)
from a13n_harness.providers import ProviderCatalog
from a13n_harness.providers.authentication import Authentication
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.web import WebProviderDefinition
from a13n_harness.providers.web.builtins import built_in_web_providers
from a13n_harness.providers.web.options import ScrapeOptions, SearchOptions
from a13n_harness.providers.web.transport import WebProviderTransport
from a13n_harness.toolsets.web import WebConfiguration, WebPolicy, WebProviderError
from anyio import Lock
from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.configuration.models import EnvironmentVariableSource, WebProviderResource
from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.model_accounts.api_keys import ApiKeyStore


def web_provider_catalog() -> ProviderCatalog[WebProviderDefinition]:
    return ProviderCatalog(built_in_web_providers())


class WebProviderType(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    display_name: str
    configuration_schema: dict[str, Any]
    credential_schema: dict[str, Any] | None
    authentication: Authentication
    setup_url: str | None
    setup_label: str | None
    operations: tuple[Literal["search", "scrape"], ...]
    supports_restricted_scrape: bool


class WebProviderCatalogView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    types: tuple[WebProviderType, ...]
    accounts: tuple[WebProviderResource, ...] = ()


def describe_web_providers(accounts: Iterable[WebProviderResource] = ()) -> WebProviderCatalogView:
    return WebProviderCatalogView(
        types=tuple(
            WebProviderType(
                type=item.type,
                display_name=item.display_name,
                configuration_schema=item.configuration_model.model_json_schema(by_alias=True),
                credential_schema=None
                if item.credential_model is None
                else item.credential_model.model_json_schema(by_alias=True),
                authentication=item.authentication,
                setup_url=item.setup_url,
                setup_label=item.setup_label,
                operations=("search", "scrape")
                if item.supports_search and item.supports_scrape
                else ("search",)
                if item.supports_search
                else ("scrape",),
                supports_restricted_scrape=item.supports_restricted_scrape,
            )
            for item in web_provider_catalog().values()
        ),
        accounts=tuple(accounts),
    )


def validate_web_account(account: WebProviderResource) -> None:
    """Validate recipes without opening providers or resolving any credential source."""
    try:
        definition = web_provider_catalog().require(account.type)
        configuration = definition.configuration_model.model_validate(account.configuration)
        definition.authentication.validate_presence(configuration, account.credential is not None)
        if account.credential is not None:
            model = definition.credential_model
            if model is None:
                raise ValueError("credential not accepted")
            schema = model.model_json_schema(by_alias=True)
            fields = schema.get("properties", {})
            if not set(schema.get("required", ())) <= account.credential.keys():
                raise ValueError("required credential fields missing")
            for name in account.credential:
                field = fields.get(name, {})
                variants = field.get("anyOf", [field])
                if not any(item.get("type") == "string" for item in variants):
                    raise ValueError("credential fields must be top-level strings")
    except (ValueError, TypeError):
        raise CompositionError(
            "Web provider account configuration or credential sources are invalid.",
            code="web_provider_configuration_invalid",
            details={"web_provider_id": account.id},
        ) from None


def selected_web_accounts(
    configuration: WebConfiguration, accounts: Iterable[WebProviderResource]
) -> tuple[tuple[WebProviderResource, bool, bool], ...]:
    """Bind only explicitly named accounts; priority is an order, not an allowlist."""

    def references(operation: Any) -> set[str]:
        if operation.mode in {"off", "native"}:
            return set()
        return {operation.backend} if operation.backend else set(operation.backend_priority)

    search = references(configuration.search)
    scrape = references(configuration.scrape)
    selected = []
    catalog = web_provider_catalog()
    available = {item.id: item for item in accounts}
    for account_id in search | scrape:
        if account_id != "default" and account_id not in available:
            raise CompositionError("A selected Web account is unavailable.", code="web_provider_missing")
    for account in available.values():
        if account.id not in search | scrape:
            continue
        definition = catalog.require(account.type)
        wants_search, wants_scrape = account.id in search, account.id in scrape
        if (wants_search and not definition.supports_search) or (wants_scrape and not definition.supports_scrape):
            raise CompositionError(
                "The Web account does not support the selected operation.", code="web_provider_operation_unavailable"
            )
        selected.append((account, wants_search, wants_scrape))
    return tuple(selected)


_UNRESOLVED = object()


class _RunWebAccount:
    """One account's successful credential resolution, shared by concurrent operations."""

    def __init__(
        self,
        recipe: WebProviderResource,
        keys: ApiKeyStore | None,
        transport: WebProviderTransport,
        configuration: WebConfiguration,
    ) -> None:
        self.recipe = recipe
        self.definition = web_provider_catalog().require(recipe.type)
        self.supports_domain_restrictions = self.definition.supports_restricted_scrape
        self._keys = keys
        self._transport = transport
        self._configuration = configuration
        self._credential: object = _UNRESOLVED
        self._lock = Lock()

    async def _resolve(self) -> object:
        async with self._lock:
            if self._credential is _UNRESOLVED:
                try:
                    values = None
                    if self.recipe.credential is not None:
                        values = {}
                        for name, source in self.recipe.credential.items():
                            value = (
                                os.environ.get(source.env)
                                if isinstance(source, EnvironmentVariableSource)
                                else (await self._keys.load(source.credential_ref) if self._keys is not None else None)
                            )
                            if value is None:
                                raise ValueError("credential source unavailable")
                            values[name] = value
                    configuration = self.definition.configuration_model.model_validate(self.recipe.configuration)
                    credential = self.definition.parse_credential(configuration, values)
                except Exception:
                    # Provider validation and store failures may contain resolved inputs.
                    raise WebProviderError("web_credential_unavailable") from None
                self._credential = credential
            return self._credential

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        credential = await self._resolve()
        async with self.definition.open(
            self.recipe.configuration,
            credential,
            options=SearchOptions(
                max_results=request.limit,
                allow_domains=self._configuration.search.allow_domains,
                deny_domains=self._configuration.search.deny_domains,
            ),
            transport=self._transport,
        ) as provider:
            return await provider.search(request)

    async def scrape(self, request: WebScrapeRequest, *, policy: WebPolicy) -> WebScrapeResult:
        credential = await self._resolve()
        async with self.definition.open(
            self.recipe.configuration,
            credential,
            options=ScrapeOptions(
                max_content_bytes=request.max_content_bytes,
                allow_domains=self._configuration.scrape.allow_domains,
                deny_domains=self._configuration.scrape.deny_domains,
            ),
            transport=self._transport,
        ) as provider:
            return await provider.scrape(request, policy=policy)


def account_backend_bindings(
    configuration: WebConfiguration,
    accounts: Iterable[WebProviderResource],
    *,
    keys: ApiKeyStore | None,
    endpoint_policy: EndpointPolicy,
) -> tuple[tuple[WebSearchBackendBinding, ...], tuple[WebScrapeBackendBinding, ...]]:
    search, scrape = [], []
    for account, wants_search, wants_scrape in selected_web_accounts(configuration, accounts):
        runtime = _RunWebAccount(account, keys, WebProviderTransport(endpoint_policy=endpoint_policy), configuration)
        if wants_search:
            search.append(WebSearchBackendBinding(account.id, runtime))
        if wants_scrape:
            scrape.append(WebScrapeBackendBinding(account.id, runtime, runtime.supports_domain_restrictions))
    return tuple(search), tuple(scrape)
