"""Build Pydantic AI models from trusted per-Provider adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx2
from a13n_harness.errors import ModelResolutionError
from pydantic_ai.models import Model as PydanticModel

from .domain import ModelExecutionSnapshot
from .provider_adapters.base import ProviderAdapter
from .provider_adapters.registry import BUILT_IN_PROVIDER_ADAPTERS
from .provider_adapters.types import RuntimeProvider


class NativeModelFactory:
    """Select the trusted adapter for one configured Model Provider."""

    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        *,
        adapters: Mapping[str, ProviderAdapter] | None = None,
    ) -> None:
        self._http_client = http_client
        self._adapters = BUILT_IN_PROVIDER_ADAPTERS if adapters is None else adapters

    def build(self, snapshot: ModelExecutionSnapshot, provider: RuntimeProvider) -> PydanticModel[Any]:
        try:
            adapter = self._adapters[provider.type]
        except KeyError as error:
            raise ModelResolutionError(
                "The Model Provider is unavailable.",
                code="model_provider_unavailable",
            ) from error
        return adapter.build_model(snapshot, provider, self._http_client)
