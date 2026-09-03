"""Trusted, distribution-owned model Provider type registry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

from .domain import BoundedName, ModelApiConfig, UpstreamModel
from .model_apis import BUILT_IN_MODEL_APIS
from .provider_adapters.base import ProviderIntegration
from .provider_adapters.registry import BUILT_IN_PROVIDER_INTEGRATIONS
from .provider_adapters.types import CredentialFormat, ValidatedProviderConfig


class DiscoveredModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    upstream_model: UpstreamModel
    display_name: BoundedName | None = None
    suggested_model_apis: tuple[ModelApiConfig, ...]


class DiscoveredModelCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[DiscoveredModel, ...]


class ModelProviderTypeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    display_name: str
    config_schema: dict[str, object]
    credential_schema: dict[str, object]
    supported_model_apis: tuple[str, ...]
    supports_model_discovery: bool


class ModelProviderTypeDefinitionCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ModelProviderTypeDefinition, ...]
    next_cursor: None = None


class ProviderRegistry:
    """Immutable allowlist of trusted Provider integrations."""

    def __init__(self, integrations: Iterable[ProviderIntegration]) -> None:
        indexed: dict[str, ProviderIntegration] = {}
        for integration in integrations:
            if integration.key in indexed:
                raise ValueError(f"duplicate provider type {integration.key!r}")
            unknown_apis = sorted(set(integration.supported_model_apis) - BUILT_IN_MODEL_APIS.keys())
            if unknown_apis:
                raise ValueError(f"unknown model APIs for {integration.key!r}: {', '.join(unknown_apis)}")
            indexed[integration.key] = integration
        self._integrations = MappingProxyType(indexed)

    def definitions(self) -> tuple[ModelProviderTypeDefinition, ...]:
        return tuple(_definition(item) for item in self._integrations.values())

    def definition(self, provider_type: str) -> ModelProviderTypeDefinition:
        return _definition(self._require(provider_type))

    def integration(self, provider_type: str) -> ProviderIntegration:
        return self._require(provider_type)

    def validate_provider(
        self, provider_type: str, config: Mapping[str, object], *, credential_configured: bool
    ) -> ValidatedProviderConfig:
        return self._require(provider_type).validate_config(config, credential_configured=credential_configured)

    def validate_model_apis(self, provider_type: str, model_apis: Sequence[ModelApiConfig]) -> None:
        allowed = set(self._require(provider_type).supported_model_apis)
        unsupported = sorted(item.api for item in model_apis if item.api not in allowed)
        if unsupported:
            raise ValueError(f"unsupported model APIs: {', '.join(unsupported)}")

    def validate_model_api(self, provider_type: str, model_api: str) -> None:
        allowed = self._require(provider_type).supported_model_apis
        if model_api not in allowed:
            raise ValueError(f"unsupported model API: {model_api}")

    def credential_format(self, provider_type: str) -> CredentialFormat | None:
        return self._require(provider_type).credential_format

    def with_validated_endpoint(
        self,
        provider_type: str,
        validated: ValidatedProviderConfig,
        endpoint: str,
    ) -> ValidatedProviderConfig:
        return self._require(provider_type).with_validated_endpoint(validated, endpoint)

    def _require(self, provider_type: str) -> ProviderIntegration:
        try:
            return self._integrations[provider_type]
        except KeyError as error:
            raise ValueError(f"unknown provider type {provider_type!r}") from error


def built_in_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(BUILT_IN_PROVIDER_INTEGRATIONS)


def _definition(integration: ProviderIntegration) -> ModelProviderTypeDefinition:
    credential_schema: dict[str, object] = {"type": "null"}
    if integration.credential_format is not None:
        credential_schema = {
            "type": "string",
            "format": "password",
            "writeOnly": True,
            "x-a13n-credential-format": integration.credential_format.value,
        }
    return ModelProviderTypeDefinition(
        key=integration.key,
        display_name=integration.display_name,
        config_schema=integration.config_model.model_json_schema(),
        credential_schema=credential_schema,
        supported_model_apis=integration.supported_model_apis,
        supports_model_discovery=integration.model_discovery is not None,
    )
