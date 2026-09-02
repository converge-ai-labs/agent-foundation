"""Run-fresh native Model resolution for pinned Agent UI recipes."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, cast

from a13n_harness import AgentContext, infer_model
from a13n_harness.errors import ModelResolutionError
from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.providers import Provider, infer_provider, infer_provider_class

from a13n_ui.composition.models import ResolvedModelRecipe

_PROVIDER_ALIASES = {
    "gemini": "google-cloud",
    "google-gla": "google-cloud",
    "google-vertex": "google-cloud",
    "openai": "openai-responses",
}


class AgentUiModelResolver:
    """Resolve only logical IDs pinned by one reconstructed Agent snapshot."""

    def __init__(self, recipes: Mapping[str, ResolvedModelRecipe]) -> None:
        copied = {key: value.model_copy(deep=True) for key, value in recipes.items()}
        self._recipes = MappingProxyType(copied)

    def fresh(self) -> AgentUiModelResolver:
        """Create one detached resolver for an independent Harness invocation."""

        return AgentUiModelResolver(self._recipes)

    async def __call__(
        self,
        context: ModelResolutionContext[AgentContext],
        model_id: str,
    ) -> Model:
        del context
        recipe = self._recipes.get(model_id)
        if recipe is None:
            raise ModelResolutionError(
                "The requested Agent UI Model recipe is not pinned by this snapshot.",
                code="model_recipe_missing",
                details={"model_id": model_id},
            )
        api_key: str | None = None
        if recipe.api_key is not None:
            api_key = os.environ.get(recipe.api_key.env)
            if not api_key:
                raise ModelResolutionError(
                    "The required Agent UI Model credential source is unavailable.",
                    code="model_credential_missing",
                    details={"environment_variable": recipe.api_key.env},
                )
        provider_name = _PROVIDER_ALIASES.get(recipe.route.partition(":")[0], recipe.route.partition(":")[0])

        def provider_factory(requested_provider: str) -> Provider[Any]:
            if requested_provider != provider_name:
                raise ModelResolutionError(
                    "The native Model requested a Provider outside its pinned recipe.",
                    code="model_provider_mismatch",
                    details={"provider": requested_provider},
                )
            if api_key is None:
                return infer_provider(requested_provider)
            provider_type = infer_provider_class(requested_provider)
            constructor = cast(Callable[..., Provider[Any]], provider_type)
            try:
                return constructor(api_key=api_key)
            except Exception as exc:
                raise ModelResolutionError(
                    "The selected Model Provider could not be constructed from its pinned credential source.",
                    code="model_provider_invalid",
                    details={"provider": requested_provider},
                ) from exc

        try:
            return infer_model(recipe.route, provider_factory=provider_factory)
        except ModelResolutionError:
            raise
        except Exception as exc:
            raise ModelResolutionError(
                "The pinned Agent UI Model recipe could not be reconstructed.",
                code="model_reconstruction_failed",
                details={"model_id": model_id},
            ) from exc


def model_recipe_id(recipe: ResolvedModelRecipe) -> str:
    """Return a concise deterministic logical ID for one complete recipe."""

    encoded = json.dumps(
        recipe.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"agent-ui:model-{hashlib.sha256(encoded).hexdigest()[:24]}"


__all__ = ["AgentUiModelResolver", "model_recipe_id"]
