"""Trusted, distribution-owned model Provider type registry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

from .domain import ModelApi, UpstreamModel
from .model_apis import BUILT_IN_MODEL_APIS
from .provider_adapters.base import ProviderIntegration
from .provider_adapters.registry import BUILT_IN_PROVIDER_INTEGRATIONS
from .provider_adapters.types import CredentialFormat, ValidatedProviderConfiguration
from .service_common import ModelError


class DescribeModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upstream_model: UpstreamModel
    model_api: ModelApi | None = None


class ModelProviderDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    display_name: str
    configuration_schema: dict[str, object]
    credential_schema: dict[str, object]
    supported_model_apis: tuple[str, ...]
    default_model_api: str
    supports_model_discovery: bool


class ModelProviderDefinitionCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ModelProviderDefinition, ...]
    next_cursor: None = None


class ProviderRegistry:
    """Immutable allowlist of trusted Provider integrations."""

    def __init__(self, integrations: Iterable[ProviderIntegration]) -> None:
        indexed: dict[str, ProviderIntegration] = {}
        for integration in integrations:
            if integration.type in indexed:
                raise ValueError(f"duplicate provider type {integration.type!r}")
            unknown_apis = sorted(set(integration.supported_model_apis) - BUILT_IN_MODEL_APIS.keys())
            if unknown_apis:
                raise ValueError(f"unknown model APIs for {integration.type!r}: {', '.join(unknown_apis)}")
            if not integration.supported_model_apis or (
                integration.default_model_api is not None
                and integration.default_model_api not in integration.supported_model_apis
            ):
                raise ValueError("the default Model API must be a supported binding")
            indexed[integration.type] = integration
        self._integrations = MappingProxyType(indexed)

    def definitions(self) -> tuple[ModelProviderDefinition, ...]:
        return tuple(_definition(item) for item in self._integrations.values())

    def definition(self, provider_type: str) -> ModelProviderDefinition:
        return _definition(self._require(provider_type))

    def integration(self, provider_type: str) -> ProviderIntegration:
        return self._require(provider_type)

    def validate_provider(
        self, provider_type: str, configuration: Mapping[str, object], *, credential_configured: bool
    ) -> ValidatedProviderConfiguration:
        return self._require(provider_type).validate_configuration(
            configuration, credential_configured=credential_configured
        )

    def validate_model_api(self, provider_type: str, model_api: str) -> None:
        allowed = self._require(provider_type).supported_model_apis
        if model_api not in allowed:
            raise ModelError("invalid_model_api", "The Model API is not supported by this Provider.", status_code=400)

    def credential_format(self, provider_type: str) -> CredentialFormat | None:
        return self._require(provider_type).credential_format

    def with_validated_endpoint(
        self,
        provider_type: str,
        validated: ValidatedProviderConfiguration,
        endpoint: str,
    ) -> ValidatedProviderConfiguration:
        return self._require(provider_type).with_validated_endpoint(validated, endpoint)

    def _require(self, provider_type: str) -> ProviderIntegration:
        try:
            return self._integrations[provider_type]
        except KeyError as error:
            raise ValueError(f"unknown provider type {provider_type!r}") from error


def built_in_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(BUILT_IN_PROVIDER_INTEGRATIONS)


def _definition(integration: ProviderIntegration) -> ModelProviderDefinition:
    credential_schema: dict[str, object] = {"type": "null"}
    if integration.credential_format is not None:
        credential_schema = {
            "type": "string",
            "format": "password",
            "writeOnly": True,
            "x-a13n-credential-format": integration.credential_format.value,
        }
    return ModelProviderDefinition(
        type=integration.type,
        display_name=integration.display_name,
        configuration_schema=integration.configuration_model.model_json_schema(),
        credential_schema=credential_schema,
        supported_model_apis=integration.supported_model_apis,
        default_model_api=integration.default_model_api or integration.supported_model_apis[0],
        supports_model_discovery=integration.model_discovery is not None,
    )
