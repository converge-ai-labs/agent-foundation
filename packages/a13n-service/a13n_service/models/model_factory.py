"""Compose current Provider integration with a finite calling-API binding."""

from __future__ import annotations

from typing import Any

import httpx2
from a13n_harness.errors import ModelResolutionError
from anyio import CancelScope, to_thread
from pydantic_ai.models import Model as PydanticModel
from pydantic_ai.profiles import ModelProfileSpec

from a13n_service.endpoint_policy import EndpointPolicyError

from .base_models import profile_reference
from .domain import ModelExecutionSnapshot
from .model_apis import BUILT_IN_MODEL_APIS
from .provider_adapters.types import RuntimeProvider
from .provider_runtime import EndpointValidator
from .providers import ProviderRegistry
from .service_common import ModelError


class NativeModelFactory:
    """Build the exact native Model selected by Provider type and calling API."""

    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        registry: ProviderRegistry,
        endpoint_policy: EndpointValidator,
    ) -> None:
        self._http_client = http_client
        self._registry = registry
        self._endpoint_policy = endpoint_policy

    async def build(self, snapshot: ModelExecutionSnapshot, provider: RuntimeProvider) -> PydanticModel[Any]:
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
        native_provider = await to_thread.run_sync(
            integration.build_provider,
            provider,
            self._http_client,
            snapshot.model_api,
        )
        try:
            await self._endpoint_policy.validate(str(native_provider.base_url), resolve_dns=True)
            profile: ModelProfileSpec | None = None
            reference_name = profile_reference(snapshot.base_model, snapshot.model_api)
            if reference_name is not None and reference_name != snapshot.upstream_model:
                profile = binding.build(reference_name, native_provider).profile
            return binding.build(snapshot.upstream_model, native_provider, profile=profile)
        except BaseException as error:
            with CancelScope(shield=True):
                await native_provider.__aexit__(type(error), error, error.__traceback__)
            if isinstance(error, EndpointPolicyError):
                raise ModelResolutionError(
                    "The current Model Provider endpoint is unavailable.", code="model_provider_unavailable"
                ) from error
            raise
