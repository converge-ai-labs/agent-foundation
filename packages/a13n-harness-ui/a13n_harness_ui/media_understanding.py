"""Lazy file-media inference using captured Models and Host credential sources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from a13n_harness import ImageInputPolicy
from a13n_harness.errors import ModelResolutionError
from a13n_harness.metering import ModelUsageBinding
from a13n_harness.toolsets.file_media import (
    AgentMediaUnderstandingProvider,
    MediaUnderstandingError,
    MediaUnderstandingRequest,
    MediaUnderstandingResult,
    NativeInputMediaKind,
)
from a13n_harness.toolsets.file_media import (
    environment_media_kinds as environment_media_kinds,
)
from pydantic_ai.settings import ModelSettings

from a13n_harness_ui.model_runtime import HarnessUiModelResolver, model_recipe_id

if TYPE_CHECKING:
    from a13n_harness_ui.composition.models import ResolvedModelRecipe


class FileMediaUnderstanding:
    """Prefer configured Models per media kind; retain the Harness environment fallback."""

    def __init__(
        self,
        models: Mapping[NativeInputMediaKind, ResolvedModelRecipe],
        resolver: HarnessUiModelResolver,
        *,
        thread_id: str,
    ) -> None:
        self._models = {kind: recipe.model_copy(deep=True) for kind, recipe in models.items()}
        self._resolver = resolver
        self._thread_id = thread_id

    def supports(self, kind: NativeInputMediaKind) -> bool:
        return kind in self._models or kind in environment_media_kinds()

    async def understand(
        self, request: MediaUnderstandingRequest, *, usage: ModelUsageBinding | None = None
    ) -> MediaUnderstandingResult:
        recipe = self._models.get(request.kind)
        if recipe is None:
            provider = AgentMediaUnderstandingProvider.from_environment(kind=request.kind)
            if provider is None:
                raise MediaUnderstandingError("media_understanding_unavailable")
        else:
            try:
                model = await self._resolver.resolve(model_recipe_id(recipe), thread_id=self._thread_id)
                provider = AgentMediaUnderstandingProvider(
                    models={request.kind: model},
                    model_settings={request.kind: cast(ModelSettings, dict(recipe.settings))},
                    image_input=(
                        recipe.model_characteristics.image_input
                        if recipe.model_characteristics is not None
                        else ImageInputPolicy()
                    ),
                )
            except (ModelResolutionError, TypeError, ValueError) as exc:
                # Do not substitute an ambient Model after a configured Model fails.
                raise MediaUnderstandingError("media_understanding_configuration_invalid") from exc
        return await provider.understand(request, usage=usage)
