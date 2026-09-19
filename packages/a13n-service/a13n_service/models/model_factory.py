"""Compose current Provider integration with a finite calling-API binding."""

from __future__ import annotations

from typing import Any

import httpx2
from a13n_harness.errors import ModelResolutionError
from a13n_harness.http import EndpointValidator
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from a13n_harness.providers.model.definition import ModelProviderDefinition
from a13n_harness.providers.model.types import ModelConnection
from pydantic_ai.models import Model as PydanticModel

from .domain import ModelExecutionSnapshot
from .profiles import catalog_profile
from .providers import validate_model_api
from .service_common import ModelError


class NativeModelFactory:
    """Build the exact native Model selected by Provider type and calling API."""

    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        registry: ProviderCatalog[ModelProviderDefinition],
        endpoint_policy: EndpointValidator,
    ) -> None:
        self._http_client = http_client
        self._registry = registry
        self._endpoint_policy = endpoint_policy

    async def build(self, snapshot: ModelExecutionSnapshot, provider: ModelConnection) -> PydanticModel[Any]:
        try:
            integration = self._registry.require(provider.type)
        except ValueError as error:
            raise ModelResolutionError(
                "The Model Provider is unavailable.",
                code="model_provider_unavailable",
            ) from error
        try:
            validate_model_api(self._registry.require(provider.type), snapshot.model_api)
            return await integration.build(
                snapshot.upstream_model,
                configuration=provider.configuration.model_dump(mode="json", by_alias=True),
                credential=provider.credential,
                model_api=snapshot.model_api,
                http_client=self._http_client,
                extra_headers=provider.extra_headers,
                endpoint_policy=self._endpoint_policy,
                profile_resolver=lambda native: catalog_profile(
                    snapshot.catalog_ref,
                    provider_type=provider.type,
                    model_api=snapshot.model_api,
                    native_provider=native,
                ),
            )
        except ModelError as error:
            raise ModelResolutionError(
                "The accepted Model API is unavailable.",
                code="model_api_unavailable",
                details={"model_api": snapshot.model_api},
            ) from error
        except EndpointPolicyError as error:
            raise ModelResolutionError(
                "The current Model Provider endpoint is unavailable.", code="model_provider_unavailable"
            ) from error
