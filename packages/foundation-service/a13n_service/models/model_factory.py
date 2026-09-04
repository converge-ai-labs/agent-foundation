"""Compose current Provider integration with a finite calling-API binding."""

from __future__ import annotations

from typing import Any

import httpx2
from a13n_harness.errors import ModelResolutionError
from pydantic_ai.models import Model as PydanticModel

from .domain import ModelExecutionSnapshot
from .model_apis import BUILT_IN_MODEL_APIS
from .provider_adapters.types import RuntimeProvider
from .providers import ProviderRegistry
from .service_common import ModelError


class NativeModelFactory:
    """Build the exact native Model selected by Provider type and calling API."""

    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        registry: ProviderRegistry,
    ) -> None:
        self._http_client = http_client
        self._registry = registry

    def build(self, snapshot: ModelExecutionSnapshot, provider: RuntimeProvider) -> PydanticModel[Any]:
        try:
            integration = self._registry.integration(provider.type)
        except ValueError as error:
            raise ModelResolutionError(
                "The Model Provider is unavailable.",
                code="model_provider_unavailable",
            ) from error
        try:
            self._registry.validate_model_api(provider.type, snapshot.model_api)
            binding = BUILT_IN_MODEL_APIS[snapshot.model_api]
        except (KeyError, ValueError, ModelError) as error:
            raise ModelResolutionError(
                "The accepted Model API is unavailable.",
                code="model_api_unavailable",
                details={"model_api": snapshot.model_api},
            ) from error
        native_provider = integration.build_provider(
            provider,
            self._http_client,
            binding.pydantic_provider_name,
        )
        return binding.build(snapshot.upstream_model, native_provider)
