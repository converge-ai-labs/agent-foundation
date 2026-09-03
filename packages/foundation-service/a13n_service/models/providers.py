"""Trusted, distribution-owned model Provider type registry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

from .domain import BoundedName, ModelApiConfig, UpstreamModel
from .provider_adapters.registry import BUILT_IN_PROVIDER_TYPES
from .provider_adapters.types import CredentialFormat, ProviderType, ValidatedProviderConfig


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
    """Immutable allowlist of trusted Provider types and native Model API bindings."""

    def __init__(self, provider_types: Iterable[ProviderType]) -> None:
        indexed: dict[str, ProviderType] = {}
        for provider_type in provider_types:
            if provider_type.key in indexed:
                raise ValueError(f"duplicate provider type {provider_type.key!r}")
            indexed[provider_type.key] = provider_type
        self._provider_types = MappingProxyType(indexed)

    def definitions(self) -> tuple[ModelProviderTypeDefinition, ...]:
        return tuple(_definition(item) for item in self._provider_types.values())

    def definition(self, provider_type: str) -> ModelProviderTypeDefinition:
        return _definition(self._require(provider_type))

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

    def _require(self, provider_type: str) -> ProviderType:
        try:
            return self._provider_types[provider_type]
        except KeyError as error:
            raise ValueError(f"unknown provider type {provider_type!r}") from error


def built_in_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(BUILT_IN_PROVIDER_TYPES)


def _definition(provider_type: ProviderType) -> ModelProviderTypeDefinition:
    credential_schema: dict[str, object] = {"type": "null"}
    if provider_type.credential_format is not None:
        credential_schema = {
            "type": "string",
            "format": "password",
            "writeOnly": True,
            "x-a13n-credential-format": provider_type.credential_format.value,
        }
    return ModelProviderTypeDefinition(
        key=provider_type.key,
        display_name=provider_type.display_name,
        config_schema=provider_type.config_model.model_json_schema(),
        credential_schema=credential_schema,
        supported_model_apis=provider_type.supported_model_apis,
        supports_model_discovery=provider_type.supports_model_discovery,
    )
