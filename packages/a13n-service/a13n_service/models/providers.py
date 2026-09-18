"""Trusted, distribution-owned model Provider type registry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType

from a13n_harness.providers.authentication import Authentication
from a13n_harness.providers.model.apis import MODEL_APIS
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_harness.providers.model.definition import ModelProviderDefinition as Definition
from a13n_harness.providers.model.types import ValidatedProviderConfiguration
from pydantic import BaseModel, ConfigDict

from a13n_service.application_errors import ErrorCategory

from .profiles import PROVIDER_CATALOGS
from .service_common import ModelError
from .settings import settings_schema


class ModelProviderDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    display_name: str
    configuration_schema: dict[str, object]
    credential_schema: dict[str, object]
    authentication: Authentication
    setup_url: str | None = None
    setup_label: str | None = None
    supports_connection_probe: bool
    supported_model_apis: tuple[str, ...]
    catalog_providers: tuple[str, ...] = ()
    default_model_api: str
    model_api_labels: dict[str, str]
    settings_schemas: dict[str, dict[str, object]]


class ModelProviderDefinitionCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ModelProviderDefinition, ...]
    next_cursor: None = None


class ProviderRegistry:
    """Immutable allowlist of trusted Provider integrations."""

    def __init__(self, integrations: Iterable[Definition]) -> None:
        indexed: dict[str, Definition] = {}
        for integration in integrations:
            if integration.type in indexed:
                raise ValueError(f"duplicate provider type {integration.type!r}")
            unknown_apis = sorted(set(integration.supported_model_apis) - MODEL_APIS.keys())
            if unknown_apis:
                raise ValueError(f"unknown model APIs for {integration.type!r}: {', '.join(unknown_apis)}")
            if not integration.supported_model_apis:
                raise ValueError("the default Model API must be a supported binding")
            indexed[integration.type] = integration
        self._integrations = MappingProxyType(indexed)

    def definitions(self) -> tuple[ModelProviderDefinition, ...]:
        return tuple(_definition(item) for item in self._integrations.values())

    def definition(self, provider_type: str) -> ModelProviderDefinition:
        return _definition(self._require(provider_type))

    def integration(self, provider_type: str) -> Definition:
        return self._require(provider_type)

    def validate_provider(
        self,
        provider_type: str,
        configuration: Mapping[str, object],
        *,
        credential_configured: bool,
        header_names: Sequence[str] = (),
    ) -> ValidatedProviderConfiguration:
        return self._require(provider_type).validate_configuration(
            configuration, credential_configured=credential_configured, header_names=header_names
        )

    def validate_model_api(self, provider_type: str, model_api: str) -> None:
        allowed = self._require(provider_type).supported_model_apis
        if model_api not in allowed:
            raise ModelError(
                "invalid_model_api",
                "The Model API is not supported by this Provider.",
                category=ErrorCategory.invalid_request,
            )

    def with_validated_endpoint(
        self,
        provider_type: str,
        validated: ValidatedProviderConfiguration,
        endpoint: str,
    ) -> ValidatedProviderConfiguration:
        return self._require(provider_type).with_validated_endpoint(validated, endpoint)

    def _require(self, provider_type: str) -> Definition:
        try:
            return self._integrations[provider_type]
        except KeyError as error:
            raise ValueError(f"unknown provider type {provider_type!r}") from error


def built_in_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(BUILT_IN_MODEL_PROVIDERS)


def _definition(integration: Definition) -> ModelProviderDefinition:
    credential_schema = integration.credential_model.model_json_schema()
    credential_schema["writeOnly"] = True
    return ModelProviderDefinition(
        type=integration.type,
        catalog_providers={"google_vertex": ("google-vertex",), "azure_openai": ("azure",)}.get(
            integration.type, PROVIDER_CATALOGS.get(integration.type, ())
        ),
        display_name=integration.display_name,
        setup_url=integration.setup_url,
        setup_label=integration.setup_label,
        supports_connection_probe=integration.supports_connection_probe,
        configuration_schema=integration.configuration_model.model_json_schema(),
        credential_schema=credential_schema,
        authentication=integration.authentication,
        supported_model_apis=integration.supported_model_apis,
        default_model_api=integration.supported_model_apis[0],
        model_api_labels={
            model_api: MODEL_APIS[model_api].display_name for model_api in integration.supported_model_apis
        },
        settings_schemas={model_api: settings_schema(model_api) for model_api in integration.supported_model_apis},
    )
