"""Trusted, distribution-owned Model Provider metadata and domain policy."""

from __future__ import annotations

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.model.apis import MODEL_APIS
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_harness.providers.model.definition import ModelProviderDefinition

from a13n_service.application_errors import ErrorCategory
from a13n_service.provider_metadata import ProviderMetadata, provider_metadata_core

from .service_common import ModelError
from .settings import settings_schema


class ModelProviderMetadata(ProviderMetadata):
    supports_connection_probe: bool
    supported_model_apis: tuple[str, ...]
    catalog_providers: tuple[str, ...] = ()
    default_model_api: str
    model_api_labels: dict[str, str]
    settings_schemas: dict[str, dict[str, object]]

    @classmethod
    def describe(cls, definition: ModelProviderDefinition) -> ModelProviderMetadata:
        return cls(
            **provider_metadata_core(definition),
            supports_connection_probe=definition.supports_connection_probe,
            supported_model_apis=definition.supported_model_apis,
            catalog_providers=definition.catalog_providers,
            default_model_api=definition.supported_model_apis[0],
            model_api_labels={
                model_api: MODEL_APIS[model_api].display_name for model_api in definition.supported_model_apis
            },
            settings_schemas={model_api: settings_schema(model_api) for model_api in definition.supported_model_apis},
        )


def validate_model_api(definition: ModelProviderDefinition, model_api: str) -> None:
    if model_api not in definition.supported_model_apis:
        raise ModelError(
            "invalid_model_api",
            "The Model API is not supported by this Provider.",
            category=ErrorCategory.invalid_request,
        )


def built_in_model_provider_catalog() -> ProviderCatalog[ModelProviderDefinition]:
    return ProviderCatalog(BUILT_IN_MODEL_PROVIDERS)
